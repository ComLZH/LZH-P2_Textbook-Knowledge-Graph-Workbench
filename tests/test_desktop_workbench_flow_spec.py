from __future__ import annotations

import json
import sys
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.exporters import FormalGraphWorkbookBuilder, XlsxWorkbookExporter
from textbook_builder.llm import LlmCandidatePayloadParser
from textbook_builder.readers import DocumentReadOptions, PdfPageRange, SourceDocumentReader


def test_desktop_workbench_underlying_flow_can_generate_and_export_candidates() -> None:
    temp_dir = PROJECT_ROOT / "storage" / "test_runs" / f"desktop_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        pdf_path = temp_dir / "source.pdf"
        _write_minimal_pdf(pdf_path)
        record = SourceDocumentReader().read(
            pdf_path,
            DocumentReadOptions(
                subject="math",
                grade="g8",
                term="term1",
                source_id="LOCAL_TEXTBOOK",
                pdf_page_ranges=[PdfPageRange(1, 1, label="local_scope")],
            ),
        )[0]
        payload = {
            "chapters": [
                {
                    "chapter": "ch_local",
                    "title": "Local",
                    "knowledge_points": [
                        {
                            "candidate_display_name": "Local Concept",
                            "candidate_node_name": "local_concept",
                            "review_status": "pending",
                        }
                    ],
                }
            ]
        }
        drafts = LlmCandidatePayloadParser().parse(json.dumps(payload), record=record)
        workbook = FormalGraphWorkbookBuilder().build_review_workbook(
            drafts,
            graph_id="REVIEW_LOCAL_TEXTBOOK",
        )
        draft_path = temp_dir / "reviewed_draft.xlsx"
        XlsxWorkbookExporter().export_draft(drafts, draft_path)

        assert draft_path.exists()
        assert len(drafts) == 2
        assert len(workbook.nodes) == 2
        assert drafts[1].review_status == "pending"
        assert drafts[1].evidence_anchors[0].target_ids == [drafts[1].candidate_node_id]
    finally:
        rmtree(temp_dir, ignore_errors=True)


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
BT /F1 12 Tf 72 96 Td (Local Concept) Tj ET
endstream
endobj
trailer
<< /Root 1 0 R >>
%%EOF
"""
    )


if __name__ == "__main__":
    test_desktop_workbench_underlying_flow_can_generate_and_export_candidates()
    print("PASS desktop workbench flow tests")
