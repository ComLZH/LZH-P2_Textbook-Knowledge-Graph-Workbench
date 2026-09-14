from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import DraftKnowledgeItemDTO, EvidenceAnchorDTO
from textbook_builder.exporters import FormalGraphWorkbookBuilder
from textbook_builder.mapping import LayerMappingBuilder
from textbook_builder.quality import GraphQualityChecker
from textbook_builder.review_views import G6ReviewHtmlRenderer, GraphReviewPayloadBuilder
from textbook_builder.services import WorkbenchReviewSession
from textbook_builder.utils.hierarchy import (
    project_draft_hierarchy,
    synchronize_draft_hierarchy,
)


def test_catalog_candidate_hierarchy_projects_six_chapters_and_nineteen_sections() -> None:
    drafts = _catalog_drafts()
    hierarchy = synchronize_draft_hierarchy(drafts)
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id="CATALOG_REVIEW",
    )
    payload = GraphReviewPayloadBuilder().build(drafts=drafts, workbook=workbook)

    assert len(hierarchy.root_node_ids) == 6
    assert len(hierarchy.parent_by_child) == 19
    assert len(payload["combos"]) == 6
    assert len(payload["nodes"]) == 19
    assert len(payload["hierarchy"]["parent_by_child"]) == 19
    assert len(payload["layout_plan"]) == 6
    assert [
        combo["id"]
        for combo in sorted(payload["combos"], key=lambda item: item["y"])
    ] == [f"chapter_{index}" for index in range(1, 7)]
    assert all(node["structuralRole"] == "section" for node in payload["nodes"])
    assert all(node["comboId"].startswith("chapter_") for node in payload["nodes"])
    assert all(edge["relationType"] != "contains" for edge in payload["edges"])


def test_catalog_batch_acceptance_is_atomic_and_does_not_accept_semantic_order() -> None:
    drafts = _catalog_drafts(include_progressive=True)
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id="CATALOG_REVIEW",
    )
    session = WorkbenchReviewSession(drafts=drafts)

    assert session.pending_structure_relation_count(workbook) == 19
    result = session.accept_catalog_structure(workbook)

    assert result.node_count == 25
    assert result.relation_count == 19
    assert result.skipped_issue_count == 0
    progressive = next(
        relation
        for draft in drafts
        for relation in draft.candidate_relations
        if relation["relation_type"] == "progressive"
    )
    assert progressive["review_status"] == "pending"

    quality = GraphQualityChecker().check_drafts(drafts)
    formal = FormalGraphWorkbookBuilder().build(drafts, graph_id="CATALOG_FORMAL")
    mapping = LayerMappingBuilder().build(formal)

    assert quality.has_errors is False
    assert len(formal.nodes) == 25
    assert len(formal.edges) == 19
    assert {edge.relation_type for edge in formal.edges} == {"contains"}
    assert len(mapping.chapter_nodes) == 6
    assert len(mapping.knowledge_nodes) == 19
    assert all(
        node.attributes["structural_role"] == "section"
        for node in mapping.knowledge_nodes
    )


def test_container_semantic_relations_remain_visible_in_g6_edges() -> None:
    drafts = _catalog_drafts(include_progressive=True)
    drafts[1].candidate_relations.append(
        {
            "source_node_id": "section_1_1",
            "target_node_id": "section_1_2",
            "relation_type": "prerequisite",
            "confidence": 0.4,
            "relation_evidence": "仅由目录顺序推断。",
            "relation_source": "test_fixture",
            "review_status": "needs_expert_review",
        }
    )
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id="CATALOG_CONTAINER_EDGE",
    )
    payload = GraphReviewPayloadBuilder().build(drafts=drafts, workbook=workbook)

    assert len(payload["edges"]) == 2
    assert payload["suppressed_container_relations"] == []
    assert {
        relation["relationType"]
        for relation in payload["edges"]
    } == {"progressive", "prerequisite"}
    assert all(not relation["is_layout_edge"] for relation in payload["edges"])
    assert payload["layout_edges"] == []
    assert any(
        "progressive" in edge_id
        for edge_id in payload["review_state"]["edges"]
    )

    html = G6ReviewHtmlRenderer().render(payload=payload)
    assert "graph.data(data);" in html
    assert "graph.render();" in html
    assert "graph.changeData(data);" in html
    assert "data.layout_edges || []" in html
    assert "包含关系由章节大框嵌套表达" in html


def test_formal_quality_gate_blocks_accepted_section_with_pending_contains() -> None:
    drafts = _catalog_drafts()
    for draft in drafts:
        draft.review_status = "accepted"
        for anchor in draft.evidence_anchors:
            anchor.review_status = "accepted"
    synchronize_draft_hierarchy(drafts)

    report = GraphQualityChecker().check_drafts(drafts)
    formal = FormalGraphWorkbookBuilder().build(drafts, graph_id="DIRECT_BUILD")

    assert report.has_errors is True
    assert any(
        issue.code == "structure_relation_not_accepted"
        for issue in report.issues
    )
    assert len(formal.nodes) == 25
    assert len(formal.edges) == 0
    assert all(not node.parent_node_id for node in formal.nodes)


def test_ambiguous_catalog_parent_is_excluded_from_batch_acceptance() -> None:
    drafts = _catalog_drafts()
    conflicted_section = drafts[1]
    drafts[4].candidate_relations.append(
        _contains("chapter_2", conflicted_section.candidate_node_id)
    )

    hierarchy = project_draft_hierarchy(drafts)
    workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        drafts,
        graph_id="CATALOG_CONFLICT",
    )
    result = WorkbenchReviewSession(drafts=drafts).accept_catalog_structure(workbook)

    assert any(issue.code == "hierarchy_multiple_parents" for issue in hierarchy.issues)
    assert hierarchy.parent_for(conflicted_section.candidate_node_id) == ""
    assert conflicted_section.review_status == "pending"
    assert result.skipped_issue_count >= 1
    assert all(
        relation["review_status"] == "pending"
        for draft in drafts
        for relation in draft.candidate_relations
        if relation["target_node_id"] == conflicted_section.candidate_node_id
    )


def _catalog_drafts(*, include_progressive: bool = False) -> list[DraftKnowledgeItemDTO]:
    section_counts = [3, 3, 3, 3, 4, 3]
    drafts: list[DraftKnowledgeItemDTO] = []
    for chapter_index, section_count in enumerate(section_counts, start=1):
        chapter_id = f"chapter_{chapter_index}"
        chapter = _draft(chapter_id, f"第{chapter_index}章")
        drafts.append(chapter)
        for section_index in range(1, section_count + 1):
            section_id = f"section_{chapter_index}_{section_index}"
            chapter.candidate_relations.append(_contains(chapter_id, section_id))
            drafts.append(
                _draft(
                    section_id,
                    f"{chapter_index}.{section_index} 小节",
                )
            )
    if include_progressive:
        drafts[0].candidate_relations.append(
            {
                "source_node_id": "chapter_1",
                "target_node_id": "chapter_2",
                "relation_type": "progressive",
                "confidence": 0.5,
                "relation_evidence": "仅为目录排列顺序，保留待审查以验证不会批量通过。",
                "relation_source": "test_fixture",
                "review_status": "pending",
            }
        )
    return drafts


def _draft(node_id: str, name: str) -> DraftKnowledgeItemDTO:
    anchor = EvidenceAnchorDTO(
        anchor_id=f"anchor_{node_id}",
        anchor_text=f"目录证据：{name}",
        target_ids=[node_id],
        review_status="pending",
    )
    return DraftKnowledgeItemDTO(
        draft_id=f"draft_{node_id}",
        subject="math",
        grade="g8",
        term="term1",
        chapter=name,
        candidate_display_name=name,
        candidate_node_name=node_id,
        candidate_node_id=node_id,
        knowledge_type="container",
        source_text=anchor.anchor_text,
        evidence_anchors=[anchor],
        review_status="pending",
    )


def _contains(source_id: str, target_id: str) -> dict[str, object]:
    return {
        "source_node_id": source_id,
        "target_node_id": target_id,
        "relation_type": "contains",
        "confidence": 0.95,
        "relation_evidence": "目录版面父子结构。",
        "relation_source": "test_fixture",
        "review_status": "pending",
    }
