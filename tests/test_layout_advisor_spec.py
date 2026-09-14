from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import FormalEdgeDTO, FormalGraphMetadataDTO, FormalGraphWorkbookDTO, FormalNodeDTO, SourceRecordDTO
from textbook_builder.llm.layout_advisor import LlmChapterStructureAdvisor
from textbook_builder.utils.chapter_layout import ChapterLayoutPlan


class _FakeJsonClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.captured_system_prompt = ""
        self.captured_user_prompt = ""

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.captured_system_prompt = system_prompt
        self.captured_user_prompt = user_prompt
        return json.dumps(self.payload, ensure_ascii=False)


def test_layout_advisor_returns_structured_recommendations() -> None:
    workbook = FormalGraphWorkbookDTO(
        metadata=FormalGraphMetadataDTO("graph_1", "math", ["g8"], ["term1"]),
        nodes=[
            FormalNodeDTO("chapter_1", "math", "g8", "term1", "第1章", "第1章", "chapter_1", node_type="container", knowledge_type="container"),
            FormalNodeDTO("k1", "math", "g8", "term1", "第1章", "概念A", "concept_a", parent_node_id="chapter_1", node_type="concept", knowledge_type="concept"),
            FormalNodeDTO("k2", "math", "g8", "term1", "第1章", "规则B", "rule_b", parent_node_id="chapter_1", node_type="rule", knowledge_type="rule"),
            FormalNodeDTO("a1", "math", "g8", "term1", "第1章", "应用C", "app_c", parent_node_id="chapter_1", node_type="application", knowledge_type="application"),
        ],
        edges=[
            FormalEdgeDTO("chapter_1", "k1", "contains"),
            FormalEdgeDTO("chapter_1", "k2", "contains"),
            FormalEdgeDTO("chapter_1", "a1", "contains"),
            FormalEdgeDTO("k1", "k2", "progressive"),
            FormalEdgeDTO("k2", "a1", "applies_to"),
        ],
    )
    record = SourceRecordDTO(
        source_id="SRC_1",
        source_type="document",
        source_path="demo.pdf",
        subject="math",
        grade="g8",
        term="term1",
    )
    fake_client = _FakeJsonClient(
        {
            "chapter_layout_recommendations": [
                {
                    "container_node_id": "chapter_1",
                    "recommended_layout_mode": "single_path",
                    "main_path": ["k1", "k2"],
                    "auxiliary_attachments": {"k2": ["a1"]},
                    "reasoning_summary": "应用节点侧挂到规则节点。",
                    "confidence": 0.9,
                }
            ]
        }
    )
    advisor = LlmChapterStructureAdvisor(fake_client)
    recommendations = advisor.advise(
        record=record,
        workbook=workbook,
        local_plans={
            "chapter_1": ChapterLayoutPlan(
                container_node_id="chapter_1",
                layout_mode="main_path_with_branches",
                main_path_nodes=["k1", "k2"],
                local_confidence=0.6,
            )
        },
    )

    assert "chapter_1" in recommendations
    assert recommendations["chapter_1"]["main_path"] == ["k1", "k2"]
    assert "章节内部结构顾问" in fake_client.captured_system_prompt
    assert "advise_textbook_chapter_internal_layout" in fake_client.captured_user_prompt


if __name__ == "__main__":
    test_layout_advisor_returns_structured_recommendations()
    print("PASS layout advisor tests")
