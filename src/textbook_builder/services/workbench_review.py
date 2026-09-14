from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from ..contracts import DraftKnowledgeItemDTO, FormalGraphWorkbookDTO
from ..exporters import FormalGraphWorkbookBuilder, XlsxWorkbookExporter
from ..mapping import LayerMappingBuilder
from ..progress_recording import PROGRESS_MODE_FULL, write_progress_artifact
from ..quality import (
    GraphQualityChecker,
    GraphQualityReport,
    ReviewDocumentQualityChecker,
    ReviewDocumentQualityReport,
)
from ..utils.hierarchy import synchronize_draft_hierarchy
from ..utils.relation_endpoints import normalize_draft_relation_endpoints
from ..utils.review_status import normalize_review_status
from ..geometry import RenderMetricsProfile, SceneGeometry, layout_scene
from ..review_document import ExportProfileDTO, P2ReviewDocumentDTO, ReviewDocumentMigrator, document_to_legacy_drafts
from ..review_document_io import ReviewDocumentExporter
from ..review_views.projection import ReviewProjectionBuilder, ReviewViewProjection
from ..review_views.projection import VIEW_MODE_RELATIONS
from .publish_planning import PublishPlan, PublishPlanningService, PublishResult
from .staged_extraction import StagedExtractionOutcome


@dataclass(slots=True)
class WorkbenchExportResult:
    output_dir: Path
    draft_path: Path
    quality_path: Path
    manifest_path: Path
    quality_report: GraphQualityReport
    formal_path: Path | None = None
    layer_mapping_path: Path | None = None

    @property
    def formal_generated(self) -> bool:
        return self.formal_path is not None

    @property
    def layer_mapping_generated(self) -> bool:
        return self.layer_mapping_path is not None


@dataclass(slots=True)
class ReviewRenderBundle:
    projection: ReviewViewProjection
    geometry: SceneGeometry


@dataclass(slots=True)
class ReviewDocumentSaveResult:
    path: Path
    base_export_id: str
    revision: int


class WorkbenchReviewService:
    """Headless review reconstruction, quality gates, and formal export orchestration."""

    def __init__(
        self,
        *,
        workbook_builder: FormalGraphWorkbookBuilder | None = None,
        exporter: XlsxWorkbookExporter | None = None,
        quality_checker: GraphQualityChecker | None = None,
        layer_mapping_builder: LayerMappingBuilder | None = None,
    ) -> None:
        self._workbook_builder = workbook_builder or FormalGraphWorkbookBuilder()
        self._exporter = exporter or XlsxWorkbookExporter()
        self._quality_checker = quality_checker or GraphQualityChecker()
        self._layer_mapping_builder = layer_mapping_builder or LayerMappingBuilder()
        self._document_exporter = ReviewDocumentExporter(workbook_exporter=self._exporter)
        self._projection_builder = ReviewProjectionBuilder()
        self._document_quality_checker = ReviewDocumentQualityChecker()
        self._publish_planner = PublishPlanningService(
            exporter=self._exporter,
            layer_mapping_builder=self._layer_mapping_builder,
        )

    def build_review_workbook(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        *,
        source_id: str,
    ) -> FormalGraphWorkbookDTO:
        drafts = deepcopy(drafts)
        normalize_draft_relation_endpoints(drafts)
        synchronize_draft_hierarchy(drafts)
        return self._workbook_builder.build_review_workbook(
            drafts,
            graph_id=f"REVIEW_{source_id}",
            textbook_version="desktop_workbench_v1",
        )

    def check_quality(self, drafts: list[DraftKnowledgeItemDTO]) -> GraphQualityReport:
        drafts = deepcopy(drafts)
        normalize_draft_relation_endpoints(drafts)
        synchronize_draft_hierarchy(drafts)
        return self._quality_checker.check_drafts(drafts)

    def create_review_document(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        *,
        source_identity: str,
        analysis_run_id: str = "",
        staged_outcome: StagedExtractionOutcome | None = None,
    ) -> P2ReviewDocumentDTO:
        occurrence_hints = None
        if staged_outcome is not None:
            occurrence_hints = {
                node.node_id: list(node.occurrences)
                for node in staged_outcome.canonical_nodes
                if node.occurrences
            }
        return ReviewDocumentMigrator().migrate(
            deepcopy(drafts),
            source_identity=source_identity,
            analysis_run_id=analysis_run_id,
            occurrence_hints=occurrence_hints,
        )

    def build_review_render_bundle(
        self,
        document: P2ReviewDocumentDTO,
        *,
        renderer: str,
        focused_scope_id: str = "",
        hidden_view_ids: set[str] | None = None,
        manual_positions: dict[str, dict[str, float]] | None = None,
        view_mode: str = VIEW_MODE_RELATIONS,
    ) -> ReviewRenderBundle:
        projection = self._projection_builder.build(
            document,
            focused_scope_id=focused_scope_id,
            hidden_view_ids=hidden_view_ids,
            view_mode=view_mode,
        )
        geometry = layout_scene(
            projection,
            RenderMetricsProfile(renderer=renderer),
            manual_positions,
        )
        return ReviewRenderBundle(projection, geometry)

    def save_review_document(
        self,
        document: P2ReviewDocumentDTO,
        file_path: str | Path,
    ) -> ReviewDocumentSaveResult:
        snapshot = deepcopy(document)
        base_export_id = self._document_exporter.export_review_document(snapshot, file_path)
        return ReviewDocumentSaveResult(Path(file_path), base_export_id, snapshot.metadata.revision)

    def check_review_document(
        self,
        document: P2ReviewDocumentDTO,
    ) -> ReviewDocumentQualityReport:
        return self._document_quality_checker.check(deepcopy(document))

    def preflight_publish(
        self,
        document: P2ReviewDocumentDTO,
        profile: ExportProfileDTO | None = None,
    ) -> PublishPlan:
        return self._publish_planner.preflight(deepcopy(document), deepcopy(profile))

    def publish(
        self,
        document: P2ReviewDocumentDTO,
        plan: PublishPlan,
        *,
        expected_revision: int,
        output_root: str | Path,
    ) -> PublishResult:
        return self._publish_planner.publish(
            deepcopy(document),
            plan,
            expected_revision=expected_revision,
            output_root=output_root,
        )

    def export_reviewed(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        *,
        source_id: str,
        output_dir: str | Path,
        dated_subdir: bool = False,
        export_date: date | None = None,
        analysis_run_id: str = "",
        progress_recording_dir: str | Path | None = None,
    ) -> WorkbenchExportResult:
        current_date = export_date or date.today()
        output_root = Path(output_dir)
        output = output_root / current_date.isoformat() if dated_subdir else output_root
        output.mkdir(parents=True, exist_ok=True)
        draft_path = output / "reviewed_draft.xlsx"
        formal_candidate = output / "formal_graph.xlsx"
        quality_path = output / "graph_quality_report.md"
        layer_mapping_candidate = output / "layer_mapping.json"
        manifest_path = output / "export_manifest.json"

        for stale_path in (
            draft_path,
            formal_candidate,
            quality_path,
            layer_mapping_candidate,
            manifest_path,
        ):
            stale_path.unlink(missing_ok=True)

        drafts = deepcopy(drafts)
        normalize_draft_relation_endpoints(drafts)
        synchronize_draft_hierarchy(drafts)
        quality_report = self._quality_checker.check_drafts(drafts)
        self._exporter.export_draft(drafts, draft_path)
        quality_path.write_text(quality_report.to_markdown(), encoding="utf-8")

        accepted_count = sum(
            normalize_review_status(draft.review_status) == "accepted" for draft in drafts
        )
        formal_path: Path | None = None
        layer_mapping_path: Path | None = None
        if not quality_report.has_errors and accepted_count:
            formal = self._workbook_builder.build(
                drafts,
                graph_id=f"FORMAL_{source_id or 'LOCAL'}",
                textbook_version="desktop_workbench_v1",
            )
            self._exporter.export_formal(formal, formal_candidate)
            formal_path = formal_candidate
            layer_mapping = self._layer_mapping_builder.build(formal)
            layer_mapping_candidate.write_text(
                json.dumps(layer_mapping.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            layer_mapping_path = layer_mapping_candidate

        if quality_report.has_errors:
            blocked_reason = "quality_errors"
        elif not accepted_count:
            blocked_reason = "no_accepted_nodes"
        else:
            blocked_reason = ""
        generated_files = [draft_path.name, quality_path.name]
        if formal_path is not None:
            generated_files.append(formal_path.name)
        if layer_mapping_path is not None:
            generated_files.append(layer_mapping_path.name)
        generated_files.append(manifest_path.name)
        relation_types: dict[str, int] = {}
        relation_statuses: dict[str, int] = {}
        relation_count = 0
        for draft in drafts:
            for relation in draft.candidate_relations:
                relation_count += 1
                relation_type = str(relation.get("relation_type") or "unknown")
                relation_status = normalize_review_status(
                    str(relation.get("review_status") or "pending")
                )
                relation_types[relation_type] = relation_types.get(relation_type, 0) + 1
                relation_statuses[relation_status] = (
                    relation_statuses.get(relation_status, 0) + 1
                )
        manifest = {
            "schema_version": 2,
            "export_date": current_date.isoformat(),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_id": source_id,
            "analysis_run_id": analysis_run_id,
            "progress_recording_dir": str(progress_recording_dir or ""),
            "review_graph_id": f"REVIEW_{source_id or 'LOCAL'}",
            "formal_graph_id": f"FORMAL_{source_id or 'LOCAL'}" if formal_path else "",
            "formal_generated": formal_path is not None,
            "layer_mapping_generated": layer_mapping_path is not None,
            "blocked_reason": blocked_reason,
            "quality": {
                "errors": quality_report.error_count,
                "warnings": quality_report.warning_count,
                "info": quality_report.info_count,
            },
            "review_state": {
                "node_count": len(drafts),
                "accepted_node_count": accepted_count,
                "relation_count": relation_count,
                "relation_types": relation_types,
                "relation_statuses": relation_statuses,
            },
            "files": generated_files,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            write_progress_artifact(
                progress_recording_dir,
                "07_review/exported_review_state.json",
                drafts,
                detail=PROGRESS_MODE_FULL,
            )
            write_progress_artifact(
                progress_recording_dir,
                "08_export/export_manifest.json",
                manifest,
            )
        except Exception:
            pass

        return WorkbenchExportResult(
            output_dir=output,
            draft_path=draft_path,
            quality_path=quality_path,
            manifest_path=manifest_path,
            quality_report=quality_report,
            formal_path=formal_path,
            layer_mapping_path=layer_mapping_path,
        )

    @staticmethod
    def quality_summary(report: GraphQualityReport) -> str:
        if not report.issues:
            return "图谱质量检查未发现问题。"
        action = (
            "请先修正错误后再导出正式图谱。"
            if report.has_errors
            else "可继续使用正式图谱，并建议后续处理警告项。"
        )
        top_issues = "\n".join(
            f"- [{issue.severity_label}] {issue.message}" for issue in report.issues[:5]
        )
        remaining = len(report.issues) - 5
        if remaining > 0:
            top_issues += f"\n- 另有 {remaining} 个问题，详见质量报告。"
        return f"{report.summary_text()}\n{action}\n{top_issues}"
