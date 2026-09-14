from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import NODE_TYPE_CONTAINER
from textbook_builder.exporters import FormalGraphWorkbookBuilder
from textbook_builder.llm import LlmCandidatePayloadParser
from textbook_builder.utils.graph_semantics import normalize_node_type, normalize_relation_type
from textbook_builder.contracts import SourceRecordDTO


def test_graph_semantic_aliases_are_normalized_to_formal_v1_values() -> None:
    assert normalize_node_type("principle") == "property"
    assert normalize_node_type("procedure") == "method"
    assert normalize_node_type("chapter") == "container"
    assert normalize_relation_type("sequence") == "progressive"
    assert normalize_relation_type("supports") == "explains"
    assert normalize_relation_type("application") == "applies_to"
    assert normalize_relation_type("related") == "explains"


def test_llm_parser_and_formal_builder_align_legacy_values_to_new_standard() -> None:
    record = SourceRecordDTO(
        source_id="TEXTBOOK",
        source_type="raw_document",
        source_path="demo",
        subject="math",
        grade="g8",
        term="term1",
        raw_text="教材先介绍三角形概念，再介绍三边关系。",
        source_format="pdf",
        source_document_type="electronic_textbook",
    )
    payload = {
        "chapters": [
            {
                "chapter": "ch11",
                "title": "三角形",
                "knowledge_points": [
                    {
                        "candidate_display_name": "三角形概念",
                        "candidate_node_name": "triangle_concept",
                        "knowledge_type": "concept",
                        "review_status": "pending",
                    },
                    {
                        "candidate_display_name": "三角形三边关系",
                        "candidate_node_name": "triangle_side_relation",
                        "knowledge_type": "principle",
                        "review_status": "pending",
                        "candidate_relations": [
                            {
                                "source_node_id": "math_g8_term1_ch11_triangle_concept",
                                "target_node_id": "math_g8_term1_ch11_triangle_side_relation",
                                "relation_type": "sequence",
                                "review_status": "pending",
                            }
                        ],
                    },
                ],
            }
        ]
    }

    drafts = LlmCandidatePayloadParser().parse(json.dumps(payload, ensure_ascii=False), record=record)
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(drafts, graph_id="REVIEW_GRAPH")

    chapter_draft = drafts[0]
    point_draft = drafts[2]
    assert chapter_draft.knowledge_type == NODE_TYPE_CONTAINER
    assert point_draft.knowledge_type == "property"
    assert point_draft.candidate_relations[0]["relation_type"] == "progressive"
    assert any(node.node_type == NODE_TYPE_CONTAINER for node in workbook.nodes)
    assert any(edge.relation_type == "progressive" for edge in workbook.edges)


if __name__ == "__main__":
    test_graph_semantic_aliases_are_normalized_to_formal_v1_values()
    test_llm_parser_and_formal_builder_align_legacy_values_to_new_standard()
    print("PASS graph semantics tests")
