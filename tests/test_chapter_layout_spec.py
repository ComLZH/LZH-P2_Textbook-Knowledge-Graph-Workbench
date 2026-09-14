from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import FormalEdgeDTO, FormalNodeDTO
from textbook_builder.utils.chapter_layout import (
    LAYOUT_MODE_MAIN_PATH_WITH_BRANCHES,
    build_chapter_layout_plans,
)


def test_local_chapter_layout_builds_main_path_and_branches() -> None:
    nodes = [
        FormalNodeDTO("chapter_1", "math", "g8", "term1", "第1章", "第1章", "chapter_1", node_type="container", knowledge_type="container"),
        FormalNodeDTO("k1", "math", "g8", "term1", "第1章", "概念A", "concept_a", parent_node_id="chapter_1", node_type="concept", knowledge_type="concept"),
        FormalNodeDTO("k2", "math", "g8", "term1", "第1章", "性质B", "property_b", parent_node_id="chapter_1", node_type="property", knowledge_type="property"),
        FormalNodeDTO("k3", "math", "g8", "term1", "第1章", "规则C", "rule_c", parent_node_id="chapter_1", node_type="rule", knowledge_type="rule"),
        FormalNodeDTO("k4", "math", "g8", "term1", "第1章", "方法D", "method_d", parent_node_id="chapter_1", node_type="method", knowledge_type="method"),
        FormalNodeDTO("k5", "math", "g8", "term1", "第1章", "性质E", "property_e", parent_node_id="chapter_1", node_type="property", knowledge_type="property"),
        FormalNodeDTO("a1", "math", "g8", "term1", "第1章", "题型F", "problem_f", parent_node_id="chapter_1", node_type="problem_type", knowledge_type="problem_type"),
    ]
    edges = [
        FormalEdgeDTO("chapter_1", "k1", "contains"),
        FormalEdgeDTO("chapter_1", "k2", "contains"),
        FormalEdgeDTO("chapter_1", "k3", "contains"),
        FormalEdgeDTO("chapter_1", "k4", "contains"),
        FormalEdgeDTO("chapter_1", "k5", "contains"),
        FormalEdgeDTO("chapter_1", "a1", "contains"),
        FormalEdgeDTO("k1", "k2", "prerequisite"),
        FormalEdgeDTO("k2", "k3", "progressive"),
        FormalEdgeDTO("k3", "k4", "derives_to"),
        FormalEdgeDTO("k2", "k5", "derives_to"),
        FormalEdgeDTO("k4", "a1", "applies_to"),
    ]

    plans = build_chapter_layout_plans(nodes, edges)
    plan = plans["chapter_1"]

    assert plan.layout_mode == LAYOUT_MODE_MAIN_PATH_WITH_BRANCHES
    assert plan.main_path_nodes[:3] == ["k1", "k2", "k3"]
    assert "k5" in plan.branch_groups.get("k2", [])
    assert "a1" in plan.auxiliary_attachments.get("k4", [])
    assert plan.local_confidence > 0.7


def test_model_recommendation_can_override_local_plan() -> None:
    nodes = [
        FormalNodeDTO("chapter_1", "math", "g8", "term1", "第1章", "第1章", "chapter_1", node_type="container", knowledge_type="container"),
        FormalNodeDTO("k1", "math", "g8", "term1", "第1章", "概念A", "concept_a", parent_node_id="chapter_1", node_type="concept", knowledge_type="concept"),
        FormalNodeDTO("k2", "math", "g8", "term1", "第1章", "性质B", "property_b", parent_node_id="chapter_1", node_type="property", knowledge_type="property"),
        FormalNodeDTO("k3", "math", "g8", "term1", "第1章", "规则C", "rule_c", parent_node_id="chapter_1", node_type="rule", knowledge_type="rule"),
        FormalNodeDTO("a1", "math", "g8", "term1", "第1章", "应用D", "app_d", parent_node_id="chapter_1", node_type="application", knowledge_type="application"),
    ]
    edges = [
        FormalEdgeDTO("chapter_1", "k1", "contains"),
        FormalEdgeDTO("chapter_1", "k2", "contains"),
        FormalEdgeDTO("chapter_1", "k3", "contains"),
        FormalEdgeDTO("chapter_1", "a1", "contains"),
        FormalEdgeDTO("k1", "k2", "progressive"),
        FormalEdgeDTO("k2", "k3", "derives_to"),
        FormalEdgeDTO("k3", "a1", "applies_to"),
    ]
    plans = build_chapter_layout_plans(
        nodes,
        edges,
        recommendations={
            "chapter_1": {
                "recommended_layout_mode": "single_path",
                "main_path": ["k1", "k3"],
                "branch_groups": {"k1": ["k2"]},
                "auxiliary_attachments": {"k3": ["a1"]},
                "reasoning_summary": "将性质作为概念分支。",
                "confidence": 0.88,
            }
        },
    )
    plan = plans["chapter_1"]

    assert plan.source == "model_suggested"
    assert plan.main_path_nodes == ["k1", "k3"]
    assert plan.branch_groups == {"k1": ["k2"]}
    assert plan.auxiliary_attachments == {"k3": ["a1"]}
    assert plan.local_confidence == 0.88


if __name__ == "__main__":
    test_local_chapter_layout_builds_main_path_and_branches()
    test_model_recommendation_can_override_local_plan()
    print("PASS chapter layout tests")
