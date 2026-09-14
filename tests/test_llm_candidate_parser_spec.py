from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import SourceRecordDTO
from textbook_builder.exporters import FormalGraphWorkbookBuilder, ReviewHtmlExporter
from textbook_builder.llm import LlmCandidatePayloadParser


def test_llm_candidate_parser_preserves_pending_candidates_and_evidence() -> None:
    record = SourceRecordDTO(
        source_id="RJ_MATH_G8_2025_AUTUMN",
        source_type="raw_document",
        source_path=str(PROJECT_ROOT / "textbook" / "2025秋人教版八上数学电子课本.pdf"),
        subject="math",
        grade="g8",
        term="term1",
        raw_text="三角形任意两边的和大于第三边。",
        source_format="pdf",
        source_document_type="electronic_textbook",
        education_stage="junior_middle_school",
        grade_band="g7_g9",
        subject_tags=["math", "geometry"],
    )
    payload = {
        "chapters": [
            {
                "chapter": "ch11",
                "title": "三角形",
                "knowledge_points": [
                    {
                        "candidate_display_name": "三角形三边关系",
                        "candidate_node_name": "triangle_side_relation",
                        "knowledge_type": "principle",
                        "source_text": "三角形任意两边的和大于第三边。",
                        "source_location": "page=42;paragraph=2",
                        "reasoning_summary": "教材原文直接给出三边关系性质。",
                        "review_status": "pending",
                    }
                ],
            }
        ]
    }

    drafts = LlmCandidatePayloadParser().parse(json.dumps(payload, ensure_ascii=False), record=record)
    chapter, point = drafts
    review_workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id="REVIEW_GRAPH",
    )

    assert chapter.review_status == "accepted"
    assert point.review_status == "pending"
    assert point.extractor_source == "llm_assisted_extractor"
    assert point.reasoning_summary == "教材原文直接给出三边关系性质。"
    assert point.evidence_anchors[0].source_format == "pdf"
    assert point.evidence_anchors[0].target_ids == [point.candidate_node_id]
    assert len(review_workbook.nodes) == 2


def test_three_region_review_html_contains_source_anchor_and_pending_node() -> None:
    record = SourceRecordDTO(
        source_id="TEXTBOOK",
        source_type="raw_document",
        source_path=str(PROJECT_ROOT / "textbook" / "2025秋人教版八上数学电子课本.pdf"),
        subject="math",
        grade="g8",
        term="term1",
        raw_text="三角形任意两边的和大于第三边。",
        source_format="pdf",
    )
    payload = {
        "chapters": [
            {
                "chapter": "ch11",
                "title": "三角形",
                "knowledge_points": [
                    {
                        "candidate_display_name": "三角形三边关系",
                        "candidate_node_name": "triangle_side_relation",
                        "source_text": "三角形任意两边的和大于第三边。",
                    }
                ],
            }
        ]
    }
    drafts = LlmCandidatePayloadParser().parse(payload, record=record)
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id="REVIEW_GRAPH",
    )
    html = ReviewHtmlExporter()._build_html(drafts=drafts, workbook=workbook)

    assert "教材原文驱动审查工作台" in html
    assert "教材原文与证据锚点" in html
    assert "知识图谱概览" in html
    assert "知识点与关系结构表" in html
    assert "data-anchor-id" in html
    assert "pending" in html
    assert "chapter-container" in html


if __name__ == "__main__":
    test_llm_candidate_parser_preserves_pending_candidates_and_evidence()
    test_three_region_review_html_contains_source_anchor_and_pending_node()
    print("PASS llm candidate parser tests")
