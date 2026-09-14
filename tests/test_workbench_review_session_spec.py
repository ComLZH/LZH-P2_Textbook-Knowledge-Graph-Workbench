from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import DraftKnowledgeItemDTO, EvidenceAnchorDTO
from textbook_builder.exporters import FormalGraphWorkbookBuilder
from textbook_builder.services import WorkbenchReviewSession


def test_review_session_undo_restores_deep_state() -> None:
    session = WorkbenchReviewSession(drafts=_drafts())
    session.graph_node_positions["node_a"] = (10.0, 20.0)
    session.g6_view_config = {
        "manual_positions": {"node_a": {"x": 10.0, "y": 20.0}}
    }
    session.push_snapshot("编辑节点")

    session.drafts[1].candidate_display_name = "已修改"
    session.graph_node_positions["node_a"] = (90.0, 100.0)
    session.g6_view_config["manual_positions"]["node_a"]["x"] = 90.0

    assert session.undo() == "编辑节点"
    assert session.drafts[1].candidate_display_name == "节点 A"
    assert session.graph_node_positions["node_a"] == (10.0, 20.0)
    assert session.g6_view_config["manual_positions"]["node_a"]["x"] == 10.0


def test_review_session_add_replace_status_and_remove_relation() -> None:
    session = WorkbenchReviewSession(drafts=_drafts(include_relation=False))
    edge_id = session.add_or_update_relation(
        source_node_id="node_a",
        target_node_id="node_b",
        relation_type="explains",
        status="pending",
        evidence="教材证据",
    )
    details = session.relation_details_by_edge_id(edge_id)
    assert details is not None
    assert details["relation_type"] == "explains"
    assert details["evidence"] == "教材证据"

    session.update_relation_status(edge_id, "accepted")
    assert session.relation_details_by_edge_id(edge_id)["status"] == "accepted"

    next_edge_id = session.replace_relation(
        edge_id=edge_id,
        source_node_id="node_b",
        target_node_id="node_a",
        relation_type="progressive",
        status="accepted",
        evidence="调整后的证据",
    )
    assert session.relation_details_by_edge_id(edge_id) is None
    assert session.relation_details_by_edge_id(next_edge_id)["source"] == "node_b"
    assert session.remove_relation_by_edge_id(next_edge_id) is True
    assert session.relation_details_by_edge_id(next_edge_id) is None

    with pytest.raises(ValueError, match="不能相同"):
        session.add_or_update_relation(
            source_node_id="node_a",
            target_node_id="node_a",
            relation_type="explains",
            status="pending",
            evidence="",
        )


def test_review_session_delete_node_cleans_all_references_and_positions() -> None:
    session = WorkbenchReviewSession(drafts=_drafts())
    session.drafts[2].candidate_prerequisites = ["node_a"]
    edge_id = session.edge_status_key("node_a", "explains", "node_b")
    session.relation_status_overrides[edge_id] = "accepted"
    session.graph_node_positions["node_a"] = (12.0, 24.0)
    session.g6_view_config = {
        "manual_positions": {"node_a": {"x": 12.0, "y": 24.0}}
    }

    assert session.delete_node("node_a") is True
    assert session.draft_by_node_id("node_a") is None
    node_b = session.draft_by_node_id("node_b")
    assert node_b is not None
    assert node_b.candidate_prerequisites == []
    assert all(
        relation.get("source_node_id") != "node_a"
        and relation.get("target_node_id") != "node_a"
        for draft in session.drafts
        for relation in draft.candidate_relations
    )
    assert "node_a" not in session.graph_node_positions
    assert "node_a" not in session.g6_view_config["manual_positions"]
    assert edge_id not in session.relation_status_overrides


def test_review_session_accepts_pending_nodes_and_relations() -> None:
    drafts = _drafts()
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id="REVIEW_SESSION",
    )
    session = WorkbenchReviewSession(drafts=drafts)

    assert session.pending_node_count() == 2
    assert session.pending_relation_count(workbook) == 3
    assert session.accept_pending_nodes() == 2
    assert session.accept_pending_relations(workbook) == 3
    assert session.pending_node_count() == 0
    assert session.pending_relation_count(workbook) == 0
    assert drafts[1].evidence_anchors[0].review_status == "accepted"
    assert drafts[1].candidate_relations[0]["review_status"] == "accepted"


def test_review_session_limits_undo_history_and_detects_rejected_relations() -> None:
    drafts = _drafts()
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id="REVIEW_SESSION",
    )
    session = WorkbenchReviewSession(drafts=drafts, max_undo_steps=3)
    for index in range(5):
        session.push_snapshot(f"step-{index}")
    assert len(session.undo_stack) == 3
    assert session.undo_stack[0].label == "step-2"

    relation_edge = next(edge for edge in workbook.edges if edge.relation_type == "explains")
    edge_id = session.edge_status_key(
        relation_edge.source_node_id,
        relation_edge.relation_type,
        relation_edge.target_node_id,
    )
    session.update_relation_status(edge_id, "rejected")
    assert session.rejected_relation_edge_ids(workbook) == [edge_id]


def _drafts(*, include_relation: bool = True) -> list[DraftKnowledgeItemDTO]:
    chapter = _draft(
        node_id="chapter",
        name="章节",
        node_type="container",
        status="accepted",
    )
    node_a = _draft(
        node_id="node_a",
        name="节点 A",
        parent_id="chapter",
        status="pending",
    )
    node_b = _draft(
        node_id="node_b",
        name="节点 B",
        parent_id="chapter",
        status="pending",
    )
    if include_relation:
        node_a.candidate_relations.append(
            WorkbenchReviewSession.manual_relation_payload(
                source_node_id="node_a",
                target_node_id="node_b",
                relation_type="explains",
                status="pending",
                evidence="教材证据",
            )
        )
    return [chapter, node_a, node_b]


def _draft(
    *,
    node_id: str,
    name: str,
    node_type: str = "concept",
    parent_id: str = "",
    status: str,
) -> DraftKnowledgeItemDTO:
    anchor = EvidenceAnchorDTO(
        anchor_id=f"anchor_{node_id}",
        anchor_text=f"{name} 的证据",
        target_ids=[node_id],
        review_status=status,
    )
    return DraftKnowledgeItemDTO(
        draft_id=f"draft_{node_id}",
        subject="math",
        grade="g8",
        term="term1",
        chapter="chapter",
        candidate_display_name=name,
        candidate_node_name=node_id,
        candidate_node_id=node_id,
        candidate_parent_name="章节" if parent_id else "",
        candidate_parent_node_id=parent_id,
        knowledge_type=node_type,
        source_text=anchor.anchor_text,
        evidence_anchors=[anchor],
        review_status=status,
    )
