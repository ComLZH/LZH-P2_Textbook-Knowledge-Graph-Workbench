from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.progress_recording import (
    PROGRESS_MODE_FULL,
    PROGRESS_MODE_OFF,
    PROGRESS_MODE_SUMMARY,
    RecordingChatJsonClient,
    create_progress_recorder,
)
from textbook_builder.readers import DocumentReadOptions
from textbook_builder.services import (
    MODEL_MODE_LOCAL,
    WorkbenchAnalysisRequest,
    WorkbenchAnalysisService,
    WorkbenchReviewService,
)


class _EchoClient:
    last_call_metadata = {"response_id": "response-test", "total_tokens": 12}

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        return (
            '{"api_key":"response-secret",'
            '"image":"data:image/png;base64,AAAA",'
            '"status":"ok"}'
        )


def test_full_recording_redacts_sensitive_text_and_allocates_unique_model_calls(
    tmp_path: Path,
) -> None:
    recorder = create_progress_recorder(tmp_path, mode=PROGRESS_MODE_FULL)
    first = RecordingChatJsonClient(_EchoClient(), recorder)
    second = RecordingChatJsonClient(_EchoClient(), recorder)

    first.complete_json(
        system_prompt="Authorization: Bearer request-secret",
        user_prompt='{"password":"prompt-secret"}',
    )
    second.complete_json(system_prompt="safe", user_prompt="safe")
    recorder.finish("completed", {"api_key": "summary-secret"})

    assert recorder.run_dir is not None
    call_dirs = sorted((recorder.run_dir / "model_calls").iterdir())
    assert [path.name.split("_")[1] for path in call_dirs] == ["0001", "0002"]
    recorded_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in recorder.run_dir.rglob("*")
        if path.is_file()
    )
    for secret in ["request-secret", "prompt-secret", "response-secret", "summary-secret"]:
        assert secret not in recorded_text
    assert "data:image/png;base64,AAAA" not in recorded_text
    assert "[REDACTED]" in recorded_text
    assert "[REDACTED_DATA_URL]" in recorded_text


def test_summary_and_off_modes_follow_hot_plug_contract(tmp_path: Path) -> None:
    summary = create_progress_recorder(tmp_path / "summary", mode=PROGRESS_MODE_SUMMARY)
    client = RecordingChatJsonClient(_EchoClient(), summary)
    client.complete_json(system_prompt="system", user_prompt="user")
    summary.finish("completed")

    assert summary.run_dir is not None
    result_files = list(summary.run_dir.glob("model_calls/*/result.json"))
    assert len(result_files) == 1
    assert not list(summary.run_dir.glob("model_calls/*/request.json"))
    assert not list(summary.run_dir.glob("model_calls/*/response_raw.txt"))

    off_root = tmp_path / "off"
    off = create_progress_recorder(off_root, mode=PROGRESS_MODE_OFF)
    assert off.enabled is False
    assert off.run_dir is None
    assert not off_root.exists()


def test_analysis_and_export_share_run_id_and_progress_directory(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    _write_minimal_pdf(pdf_path)
    service = WorkbenchAnalysisService(
        render_dir=tmp_path / "rendered",
        progress_root=tmp_path / "progress_recordings",
    )
    request = WorkbenchAnalysisRequest(
        source_path=pdf_path,
        source_format="pdf",
        options=DocumentReadOptions(
            subject="math",
            grade="g8",
            term="term1",
            source_id="TRACE_TEST",
        ),
        model_mode=MODEL_MODE_LOCAL,
        api_key="analysis-secret",
        dotenv_values={"TEXTBOOK_BUILDER_LLM_API_KEY": "dotenv-secret"},
        progress_recording_mode=PROGRESS_MODE_FULL,
    )

    result = service.analyze(request)
    progress_dir = Path(result.progress_recording_dir)

    assert result.analysis_run_id.startswith("P2-")
    assert result.progress_recording_mode == PROGRESS_MODE_FULL
    assert progress_dir.is_dir()
    assert (progress_dir / "00_run" / "analysis_request.json").is_file()
    assert (progress_dir / "07_review" / "initial_review_workbook.json").is_file()
    recorded_request = (progress_dir / "00_run" / "analysis_request.json").read_text(
        encoding="utf-8"
    )
    assert "analysis-secret" not in recorded_request
    assert "dotenv-secret" not in recorded_request

    for draft in result.drafts:
        draft.review_status = "accepted"
        for relation in draft.candidate_relations:
            relation["review_status"] = "accepted"
    exported = WorkbenchReviewService().export_reviewed(
        result.drafts,
        source_id=result.record.source_id,
        output_dir=tmp_path / "export",
        analysis_run_id=result.analysis_run_id,
        progress_recording_dir=result.progress_recording_dir,
    )
    manifest = json.loads(exported.manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 2
    assert manifest["analysis_run_id"] == result.analysis_run_id
    assert manifest["progress_recording_dir"] == result.progress_recording_dir
    assert (progress_dir / "07_review" / "exported_review_state.json").is_file()
    assert (progress_dir / "08_export" / "export_manifest.json").is_file()


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
<< /Length 47 >>
stream
BT 72 96 Td (Trace Concept) Tj ET
endstream
endobj
trailer
<< /Root 1 0 R >>
%%EOF
"""
    )
