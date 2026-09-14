from __future__ import annotations

import json
from datetime import date
import sys
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.readers import DocumentReadOptions
from textbook_builder.pipeline_contracts import PageEvidenceBundleDTO
from textbook_builder.services import (
    MODEL_MODE_LOCAL,
    MODEL_MODE_REMOTE,
    WorkbenchAnalysisCanceled,
    WorkbenchAnalysisRequest,
    WorkbenchAnalysisService,
    WorkbenchReviewService,
)


def test_analysis_service_runs_local_candidate_pipeline_without_qt_window() -> None:
    temp_dir = _temp_dir("analysis")
    try:
        pdf_path = temp_dir / "source.pdf"
        _write_minimal_pdf(pdf_path)
        progress: list[int] = []
        result = WorkbenchAnalysisService(render_dir=temp_dir / "rendered").analyze(
            _request(pdf_path),
            on_progress=lambda value, _message: progress.append(value),
        )

        assert result.record.source_id == "SERVICE_TEXTBOOK_p1_p1_service_scope"
        assert len(result.drafts) == 3
        assert len(result.workbook.nodes) == 3
        assert len(result.workbook.edges) == 3
        assert result.layout_plans
        assert progress == [5, 20, 45, 80, 92, 100]
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_analysis_service_honors_cancellation_before_reading_source() -> None:
    temp_dir = _temp_dir("cancel")
    try:
        service = WorkbenchAnalysisService(render_dir=temp_dir / "rendered")
        with pytest.raises(WorkbenchAnalysisCanceled):
            service.analyze(
                _request(temp_dir / "missing.pdf"),
                is_canceled=lambda: True,
            )
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_analysis_service_rejects_remote_mode_without_key_before_network_call() -> None:
    temp_dir = _temp_dir("remote_key")
    try:
        pdf_path = temp_dir / "source.pdf"
        _write_minimal_pdf(pdf_path)
        request = _request(pdf_path)
        request.model_mode = MODEL_MODE_REMOTE
        request.api_key = ""

        with pytest.raises(ValueError, match="API Key"):
            WorkbenchAnalysisService(render_dir=temp_dir / "rendered").analyze(request)
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_analysis_service_accepts_injected_client_for_offline_remote_regression() -> None:
    temp_dir = _temp_dir("remote_fake")
    try:
        image_path = temp_dir / "source.png"
        _write_minimal_png(image_path)
        fake_client = _FakeClient()
        service = WorkbenchAnalysisService(
            render_dir=temp_dir / "rendered",
            client_factory=lambda _config: fake_client,
        )
        request = _request(image_path)
        request.source_format = "png"
        request.model_mode = MODEL_MODE_REMOTE
        request.api_key = "test-placeholder"

        result = service.analyze(request)

        assert len(result.drafts) == 2
        assert result.drafts[1].candidate_display_name == "远程协议候选"
        assert fake_client.image_call_count == 1
        assert fake_client.text_call_count == 0
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_analysis_service_runs_staged_remote_path_with_page_evidence_manifest() -> None:
    temp_dir = _temp_dir("remote_staged")
    try:
        pdf_path = temp_dir / "source.pdf"
        _write_minimal_pdf(pdf_path)
        fake_client = _FakeStagedClient()
        service = WorkbenchAnalysisService(
            render_dir=temp_dir / "rendered",
            client_factory=lambda _config: fake_client,
            page_evidence_service=_FakePageEvidenceService(pdf_path),
        )
        request = _request(pdf_path)
        request.model_mode = MODEL_MODE_REMOTE
        request.api_key = "test-placeholder"
        request.staged_pipeline_enabled = True
        request.page_order_check_enabled = True

        result = service.analyze(request)

        assert len(result.drafts) == 2
        assert len(result.page_evidence) == 2
        assert result.run_manifest is not None
        assert result.run_manifest.text_model_calls == 2
        assert result.run_manifest.image_model_calls == 0
        assert result.staged_outcome is not None
        assert result.staged_outcome.book_manifest.page_count == 2
        assert fake_client.text_call_count == 2
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_review_service_exports_accepted_graph_quality_and_layer_mapping() -> None:
    temp_dir = _temp_dir("export")
    try:
        analysis = _local_analysis(temp_dir)
        _accept_all(analysis.drafts)
        export_result = WorkbenchReviewService().export_reviewed(
            analysis.drafts,
            source_id=analysis.record.source_id,
            output_dir=temp_dir / "output",
            dated_subdir=True,
            export_date=date(2026, 8, 23),
        )

        assert export_result.output_dir == temp_dir / "output" / "2026-08-23"
        assert export_result.draft_path.exists()
        assert export_result.quality_path.exists()
        assert export_result.manifest_path.exists()
        assert export_result.formal_path is not None and export_result.formal_path.exists()
        assert export_result.layer_mapping_path is not None
        layer_mapping = json.loads(export_result.layer_mapping_path.read_text(encoding="utf-8"))
        assert layer_mapping["graph_id"].startswith("FORMAL_")
        assert layer_mapping["summary"]["chapter_node_count"] == 1
        assert layer_mapping["summary"]["knowledge_node_count"] == 2
        manifest = json.loads(export_result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["export_date"] == "2026-08-23"
        assert manifest["formal_generated"] is True
        assert manifest["source_id"] == analysis.record.source_id
        assert set(manifest["files"]) == {
            "reviewed_draft.xlsx",
            "graph_quality_report.md",
            "formal_graph.xlsx",
            "layer_mapping.json",
            "export_manifest.json",
        }
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_review_service_blocks_formal_export_when_quality_has_error() -> None:
    temp_dir = _temp_dir("quality_gate")
    try:
        analysis = _local_analysis(temp_dir)
        _accept_all(analysis.drafts)
        node_id = analysis.drafts[1].candidate_node_id
        analysis.drafts[1].candidate_relations.append(
            {
                "source_node_id": node_id,
                "target_node_id": node_id,
                "relation_type": "prerequisite",
                "review_status": "accepted",
                "relation_evidence": "用于验证自环质量门禁。",
            }
        )

        stale_output = temp_dir / "output" / "2026-08-23"
        stale_output.mkdir(parents=True, exist_ok=True)
        (stale_output / "formal_graph.xlsx").write_text("stale", encoding="utf-8")
        (stale_output / "layer_mapping.json").write_text("stale", encoding="utf-8")

        export_result = WorkbenchReviewService().export_reviewed(
            analysis.drafts,
            source_id=analysis.record.source_id,
            output_dir=temp_dir / "output",
            dated_subdir=True,
            export_date=date(2026, 8, 23),
        )

        assert export_result.quality_report.has_errors is True
        assert any(issue.code == "edge_self_loop" for issue in export_result.quality_report.issues)
        assert export_result.formal_path is None
        assert export_result.layer_mapping_path is None
        assert export_result.draft_path.exists()
        assert export_result.quality_path.exists()
        manifest = json.loads(export_result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["formal_generated"] is False
        assert manifest["blocked_reason"] == "quality_errors"
        assert not (export_result.output_dir / "formal_graph.xlsx").exists()
        assert not (export_result.output_dir / "layer_mapping.json").exists()
    finally:
        rmtree(temp_dir, ignore_errors=True)


def _local_analysis(temp_dir: Path):
    pdf_path = temp_dir / "source.pdf"
    _write_minimal_pdf(pdf_path)
    return WorkbenchAnalysisService(render_dir=temp_dir / "rendered").analyze(
        _request(pdf_path)
    )


def _request(pdf_path: Path) -> WorkbenchAnalysisRequest:
    return WorkbenchAnalysisRequest(
        source_path=pdf_path,
        source_format="pdf",
        options=DocumentReadOptions(
            subject="math",
            grade="g8",
            term="term1",
            source_id="SERVICE_TEXTBOOK",
            source_document_type="electronic_textbook",
            education_stage="junior_middle_school",
            grade_band="g7_g9",
            subject_tags=["math", "geometry"],
        ),
        start_page=1,
        end_page=1,
        scope_label="service_scope",
        model_mode=MODEL_MODE_LOCAL,
    )


def _accept_all(drafts) -> None:
    for draft in drafts:
        draft.review_status = "accepted"
        for relation in draft.candidate_relations:
            relation["review_status"] = "accepted"


def _temp_dir(label: str) -> Path:
    path = PROJECT_ROOT / "storage" / "test_runs" / f"service_{label}_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_minimal_pdf(path: Path) -> None:
    path.write_bytes(
        b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 56 >>
stream
BT /F1 12 Tf 72 96 Td (Service Concept) Tj ET
endstream
endobj
trailer
<< /Root 1 0 R >>
%%EOF
"""
    )


def _write_minimal_png(path: Path) -> None:
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a"
            "0000000d49484452"
            "0000000200000002"
            "0802000000"
            "fdd49a73"
            "0000000049454e44ae426082"
        )
    )


class _FakeClient:
    def __init__(self) -> None:
        self.text_call_count = 0
        self.image_call_count = 0

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.text_call_count += 1
        return self._payload()

    def complete_json_with_images(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[Path],
    ) -> str:
        self.image_call_count += 1
        assert image_paths
        return self._payload()

    @staticmethod
    def _payload() -> str:
        return json.dumps(
            {
                "chapters": [
                    {
                        "chapter": "remote",
                        "title": "远程协议章节",
                        "knowledge_points": [
                            {
                                "candidate_display_name": "远程协议候选",
                                "candidate_node_name": "remote_candidate",
                                "review_status": "pending",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )


class _FakePageEvidenceService:
    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path

    def build(self, *_args, **_kwargs):
        return (
            PageEvidenceBundleDTO(
                page_id="SERVICE_TEXTBOOK_p1_p1_service_scope:page:1",
                page_index=1,
                source_id="SERVICE_TEXTBOOK_p1_p1_service_scope",
                source_path=str(self.source_path),
                image_path="page1.png",
                image_sha256="p1",
                source_text="一次函数的一般形式是y=kx+b。",
                ocr_status="ok",
            ),
            PageEvidenceBundleDTO(
                page_id="SERVICE_TEXTBOOK_p1_p1_service_scope:page:2",
                page_index=2,
                source_id="SERVICE_TEXTBOOK_p1_p1_service_scope",
                source_path=str(self.source_path),
                image_path="page2.png",
                image_sha256="p2",
                source_text="正比例函数是b=0的特殊情况。",
                ocr_status="ok",
            ),
        )


class _FakeStagedClient:
    def __init__(self) -> None:
        self.text_call_count = 0

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.text_call_count += 1
        payload = json.loads(user_prompt)
        if payload["task"] == "extract_independent_nodes_and_local_relation_claims":
            return json.dumps(
                {
                    "nodes": [
                        {
                            "temporary_id": "n1",
                            "display_name": "一次函数",
                            "chapter": "第十四章",
                            "evidence_refs": [
                                "SERVICE_TEXTBOOK_p1_p1_service_scope:page:1"
                            ],
                            "confidence": 0.9,
                        },
                        {
                            "temporary_id": "n2",
                            "display_name": "正比例函数",
                            "chapter": "第十四章",
                            "evidence_refs": [
                                "SERVICE_TEXTBOOK_p1_p1_service_scope:page:2"
                            ],
                            "confidence": 0.88,
                        },
                    ],
                    "local_relation_claims": [],
                },
                ensure_ascii=False,
            )
        node_ids = [item["node_id"] for item in payload["canonical_nodes"]]
        return json.dumps(
            {
                "relations": [
                    {
                        "source_node_id": node_ids[0],
                        "target_node_id": node_ids[1],
                        "relation_type": "contains",
                        "evidence_type": "explicit",
                        "inference_scope": "adjacent_pages",
                        "evidence_refs": [
                            "SERVICE_TEXTBOOK_p1_p1_service_scope:page:1",
                            "SERVICE_TEXTBOOK_p1_p1_service_scope:page:2",
                        ],
                        "evidence_text": "正比例函数是一次函数的特殊情况。",
                        "confidence": 0.9,
                    }
                ]
            },
            ensure_ascii=False,
        )
