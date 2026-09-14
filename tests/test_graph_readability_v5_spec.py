from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from textbook_builder.contracts import EvidenceAnchorDTO
from textbook_builder.geometry import RenderMetricsProfile, layout_scene
from textbook_builder.geometry.elk_backend import runtime_status
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
    VIEW_MODE_RELATIONS,
    relation_display_spec,
)
from textbook_builder.utils.subject_metadata import (
    derive_subject_metadata,
    missing_required_metadata,
)


def test_relation_style_never_mislabels_unresolved_or_unknown_as_explains() -> None:
    unresolved = relation_display_spec(RELATION_FAMILY_UNRESOLVED, "contains")
    explains = relation_display_spec(RELATION_FAMILY_SEMANTIC, "explains")
    future = relation_display_spec(RELATION_FAMILY_SEMANTIC, "future_relation")
    empty = relation_display_spec(RELATION_FAMILY_SEMANTIC, "")

    assert unresolved.key == "unresolved_contains"
    assert unresolved.label == "包含·含义待确认"
    assert unresolved.semantics_pending
    assert explains.label == "解释"
    assert not explains.semantics_pending
    assert future.label == "未知关系〔future_relation〕"
    assert empty.label == "关系类型待确认"


def test_subject_metadata_has_no_silent_math_or_geometry_fallback() -> None:
    art = derive_subject_metadata("Art", "G8", "Term1")
    math = derive_subject_metadata("math", "g11", "term2")
    unknown = derive_subject_metadata("", "", "")

    assert art.education_stage == "junior_middle_school"
    assert art.grade_band == "g7_g9"
    assert art.subject_tags == ("art",)
    assert math.education_stage == "senior_high_school"
    assert math.grade_band == "g10_g12"
    assert math.subject_tags == ("math",)
    assert unknown.subject_tags == ()
    assert missing_required_metadata("", "g8", "") == ("学科", "册次")


def test_web_upload_does_not_restore_removed_math_defaults() -> None:
    source = (PROJECT_ROOT / "src" / "textbook_builder" / "web_app.py").read_text(
        encoding="utf-8"
    )

    assert "value || 'math'" not in source
    assert "value || 'g8'" not in source
    assert "value || 'term1'" not in source
    assert "系统不会自动补成数学" in source


def test_rank_band_wraps_wide_rank_into_two_dimensions() -> None:
    nodes = [ReviewNodeDTO(f"n{index:02d}", f"节点 {index}", f"node-{index}") for index in range(16)]
    relations = [
        ReviewRelationDTO(
            f"r{index:02d}",
            RELATION_FAMILY_SEMANTIC,
            f"n{index:02d}",
            "n15",
            "prerequisite",
        )
        for index in range(15)
    ]
    document = P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("wide-rank", "fixture"),
        nodes=nodes,
        relations=relations,
    )
    projection = ReviewProjectionBuilder().build(document, view_mode=VIEW_MODE_RELATIONS)
    geometry = layout_scene(projection, RenderMetricsProfile(renderer="test"))
    counts_by_x: dict[float, int] = {}
    for rect in geometry.node_rects.values():
        counts_by_x[rect.x] = counts_by_x.get(rect.x, 0) + 1

    assert max(counts_by_x.values()) <= 5
    assert geometry.readability.node_overlap == 0
    assert geometry.readability.edge_node_intrusion == 0
    assert geometry.readability.failed_edges == 0


def test_g6_payload_keeps_exact_shared_ports_and_evidence() -> None:
    anchor = EvidenceAnchorDTO(
        anchor_id="anchor-1",
        source_id="book",
        anchor_text="原文证据",
        target_type="relation",
        target_ids=["r1"],
    )
    document = P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("evidence", "book"),
        nodes=[
            ReviewNodeDTO("a", "甲", "a", evidence_anchor_ids=["anchor-1"]),
            ReviewNodeDTO("b", "乙", "b"),
        ],
        relations=[
            ReviewRelationDTO(
                "r1",
                RELATION_FAMILY_SEMANTIC,
                "a",
                "b",
                "explains",
                evidence_anchor_ids=["anchor-1"],
                reasoning_summary="原文给出解释关系",
            )
        ],
        evidence=[anchor],
    )
    projection = ReviewProjectionBuilder().build(document, view_mode=VIEW_MODE_RELATIONS)
    geometry = layout_scene(projection, RenderMetricsProfile(renderer="g6"))
    payload = GraphReviewPayloadBuilder().build_from_projection(
        projection=projection,
        geometry=geometry,
        document=document,
    )
    edge = payload["edges"][0]
    route = geometry.edge_routes["edge:r1"]
    source = next(item for item in payload["nodes"] if item["id"] == edge["source"])
    target = next(item for item in payload["nodes"] if item["id"] == edge["target"])

    assert source["anchorPoints"][edge["sourceAnchor"]] in (
        [0.0, (route.points[0][1] - geometry.node_rects[edge["source"]].y) / 86.0],
        [1.0, (route.points[0][1] - geometry.node_rects[edge["source"]].y) / 86.0],
    )
    assert target["anchorPoints"][edge["targetAnchor"]]
    assert edge["evidenceAnchorIds"] == ["anchor-1"]
    assert payload["evidence_anchors"][0]["anchor_text"] == "原文证据"
    assert payload["readability_report"]["edge_node_intrusion"] == 0


def test_g6_details_escape_untrusted_html() -> None:
    payload = {
        "graph_id": "unsafe",
        "metadata": {"view_mode": "relations", "layout_version": 5},
        "nodes": [{"id": "n", "label": "<img src=x onerror=alert(1)>", "x": 0, "y": 0}],
        "edges": [],
        "combos": [],
    }
    html = G6ReviewHtmlRenderer().render(payload=payload)

    assert "function escapeHtml" in html
    assert ".replaceAll('&', '&amp;')" in html
    assert "escapeHtml(model.label || model.id)" in html
    assert 'id="oneHopBtn"' in html
    assert 'id="twoHopBtn"' in html
    assert "function showNeighborhood(maxHops)" in html


def test_missing_elk_runtime_uses_explicit_python_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("P2_LAYOUT_RUNTIME_ROOT", str(tmp_path / "missing-runtime"))
    document = P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("fallback", "fixture"),
        nodes=[ReviewNodeDTO("a", "甲", "a"), ReviewNodeDTO("b", "乙", "b")],
        relations=[
            ReviewRelationDTO("r", RELATION_FAMILY_SEMANTIC, "a", "b", "explains")
        ],
    )
    projection = ReviewProjectionBuilder().build(document, view_mode=VIEW_MODE_RELATIONS)

    geometry = layout_scene(projection, RenderMetricsProfile(renderer="test"))

    assert runtime_status()["available"] is False
    assert geometry.engine == "python_networkx_v6"
    assert any(issue.code == "layout_backend_fallback" for issue in geometry.issues)
    assert geometry.readability.hard_violations == 0


def test_oversized_graph_returns_bounded_diagnostic_instead_of_recursing() -> None:
    nodes = [ReviewNodeDTO(f"n{index}", f"节点 {index}", f"node-{index}") for index in range(300)]
    relations = [
        ReviewRelationDTO(
            f"r{index}",
            RELATION_FAMILY_SEMANTIC,
            f"n{index}",
            f"n{index + 1}",
            "progressive",
        )
        for index in range(299)
    ]
    document = P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO("oversized", "fixture"),
        nodes=nodes,
        relations=relations,
    )
    projection = ReviewProjectionBuilder().build(document, view_mode=VIEW_MODE_RELATIONS)

    geometry = layout_scene(projection, RenderMetricsProfile(renderer="test"))

    assert geometry.engine == "bounded_diagnostic_grid_v6"
    assert geometry.readability.routed_edges == 0
    assert geometry.readability.failed_edges == 299
    assert any(issue.code == "layout_scale_degraded" for issue in geometry.issues)
