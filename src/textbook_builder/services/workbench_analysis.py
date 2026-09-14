from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ..contracts import DraftKnowledgeItemDTO, FormalGraphWorkbookDTO, SourceRecordDTO
from ..exporters import FormalGraphWorkbookBuilder
from ..llm import (
    LlmAssistedExtractor,
    LlmCandidatePayloadParser,
    LlmChapterStructureAdvisor,
    LlmClientConfig,
    OpenAICompatibleChatClient,
)
from ..readers import DocumentReadOptions, PdfPageRange, SourceDocumentReader
from ..pipeline_contracts import (
    ExtractionRunManifestDTO,
    PageContinuityResultDTO,
    PageEvidenceBundleDTO,
)
from ..progress_recording import (
    PROGRESS_MODE_FULL,
    PROGRESS_MODE_OFF,
    NullProgressRecorder,
    ProgressRecorder,
    RecordingChatJsonClient,
    create_progress_recorder,
    normalize_progress_mode,
)
from ..utils.chapter_layout import (
    ChapterLayoutPlan,
    build_chapter_layout_plans,
    should_request_model_advice,
)
from ..utils.env_config import env_bool, env_value
from .page_continuity import PageContinuityChecker, PageContinuityModelReviewer
from .page_evidence import PageEvidenceCache, PageEvidenceService, PaddleOcrPageAnalyzer
from .staged_extraction import StagedExtractionOutcome, StagedTextbookExtractionService


MODEL_MODE_LOCAL = "本地模拟"
MODEL_MODE_REMOTE = "真实大模型"

ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
ClientFactory = Callable[[LlmClientConfig], OpenAICompatibleChatClient]


class WorkbenchAnalysisCanceled(RuntimeError):
    """Raised when a workbench analysis request is canceled at a checkpoint."""


@dataclass(slots=True)
class WorkbenchAnalysisRequest:
    source_path: Path
    source_format: str
    options: DocumentReadOptions
    start_page: int = 1
    end_page: int = 1
    scope_label: str = "manual_scope"
    model_mode: str = MODEL_MODE_LOCAL
    api_key: str = ""
    model_name: str = ""
    base_url: str = ""
    dotenv_values: dict[str, str] = field(default_factory=dict)
    ocr_enabled: bool = True
    force_ocr: bool = False
    page_order_check_enabled: bool = False
    page_order_model_review_enabled: bool = True
    staged_pipeline_enabled: bool = False
    relation_completion_enabled: bool = True
    progress_recording_mode: str = PROGRESS_MODE_OFF


@dataclass(slots=True)
class WorkbenchAnalysisResult:
    record: SourceRecordDTO
    drafts: list[DraftKnowledgeItemDTO]
    workbook: FormalGraphWorkbookDTO
    layout_plans: dict[str, ChapterLayoutPlan]
    layout_recommendations: dict[str, dict[str, object]] = field(default_factory=dict)
    page_evidence: list[PageEvidenceBundleDTO] = field(default_factory=list)
    continuity_results: list[PageContinuityResultDTO] = field(default_factory=list)
    staged_outcome: StagedExtractionOutcome | None = None
    run_manifest: ExtractionRunManifestDTO | None = None
    analysis_run_id: str = ""
    progress_recording_dir: str = ""
    progress_recording_mode: str = PROGRESS_MODE_OFF


class WorkbenchAnalysisService:
    """Headless orchestration for source reading, candidate analysis, and review setup."""

    def __init__(
        self,
        *,
        render_dir: str | Path,
        source_reader: SourceDocumentReader | None = None,
        payload_parser: LlmCandidatePayloadParser | None = None,
        workbook_builder: FormalGraphWorkbookBuilder | None = None,
        client_factory: ClientFactory | None = None,
        page_evidence_service: PageEvidenceService | None = None,
        ocr_settings: dict[str, str] | None = None,
        progress_root: str | Path | None = None,
    ) -> None:
        self._render_dir = Path(render_dir)
        self._source_reader = source_reader or SourceDocumentReader()
        self._payload_parser = payload_parser or LlmCandidatePayloadParser()
        self._workbook_builder = workbook_builder or FormalGraphWorkbookBuilder()
        self._client_factory = client_factory or OpenAICompatibleChatClient
        self._progress_root = Path(progress_root) if progress_root else (
            Path(__file__).resolve().parents[3] / "progress_recordings"
        )
        cache = PageEvidenceCache(self._render_dir / ".page_evidence_cache")
        analyzer = PaddleOcrPageAnalyzer(cache=cache, settings=ocr_settings)
        self._page_evidence_service = page_evidence_service or PageEvidenceService(
            analyzer=analyzer,
            render_root=self._render_dir,
        )

    def read_source(
        self,
        source_path: str | Path,
        options: DocumentReadOptions,
    ) -> SourceRecordDTO:
        records = self._source_reader.read(source_path, options)
        if not records:
            raise ValueError("教材来源未生成可分析记录。")
        return records[0]

    def analyze(
        self,
        request: WorkbenchAnalysisRequest,
        *,
        on_progress: ProgressCallback | None = None,
        is_canceled: CancelCallback | None = None,
    ) -> WorkbenchAnalysisResult:
        recorder = self._create_recorder(request.progress_recording_mode)
        self._safe_record_json(
            recorder,
            "00_run/analysis_request.json",
            request,
        )
        try:
            result = self._analyze(
                request,
                on_progress=on_progress,
                is_canceled=is_canceled,
                recorder=recorder,
            )
        except WorkbenchAnalysisCanceled:
            self._safe_finish(recorder, "canceled", {"reason": "user_requested"})
            raise
        except Exception as exc:
            self._safe_record_event(
                recorder,
                "run",
                "analysis_failed",
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                level="error",
            )
            self._safe_finish(
                recorder,
                "failed",
                {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        result.analysis_run_id = recorder.run_id or (
            result.run_manifest.run_id if result.run_manifest is not None else ""
        )
        result.progress_recording_dir = str(recorder.run_dir or "")
        result.progress_recording_mode = recorder.mode
        summary = self._result_summary(result)
        self._safe_record_json(recorder, "00_run/analysis_result_summary.json", summary)
        self._safe_finish(recorder, "completed", summary)
        return result

    def _analyze(
        self,
        request: WorkbenchAnalysisRequest,
        *,
        on_progress: ProgressCallback | None = None,
        is_canceled: CancelCallback | None = None,
        recorder: ProgressRecorder,
    ) -> WorkbenchAnalysisResult:
        if request.end_page < request.start_page:
            raise ValueError("结束页不能小于起始页。")

        self._progress(on_progress, 5, "正在准备分析参数...")
        self._cancel_if_requested(is_canceled)

        options = replace(request.options)
        if request.source_format == "pdf":
            options.pdf_page_ranges = [
                PdfPageRange(
                    start_page=request.start_page,
                    end_page=request.end_page,
                    label=request.scope_label or "manual_scope",
                )
            ]

        self._progress(on_progress, 20, "正在读取教材来源记录...")
        record = self.read_source(request.source_path, options)
        self._safe_record_json(recorder, "01_input/source_record.json", record)
        self._safe_record_event(
            recorder,
            "01_input",
            "source_loaded",
            {
                "source_id": record.source_id,
                "source_format": record.source_format,
                "selected_pages": record.source_metadata.get("selected_pages", []),
            },
        )
        self._cancel_if_requested(is_canceled)

        page_evidence: list[PageEvidenceBundleDTO] = []
        continuity_results: list[PageContinuityResultDTO] = []
        staged_outcome: StagedExtractionOutcome | None = None
        run_manifest: ExtractionRunManifestDTO | None = None

        if request.model_mode == MODEL_MODE_REMOTE and request.staged_pipeline_enabled:
            if not request.api_key:
                raise ValueError(
                    "未配置真实大模型 API Key。请在界面中填写，或在项目根目录 .env 中配置 "
                    "TEXTBOOK_BUILDER_LLM_API_KEY。"
                )
            self._progress(on_progress, 35, "正在建立逐页文本与视觉证据缓存...")
            page_evidence = self._build_page_evidence(request=request, record=record)
            self._record_page_evidence(recorder, page_evidence)
            self._cancel_if_requested(is_canceled)
            client = self._model_client(request, recorder)
            if request.page_order_check_enabled:
                self._progress(on_progress, 43, "正在检查当前页面顺序的连续性...")
                continuity_results = PageContinuityChecker().check(page_evidence)
                if request.page_order_model_review_enabled and any(
                    item.needs_model_review for item in continuity_results
                ):
                    continuity_results = PageContinuityModelReviewer(client).review(
                        continuity_results,
                        page_evidence,
                    )
                self._safe_record_json(
                    recorder,
                    "02_page_evidence/continuity_results.json",
                    continuity_results,
                )
            self._cancel_if_requested(is_canceled)
            self._progress(on_progress, 52, "正在首轮抽取独立节点与局部关系线索...")
            staged_outcome = StagedTextbookExtractionService(client).extract(
                record=record,
                pages=page_evidence,
                run_relation_completion=request.relation_completion_enabled,
            )
            self._record_staged_outcome(recorder, staged_outcome)
            drafts = staged_outcome.drafts
            run_manifest = self._run_manifest(
                request=request,
                pages=page_evidence,
                outcome=staged_outcome,
                continuity_results=continuity_results,
                run_id=recorder.run_id,
            )
            self._safe_record_json(recorder, "00_run/run_manifest.json", run_manifest)
            self._progress(on_progress, 80, "节点归一化与分层关系整合已完成...")
        else:
            if request.page_order_check_enabled:
                self._progress(on_progress, 35, "正在建立逐页证据并检查当前页面顺序...")
                page_evidence = self._build_page_evidence(request=request, record=record)
                self._record_page_evidence(recorder, page_evidence)
                continuity_results = PageContinuityChecker().check(page_evidence)
                self._safe_record_json(
                    recorder,
                    "02_page_evidence/continuity_results.json",
                    continuity_results,
                )
            self._progress(on_progress, 45, "正在准备模型输入...")
            payload = self._candidate_payload(
                request=request,
                record=record,
                recorder=recorder,
            )
            self._cancel_if_requested(is_canceled)

            self._progress(on_progress, 80, "正在解析模型返回的候选结构...")
            drafts = self._payload_parser.parse(
                payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
                record=record,
            )
            self._safe_record_json(recorder, "03_first_pass/parsed_drafts.json", drafts)
        self._cancel_if_requested(is_canceled)

        self._progress(on_progress, 92, "正在构建候选图谱与审查工作底稿...")
        workbook = self._workbook_builder.build_review_workbook(
            drafts,
            graph_id=f"REVIEW_{record.source_id}",
            textbook_version="desktop_workbench_v1",
        )
        self._safe_record_json(
            recorder,
            "07_review/initial_review_workbook.json",
            workbook,
            detail=PROGRESS_MODE_FULL,
        )
        self._safe_record_json(
            recorder,
            "07_review/initial_review_summary.json",
            self._workbook_summary(workbook),
        )
        layout_plans = build_chapter_layout_plans(workbook.nodes, workbook.edges)
        layout_recommendations: dict[str, dict[str, object]] = {}
        if request.model_mode == MODEL_MODE_REMOTE and any(
            should_request_model_advice(plan) for plan in layout_plans.values()
        ):
            self._progress(on_progress, 96, "正在生成章节内部结构建议...")
            layout_recommendations = self._request_structure_advice(
                request=request,
                record=record,
                workbook=workbook,
                layout_plans=layout_plans,
                recorder=recorder,
            )
            self._cancel_if_requested(is_canceled)
            if layout_recommendations:
                layout_plans = build_chapter_layout_plans(
                    workbook.nodes,
                    workbook.edges,
                    recommendations=layout_recommendations,
                )
        self._safe_record_json(
            recorder,
            "06_layout/layout_recommendations.json",
            layout_recommendations,
        )
        self._safe_record_json(
            recorder,
            "06_layout/final_layout_plans.json",
            layout_plans,
        )

        self._progress(on_progress, 100, "分析完成，正在回填到工作台...")
        return WorkbenchAnalysisResult(
            record=record,
            drafts=drafts,
            workbook=workbook,
            layout_plans=layout_plans,
            layout_recommendations=layout_recommendations,
            page_evidence=page_evidence,
            continuity_results=continuity_results,
            staged_outcome=staged_outcome,
            run_manifest=run_manifest,
        )

    def _build_page_evidence(
        self,
        *,
        request: WorkbenchAnalysisRequest,
        record: SourceRecordDTO,
    ) -> list[PageEvidenceBundleDTO]:
        selected_pages: list[int] | None = None
        if record.source_format == "pdf":
            raw_pages = record.source_metadata.get("selected_pages", [])
            if isinstance(raw_pages, list):
                selected_pages = [int(page) for page in raw_pages if str(page).isdigit()]
            if not selected_pages:
                selected_pages = list(range(request.start_page, request.end_page + 1))
        return list(
            self._page_evidence_service.build(
                request.source_path,
                source_id=record.source_id,
                selected_pages=selected_pages,
                ocr_enabled=request.ocr_enabled,
                force_ocr=request.force_ocr,
            )
        )

    @staticmethod
    def _run_manifest(
        *,
        request: WorkbenchAnalysisRequest,
        pages: list[PageEvidenceBundleDTO],
        outcome: StagedExtractionOutcome,
        continuity_results: list[PageContinuityResultDTO],
        run_id: str = "",
    ) -> ExtractionRunManifestDTO:
        digest = hashlib.sha256()
        with request.source_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        notes = list(outcome.warnings)
        suspicious = sum(item.needs_model_review for item in continuity_results)
        if suspicious:
            notes.append(f"页面连续性检查有 {suspicious} 组相邻页需要人工确认；系统未自动重排。")
        notes.append(f"完成时间：{datetime.now(timezone.utc).isoformat()}")
        return ExtractionRunManifestDTO(
            run_id=run_id or f"P2-{uuid4().hex}",
            source_path=str(request.source_path),
            source_sha256=digest.hexdigest(),
            pipeline_version="staged_ocr_graph_v1",
            ocr_enabled=request.ocr_enabled,
            page_order_check_enabled=request.page_order_check_enabled,
            model_mode=request.model_mode,
            page_count=len(pages),
            ocr_cache_hits=sum(page.cache_hit for page in pages),
            image_model_calls=outcome.image_model_calls,
            text_model_calls=outcome.text_model_calls,
            model_input_characters=outcome.model_input_characters,
            status="completed",
            notes=notes,
        )

    def _candidate_payload(
        self,
        *,
        request: WorkbenchAnalysisRequest,
        record: SourceRecordDTO,
        recorder: ProgressRecorder,
    ) -> str | dict[str, object]:
        if request.model_mode == MODEL_MODE_LOCAL:
            return self._offline_candidate_payload(record)
        if not request.api_key:
            raise ValueError(
                "未配置真实大模型 API Key。请在界面中填写，或在项目根目录 .env 中配置 "
                "TEXTBOOK_BUILDER_LLM_API_KEY。"
            )

        client = self._model_client(request, recorder)
        image_paths = self._analysis_image_paths(
            source_path=request.source_path,
            record=record,
            fallback_page=request.start_page,
        )
        system_prompt = LlmAssistedExtractor._system_prompt()
        user_prompt = LlmAssistedExtractor._user_prompt(record)
        if image_paths:
            return client.complete_json_with_images(
                system_prompt=system_prompt,
                user_prompt=(
                    user_prompt
                    + "\n\n请重点依据随消息附带的教材页面图片或导入图片进行分析。"
                    "不要把 OCR 当成全量结构化任务，只输出适合教师审查的候选知识点与关系。"
                ),
                image_paths=image_paths,
            )
        return client.complete_json(system_prompt=system_prompt, user_prompt=user_prompt)

    def _request_structure_advice(
        self,
        *,
        request: WorkbenchAnalysisRequest,
        record: SourceRecordDTO,
        workbook: FormalGraphWorkbookDTO,
        layout_plans: dict[str, ChapterLayoutPlan],
        recorder: ProgressRecorder,
    ) -> dict[str, dict[str, object]]:
        client = self._model_client(request, recorder)
        return LlmChapterStructureAdvisor(client).advise(
            record=record,
            workbook=workbook,
            local_plans=layout_plans,
        )

    def _model_client(
        self,
        request: WorkbenchAnalysisRequest,
        recorder: ProgressRecorder,
    ) -> object:
        config = self._client_config(request)
        client = self._client_factory(config)
        if not recorder.enabled:
            return client
        return RecordingChatJsonClient(
            client,
            recorder,
            model_metadata={
                "model": config.model,
                "base_url": config.base_url,
                "temperature": config.temperature,
                "extra_body": config.extra_body,
            },
        )

    @staticmethod
    def _client_config(request: WorkbenchAnalysisRequest) -> LlmClientConfig:
        model = request.model_name or env_value(
            "TEXTBOOK_BUILDER_LLM_MODEL",
            dotenv_values=request.dotenv_values,
            default="qwen3.6-plus",
        )
        base_url = request.base_url or env_value(
            "TEXTBOOK_BUILDER_LLM_BASE_URL",
            dotenv_values=request.dotenv_values,
            default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        enable_thinking = env_bool(
            env_value(
                "TEXTBOOK_BUILDER_LLM_ENABLE_THINKING",
                dotenv_values=request.dotenv_values,
                default="false",
            )
        )
        return LlmClientConfig(
            model=model,
            base_url=base_url,
            api_key=request.api_key,
            extra_body={"enable_thinking": enable_thinking},
        )

    def _analysis_image_paths(
        self,
        *,
        source_path: Path,
        record: SourceRecordDTO,
        fallback_page: int,
    ) -> list[Path]:
        if record.source_format in {"png", "jpg", "jpeg"}:
            return [source_path]
        if record.source_format != "pdf":
            return []
        selected_pages = record.source_metadata.get("selected_pages", [])
        if not isinstance(selected_pages, list) or not selected_pages:
            selected_pages = [fallback_page]
        normalized_pages = [int(page) for page in selected_pages if str(page).isdigit()]
        return self._render_pdf_pages(source_path, normalized_pages or [fallback_page])

    def _render_pdf_pages(self, source_path: Path, selected_pages: list[int]) -> list[Path]:
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz
        except ImportError:
            return []

        self._render_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[Path] = []
        with fitz.open(source_path) as document:
            for page_number in selected_pages[:4]:
                page_index = int(page_number) - 1
                if page_index < 0 or page_index >= document.page_count:
                    continue
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                image_path = self._render_dir / f"{source_path.stem}_p{page_number}.png"
                pixmap.save(str(image_path))
                rendered.append(image_path)
        return rendered

    def _create_recorder(self, mode: str) -> ProgressRecorder:
        try:
            return create_progress_recorder(
                self._progress_root,
                mode=normalize_progress_mode(mode),
            )
        except Exception:
            return NullProgressRecorder()

    @staticmethod
    def _safe_record_json(
        recorder: ProgressRecorder,
        relative_path: str,
        payload: object,
        *,
        detail: str = "summary",
    ) -> None:
        try:
            recorder.record_json(relative_path, payload, detail=detail)
        except Exception:
            return None

    @staticmethod
    def _safe_record_event(
        recorder: ProgressRecorder,
        stage: str,
        event: str,
        payload: object | None = None,
        *,
        level: str = "info",
    ) -> None:
        try:
            recorder.record_event(stage, event, payload, level=level)
        except Exception:
            return None

    @staticmethod
    def _safe_finish(
        recorder: ProgressRecorder,
        status: str,
        summary: object | None = None,
    ) -> None:
        try:
            recorder.finish(status, summary)
        except Exception:
            return None

    def _record_page_evidence(
        self,
        recorder: ProgressRecorder,
        pages: list[PageEvidenceBundleDTO],
    ) -> None:
        summary = {
            "page_count": len(pages),
            "cache_hits": sum(page.cache_hit for page in pages),
            "pages": [
                {
                    "page_id": page.page_id,
                    "page_index": page.page_index,
                    "ocr_status": page.ocr_status,
                    "ocr_block_count": len(page.ocr_blocks),
                    "combined_text_characters": len(page.combined_text()),
                    "cache_hit": page.cache_hit,
                    "image_path": page.image_path,
                }
                for page in pages
            ],
        }
        self._safe_record_json(
            recorder,
            "02_page_evidence/page_evidence_summary.json",
            summary,
        )
        self._safe_record_json(
            recorder,
            "02_page_evidence/page_evidence_full.json",
            pages,
            detail=PROGRESS_MODE_FULL,
        )

    def _record_staged_outcome(
        self,
        recorder: ProgressRecorder,
        outcome: StagedExtractionOutcome,
    ) -> None:
        artifacts = {
            "01_input/book_manifest.json": outcome.book_manifest,
            "03_first_pass/local_nodes.json": outcome.local_nodes,
            "03_first_pass/local_relation_claims.json": outcome.local_relation_claims,
            "04_node_normalization/canonical_nodes.json": outcome.canonical_nodes,
            "04_node_normalization/alias_mappings.json": outcome.alias_mappings,
            "05_relation_completion/relation_candidates.json": outcome.relation_candidates,
            "05_relation_completion/unresolved_relation_claims.json": (
                outcome.unresolved_relation_claims
            ),
            "05_relation_completion/group_parse_diagnostics.json": (
                outcome.relation_completion_diagnostics
            ),
            "07_review/initial_drafts.json": outcome.drafts,
        }
        for relative_path, payload in artifacts.items():
            self._safe_record_json(
                recorder,
                relative_path,
                payload,
                detail=PROGRESS_MODE_FULL,
            )
        relation_types: dict[str, int] = {}
        relation_statuses: dict[str, int] = {}
        relation_sources: dict[str, int] = {}
        for relation in outcome.relation_candidates:
            relation_types[relation.relation_type] = (
                relation_types.get(relation.relation_type, 0) + 1
            )
            relation_statuses[relation.review_status] = (
                relation_statuses.get(relation.review_status, 0) + 1
            )
            relation_sources[relation.relation_source] = (
                relation_sources.get(relation.relation_source, 0) + 1
            )
        self._safe_record_json(
            recorder,
            "05_relation_completion/relation_summary.json",
            {
                "local_node_count": len(outcome.local_nodes),
                "local_relation_claim_count": len(outcome.local_relation_claims),
                "canonical_node_count": len(outcome.canonical_nodes),
                "alias_mapping_count": len(outcome.alias_mappings),
                "relation_candidate_count": len(outcome.relation_candidates),
                "unresolved_relation_claim_count": len(outcome.unresolved_relation_claims),
                "relation_completion_groups": outcome.relation_completion_diagnostics,
                "relation_types": relation_types,
                "relation_statuses": relation_statuses,
                "relation_sources": relation_sources,
                "warnings": outcome.warnings,
                "image_model_calls": outcome.image_model_calls,
                "text_model_calls": outcome.text_model_calls,
                "model_input_characters": outcome.model_input_characters,
            },
        )

    @staticmethod
    def _workbook_summary(workbook: FormalGraphWorkbookDTO) -> dict[str, object]:
        relation_types: dict[str, int] = {}
        relation_statuses: dict[str, int] = {}
        for edge in workbook.edges:
            relation_type = str(getattr(edge, "relation_type", "") or "unknown")
            review_status = str(getattr(edge, "review_status", "") or "unknown")
            relation_types[relation_type] = relation_types.get(relation_type, 0) + 1
            relation_statuses[review_status] = relation_statuses.get(review_status, 0) + 1
        return {
            "node_count": len(workbook.nodes),
            "edge_count": len(workbook.edges),
            "relation_types": relation_types,
            "relation_statuses": relation_statuses,
        }

    @classmethod
    def _result_summary(cls, result: WorkbenchAnalysisResult) -> dict[str, object]:
        summary = cls._workbook_summary(result.workbook)
        summary.update(
            {
                "analysis_run_id": result.analysis_run_id,
                "page_evidence_count": len(result.page_evidence),
                "continuity_result_count": len(result.continuity_results),
                "layout_plan_count": len(result.layout_plans),
                "layout_recommendation_count": len(result.layout_recommendations),
                "staged_pipeline_used": result.staged_outcome is not None,
            }
        )
        return summary

    @staticmethod
    def _offline_candidate_payload(record: SourceRecordDTO) -> dict[str, object]:
        source_location = "image=1" if record.source_format in {"png", "jpg", "jpeg"} else "page=selected"
        selected_pages = record.source_metadata.get("selected_pages")
        if isinstance(selected_pages, list) and selected_pages:
            source_location = f"page={selected_pages[0]}-{selected_pages[-1]}"
        subject = record.subject
        grade = record.grade
        term = record.term
        return {
            "chapters": [
                {
                    "chapter": "ch_local",
                    "title": "本地导入材料",
                    "knowledge_points": [
                        {
                            "candidate_display_name": "材料中的核心概念",
                            "candidate_node_name": "core_concept",
                            "knowledge_type": "concept",
                            "cognitive_level": "understand",
                            "source_text": record.raw_text
                            or "由教师导入的局部教材材料，需要结合页面内容确认核心概念。",
                            "source_location": source_location,
                            "confidence": 0.72,
                            "reasoning_summary": "当前使用本地模拟候选生成，用于验证教师审查闭环；后续可替换为真实大模型输出。",
                            "review_status": "pending",
                        },
                        {
                            "candidate_display_name": "核心概念的应用关系",
                            "candidate_node_name": "core_concept_application",
                            "knowledge_type": "property",
                            "cognitive_level": "apply",
                            "source_text": record.raw_text or "请教师根据导入材料页面确认该候选关系是否成立。",
                            "source_location": source_location,
                            "confidence": 0.68,
                            "reasoning_summary": "该候选用于演示从概念到应用或性质的关系审查。",
                            "review_status": "pending",
                            "candidate_relations": [
                                {
                                    "source_node_id": f"{subject}_{grade}_{term}_ch_local_core_concept",
                                    "target_node_id": (
                                        f"{subject}_{grade}_{term}_ch_local_core_concept_application"
                                    ),
                                    "relation_type": "progressive",
                                    "confidence": 0.68,
                                    "relation_evidence": "从核心概念进入应用或性质判断，暂作为递进关系候选。",
                                    "reasoning_summary": "需要教师根据教材页面确认。",
                                    "review_status": "pending",
                                }
                            ],
                        },
                    ],
                }
            ]
        }

    @staticmethod
    def _progress(callback: ProgressCallback | None, value: int, message: str) -> None:
        if callback is not None:
            callback(value, message)

    @staticmethod
    def _cancel_if_requested(callback: CancelCallback | None) -> None:
        if callback is not None and callback():
            raise WorkbenchAnalysisCanceled("教材分析已取消。")
