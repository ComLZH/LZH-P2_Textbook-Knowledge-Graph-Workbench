from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from textbook_builder.contracts import EvidenceAnchorDTO
from textbook_builder.geometry import RenderMetricsProfile, layout_scene
from textbook_builder.review_document import (
    P2ReviewDocumentDTO,
    RELATION_FAMILY_SEMANTIC,
    RELATION_FAMILY_UNRESOLVED,
    ReviewDocumentMetadataDTO,
    ReviewNodeDTO,
    ReviewRelationDTO,
)
from textbook_builder.review_views import (
    G6ReviewHtmlRenderer,
    GraphReviewPayloadBuilder,
    ReviewProjectionBuilder,
)


def test_evidence_ordered_fanout_keeps_business_edges_and_routes_safely() -> None:
    document = _ordered_document()
    fingerprint = document.business_fingerprint()

    projection = ReviewProjectionBuilder().build(document)
    assert len(projection.reading_sequences) == 1
    sequence = projection.reading_sequences[0]
    assert sequence.verification_state == "evidence_backed"
    assert [(item.ordinal, item.node_id) for item in sequence.items] == [
        (1, "step-1"),
        (2, "step-2"),
        (3, "step-3"),
        (4, "step-4"),
        (5, "step-5"),
    ]
    assert len(sequence.routing_only_relation_ids) == 4

    geometry = layout_scene(projection, RenderMetricsProfile(renderer="test"))
    view_by_node = {item.node_id: item.view_id for item in projection.node_instances}
    step_rects = [geometry.node_rects[view_by_node[f"step-{index}"]] for index in range(1, 6)]
    assert len({round(rect.x, 6) for rect in step_rects}) == 1
    assert [rect.y for rect in step_rects] == sorted(rect.y for rect in step_rects)
    assert geometry.node_rects[view_by_node["parent"]].right < step_rects[0].x
    assert geometry.readability.hard_violations == 0
    assert len(geometry.edge_routes) == 9
    assert document.business_fingerprint() == fingerprint

    payload = GraphReviewPayloadBuilder().build_from_projection(
        projection=projection,
        geometry=geometry,
        document=document,
    )
    progressive = [edge for edge in payload["edges"] if edge["relationType"] == "progressive"]
    assert len(progressive) == 4
    assert {edge["layoutEdgeRole"] for edge in progressive} == {"routing_only"}
    assert all(not edge["is_layout_edge"] for edge in progressive)
    assert payload["visual_groups"]


def test_multi_target_paragraph_does_not_assign_ordinals_by_target_array() -> None:
    document = P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("ambiguous", "fixture"),
        nodes=[
            ReviewNodeDTO("parent", "制作步骤", "steps", evidence_anchor_ids=["e"]),
            ReviewNodeDTO("a", "甲方法", "a"),
            ReviewNodeDTO("b", "乙方法", "b"),
        ],
        relations=[
            ReviewRelationDTO("ca", RELATION_FAMILY_UNRESOLVED, "parent", "a", "contains"),
            ReviewRelationDTO("cb", RELATION_FAMILY_UNRESOLVED, "parent", "b", "contains"),
        ],
        evidence=[
            EvidenceAnchorDTO(
                anchor_id="e",
                anchor_text="1. 先完成第一项\n2. 再完成第二项",
                target_type="node",
                target_ids=["b", "a", "parent"],
            )
        ],
    )

    projection = ReviewProjectionBuilder().build(document)
    assert projection.reading_sequences == []


def test_relation_groups_use_base_graph_and_are_stable_under_hidden_filter() -> None:
    document = P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("groups", "fixture"),
        nodes=[ReviewNodeDTO(name, name.upper(), name) for name in "abcde"],
        relations=[
            ReviewRelationDTO("ab", RELATION_FAMILY_SEMANTIC, "a", "b", "explains"),
            ReviewRelationDTO("cd", RELATION_FAMILY_SEMANTIC, "c", "d", "parallel"),
        ],
    )
    builder = ReviewProjectionBuilder()
    first = builder.build(document)
    hidden_view_id = next(item.view_id for item in first.node_instances if item.node_id == "b")
    filtered = builder.build(document, hidden_view_ids={hidden_view_id})

    assert [(item.group_id, item.member_node_ids) for item in first.visual_groups] == [
        (item.group_id, item.member_node_ids) for item in filtered.visual_groups
    ]
    assert sorted(len(item.member_node_ids) for item in first.visual_groups) == [1, 2, 2]
    geometry = layout_scene(first, RenderMetricsProfile(renderer="test"))
    assert len(geometry.visual_groups) == 3
    assert geometry.readability.group_overlap == 0
    assert geometry.readability.group_overflow == 0


def test_arrow_polygons_and_terminal_vectors_stay_outside_cards() -> None:
    document = P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("terminal", "fixture"),
        nodes=[ReviewNodeDTO("a", "来源", "a"), ReviewNodeDTO("b", "目标", "b")],
        relations=[ReviewRelationDTO("r", RELATION_FAMILY_SEMANTIC, "a", "b", "explains")],
    )
    projection = ReviewProjectionBuilder().build(document)
    geometry = layout_scene(projection, RenderMetricsProfile(renderer="test"))
    route = geometry.edge_routes["edge:r"]

    assert route.target_arrow
    assert route.source_escape is not None and route.target_escape is not None
    assert geometry.readability.terminal_direction_violation == 0
    assert geometry.readability.terminal_stub_short == 0
    assert geometry.readability.arrow_occlusion == 0
    assert geometry.readability.edge_node_intrusion == 0


def test_locked_g6_view_exposes_distinct_camera_reflow_and_reset_actions() -> None:
    document = _ordered_document()
    projection = ReviewProjectionBuilder().build(document)
    geometry = layout_scene(projection, RenderMetricsProfile(renderer="test"))
    payload = GraphReviewPayloadBuilder().build_from_projection(
        projection=projection,
        geometry=geometry,
        document=document,
    )

    html = G6ReviewHtmlRenderer().render(payload=payload)

    assert 'id="fitBtn">适配</button>' in html
    assert 'id="reflowBtn">重新排版</button>' in html
    assert 'id="resetBtn">恢复默认视图</button>' in html
    assert "qtBridge.requestRelayout()" in html
    assert "event.key !== 'Escape'" in html
    assert "lockedAutomaticLayout" in html
    assert "? []\n              : ['collapse-expand-combo']" in html


def test_g6_payload_cannot_close_its_embedding_script() -> None:
    malicious = "</script><script>window.injected=true</script>\u2028"

    html = G6ReviewHtmlRenderer().render(
        payload={"graph_id": malicious, "nodes": [{"id": "n", "label": malicious}]}
    )

    assert "</script><script>window.injected=true</script>" not in html
    assert "<\\/script><script>window.injected=true<\\/script>\\u2028" in html


def test_large_graph_with_many_small_components_keeps_valid_components() -> None:
    node_count = 300
    nodes = [
        ReviewNodeDTO(f"n{index:03d}", f"知识点 {index:03d}", f"node-{index:03d}")
        for index in range(node_count)
    ]
    pairs = [
        (base + offset, base + offset + 1)
        for base in range(0, node_count, 10)
        for offset in range(9)
    ]
    relations = [
        ReviewRelationDTO(
            f"r{index:03d}",
            RELATION_FAMILY_SEMANTIC,
            f"n{source:03d}",
            f"n{target:03d}",
            "progressive",
        )
        for index, (source, target) in enumerate(pairs)
    ]
    document = P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("partitioned-large", "fixture"),
        nodes=nodes,
        relations=relations,
    )

    geometry = layout_scene(
        ReviewProjectionBuilder().build(document),
        RenderMetricsProfile(renderer="test"),
    )

    assert geometry.engine == "python_networkx_v6"
    assert geometry.readability.routed_edges == len(relations)
    assert geometry.readability.failed_edges == 0
    assert geometry.readability.hard_violations == 0
    assert len(geometry.visual_groups) == 30


def _ordered_document() -> P2ReviewDocumentDTO:
    labels = {
        "parent": "篆刻印章步骤",
        "step-1": "磨平印面",
        "step-2": "设计印稿",
        "step-3": "印稿上石",
        "step-4": "操刀刻印",
        "step-5": "钤印于纸",
    }
    nodes = [
        ReviewNodeDTO(
            node_id,
            label,
            node_id,
            evidence_anchor_ids=["steps-evidence"],
        )
        for node_id, label in labels.items()
    ]
    contains = [
        ReviewRelationDTO(
            f"contains-{index}",
            RELATION_FAMILY_UNRESOLVED,
            "parent",
            f"step-{index}",
            "contains",
        )
        for index in range(1, 6)
    ]
    progressive = [
        ReviewRelationDTO(
            f"progress-{index}",
            RELATION_FAMILY_SEMANTIC,
            f"step-{index}",
            f"step-{index + 1}",
            "progressive",
        )
        for index in range(1, 5)
    ]
    return P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("ordered", "fixture"),
        nodes=nodes,
        relations=contains + progressive,
        evidence=[
            EvidenceAnchorDTO(
                anchor_id="steps-evidence",
                anchor_text="5. 钤印于纸\n3. 印稿上石\n1. 磨平印面\n4. 操刀刻印\n2. 设计印稿",
                target_type="node",
                target_ids=list(reversed(labels)),
            )
        ],
    )
