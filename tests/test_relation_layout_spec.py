from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import (
    FormalEdgeDTO,
    FormalGraphMetadataDTO,
    FormalGraphWorkbookDTO,
    FormalNodeDTO,
)
from textbook_builder.review_views import G6ReviewHtmlRenderer, GraphReviewPayloadBuilder
from textbook_builder.utils.relation_layout import (
    build_relation_layouts,
    relation_edge_id,
)


def test_directional_layout_is_deterministic_left_to_right_with_parallel_targets() -> None:
    nodes = _chapter_nodes("a", "b", "c", "d", "e")
    edges = _contains_edges("a", "b", "c", "d", "e") + [
        FormalEdgeDTO("a", "b", "prerequisite"),
        FormalEdgeDTO("a", "c", "prerequisite"),
        FormalEdgeDTO("b", "c", "parallel"),
        FormalEdgeDTO("b", "d", "applies_to"),
        FormalEdgeDTO("c", "e", "explains"),
    ]

    first = build_relation_layouts(nodes, edges)["chapter_1"]
    second = build_relation_layouts(nodes, edges)["chapter_1"]

    assert first.as_dict() == second.as_dict()
    assert first.node_ranks["a"] < first.node_ranks["b"]
    assert first.node_ranks["a"] < first.node_ranks["c"]
    assert first.node_ranks["b"] == first.node_ranks["c"]
    assert first.node_ranks["b"] < first.node_ranks["d"]
    assert first.node_ranks["c"] < first.node_ranks["e"]
    assert first.node_orders["b"] != first.node_orders["c"]
    assert first.cycle_edge_ids == []


def test_directional_cycle_is_collapsed_and_reported_without_reversing_edges() -> None:
    nodes = _chapter_nodes("a", "b", "c")
    edges = _contains_edges("a", "b", "c") + [
        FormalEdgeDTO("a", "b", "progressive"),
        FormalEdgeDTO("b", "c", "derives_to"),
        FormalEdgeDTO("c", "a", "prerequisite"),
    ]

    layout = build_relation_layouts(nodes, edges)["chapter_1"]

    assert {layout.node_ranks[node_id] for node_id in ("a", "b", "c")} == {0}
    assert layout.cycle_node_ids == ["a", "b", "c"]
    assert set(layout.cycle_edge_ids) == {
        relation_edge_id("a", "progressive", "b"),
        relation_edge_id("b", "derives_to", "c"),
        relation_edge_id("c", "prerequisite", "a"),
    }


def test_relation_free_structure_keeps_compact_three_column_fallback() -> None:
    nodes = _chapter_nodes("a", "b", "c", "d", "e")
    layout = build_relation_layouts(
        nodes,
        _contains_edges("a", "b", "c", "d", "e"),
    )["chapter_1"]

    assert layout.fallback_mode == "structural_grid"
    assert [layout.node_ranks[node_id] for node_id in ("a", "b", "c", "d", "e")] == [
        0,
        1,
        2,
        0,
        1,
    ]


def test_g6_payload_uses_shared_ranks_anchors_and_neutral_symmetric_routing() -> None:
    nodes = _chapter_nodes("a", "b", "c", "d", "e")
    edges = _contains_edges("a", "b", "c", "d", "e") + [
        FormalEdgeDTO("a", "b", "prerequisite"),
        FormalEdgeDTO("a", "c", "prerequisite"),
        FormalEdgeDTO("b", "c", "parallel"),
        FormalEdgeDTO("b", "d", "applies_to"),
        FormalEdgeDTO("c", "e", "explains"),
    ]
    workbook = FormalGraphWorkbookDTO(
        metadata=FormalGraphMetadataDTO("RELATION_LAYOUT", "math", ["g8"], ["term1"]),
        nodes=nodes,
        edges=edges,
    )

    payload = GraphReviewPayloadBuilder().build(
        drafts=[],
        workbook=workbook,
        view_config={
            "layout_revision": 2,
            "manual_positions": {"b": {"x": -500.0, "y": -500.0}},
        },
    )
    positions = {
        item["id"]: (item["x"], item["y"])
        for item in payload["nodes"]
    }
    edge_by_id = {edge["id"]: edge for edge in payload["edges"]}

    for edge in payload["edges"]:
        if edge["directionality"] != "directional" or edge["isCycleEdge"]:
            continue
        assert positions[edge["source"]][0] < positions[edge["target"]][0]
        assert edge["sourceAnchor"] == 1
        assert edge["targetAnchor"] == 0
        assert edge["layoutDirection"] == "left_to_right"

    parallel = edge_by_id[relation_edge_id("b", "parallel", "c")]
    assert positions["b"][0] == positions["c"][0]
    assert positions["b"][1] != positions["c"][1]
    assert parallel["directionality"] == "symmetric"
    assert parallel["routing"] == "vertical_neutral"
    assert payload["view_config"]["layout_revision"] == 4
    assert payload["view_config"]["manual_positions"] == {}
    assert payload["relation_layout"]["chapter_1"]["max_rank"] == 2

    html = G6ReviewHtmlRenderer().render(payload=payload)
    assert "arrowMode: 'both'" in html
    assert "arrowMode: 'none'" in html
    assert "vertical_neutral" in html
    assert "cycle_back" in html
    assert "sourceAnchor" in html


def _chapter_nodes(*node_ids: str) -> list[FormalNodeDTO]:
    nodes = [
        FormalNodeDTO(
            "chapter_1",
            "math",
            "g8",
            "term1",
            "第1章",
            "第1章",
            "chapter_1",
            node_type="container",
            knowledge_type="container",
        )
    ]
    for node_id in node_ids:
        nodes.append(
            FormalNodeDTO(
                node_id,
                "math",
                "g8",
                "term1",
                "第1章",
                f"节点 {node_id}",
                f"node_{node_id}",
                parent_node_id="chapter_1",
                node_type="container",
                knowledge_type="container",
            )
        )
    return nodes


def _contains_edges(*node_ids: str) -> list[FormalEdgeDTO]:
    return [FormalEdgeDTO("chapter_1", node_id, "contains") for node_id in node_ids]
