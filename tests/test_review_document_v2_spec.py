from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from textbook_builder.contracts import DraftKnowledgeItemDTO
from textbook_builder.exporters import FormalGraphWorkbookBuilder
from textbook_builder.geometry import RenderMetricsProfile, layout_scene
from textbook_builder.review_document import (
    ExportProfileDTO,
    P2ReviewDocumentDTO,
    RELATION_FAMILY_MEMBERSHIP,
    RELATION_FAMILY_SEMANTIC,
    RELATION_FAMILY_STRUCTURE,
    ReviewDocumentMetadataDTO,
    ReviewDocumentMigrator,
    ReviewNodeDTO,
    ReviewRelationDTO,
    ReviewViewStateDTO,
    StructuralScopeDTO,
)
from textbook_builder.review_document_io import ReviewDocumentExporter, ReviewDocumentReader
from textbook_builder.review_document_merge import (
    ExcelImportPlan,
    ExcelImportPlanner,
    ImportConflict,
)
from textbook_builder.review_views import (
    GraphReviewPayloadBuilder,
    ReviewProjectionBuilder,
    VIEW_MODE_RELATIONS,
    VIEW_MODE_TEXTBOOK,
)
from textbook_builder.normalizers.node_canonicalizer import NodeCanonicalizer
from textbook_builder.pipeline_contracts import LocalNodeCandidateDTO
from textbook_builder.services.publish_planning import PublishPlanningService
from textbook_builder.services.workbench_session import (
    ReviewDocumentCommand,
    ReviewDocumentSession,
    StaleReviewCommandError,
)


def _document(*, accepted: bool = False, multi_membership: bool = False) -> P2ReviewDocumentDTO:
    status = "accepted" if accepted else "pending"
    nodes = [
        ReviewNodeDTO("chapter", "第13章", "chapter-13", "container", review_status=status),
        ReviewNodeDTO("section", "13.1 三角形", "section-13-1", "container", review_status=status),
        ReviewNodeDTO("subsection", "13.1.1 定义", "section-13-1-1", "container", review_status=status),
        ReviewNodeDTO("a", "三角形", "triangle", review_status=status),
        ReviewNodeDTO("b", "内角", "interior-angle", "property", review_status=status),
        ReviewNodeDTO("c", "顶点", "vertex", "property", review_status=status),
    ]
    scopes = [
        StructuralScopeDTO("chapter", "book", "chapter", "13", 1, review_status=status),
        StructuralScopeDTO("section", "book", "section", "13.1", 2, review_status=status),
        StructuralScopeDTO("subsection", "book", "subsection", "13.1.1", 3, review_status=status),
    ]
    relations = [
        ReviewRelationDTO("s1", RELATION_FAMILY_STRUCTURE, "chapter", "section", "contains", review_status=status),
        ReviewRelationDTO("s2", RELATION_FAMILY_STRUCTURE, "section", "subsection", "contains", review_status=status),
        ReviewRelationDTO("m1", RELATION_FAMILY_MEMBERSHIP, "subsection", "a", "contains", review_status=status),
        ReviewRelationDTO("m2", RELATION_FAMILY_MEMBERSHIP, "subsection", "b", "contains", review_status=status),
        ReviewRelationDTO("m3", RELATION_FAMILY_MEMBERSHIP, "subsection", "c", "contains", review_status=status),
        ReviewRelationDTO("r1", RELATION_FAMILY_SEMANTIC, "a", "b", "prerequisite", review_status=status),
        ReviewRelationDTO("r2", RELATION_FAMILY_SEMANTIC, "b", "c", "progressive", review_status=status),
    ]
    if multi_membership:
        nodes.append(ReviewNodeDTO("section2", "13.2 证明", "section-13-2", "container", review_status=status))
        scopes.append(StructuralScopeDTO("section2", "book", "section", "13.2", 4, review_status=status))
        relations.extend(
            [
                ReviewRelationDTO("s3", RELATION_FAMILY_STRUCTURE, "chapter", "section2", "contains", review_status=status),
                ReviewRelationDTO("m4", RELATION_FAMILY_MEMBERSHIP, "section2", "a", "contains", review_status=status),
            ]
        )
    return P2ReviewDocumentDTO(
        metadata=ReviewDocumentMetadataDTO("doc", "book"),
        nodes=nodes,
        scopes=scopes,
        relations=relations,
    )


def test_migration_separates_structure_membership_and_unresolved_contains() -> None:
    drafts = [
        DraftKnowledgeItemDTO("d1", "math", "g8", "t1", "13", "第13章", "chapter", "chapter", knowledge_type="container", review_status="accepted"),
        DraftKnowledgeItemDTO("d2", "math", "g8", "t1", "13", "13.1", "section", "section", candidate_parent_node_id="chapter", knowledge_type="container", review_status="accepted"),
        DraftKnowledgeItemDTO("d3", "math", "g8", "t1", "13", "三角形", "triangle", "a", section="13.1", candidate_parent_node_id="section", review_status="needs_expert_review"),
        DraftKnowledgeItemDTO(
            "d4", "math", "g8", "t1", "13", "内角", "angle", "b", section="13.1",
            candidate_relations=[{"source_node_id": "a", "target_node_id": "b", "relation_type": "contains", "review_status": "pending"}],
        ),
    ]
    document = ReviewDocumentMigrator().migrate(drafts)
    families = {item.relation_id: item.relation_family for item in document.relations}
    assert sum(value == RELATION_FAMILY_STRUCTURE for value in families.values()) == 1
    assert sum(value == RELATION_FAMILY_MEMBERSHIP for value in families.values()) == 2
    assert any(value == "unresolved" for value in families.values())
    assert document.node_by_id("a").review_status == "needs_expert_review"


def test_canonicalization_keeps_each_cross_section_occurrence() -> None:
    nodes, _mappings = NodeCanonicalizer().canonicalize(
        [
            LocalNodeCandidateDTO("tmp-1", "三角形", chapter="13", section="13.1"),
            LocalNodeCandidateDTO("tmp-2", "三角形", chapter="13", section="13.3"),
        ],
        subject="math",
        grade="g8",
        term="term1",
    )
    assert len(nodes) == 1
    assert {item["section"] for item in nodes[0].occurrences} == {"13.1", "13.3"}


def test_review_document_round_trip_is_lossless(tmp_path: Path) -> None:
    document = _document(multi_membership=True)
    document.nodes[-1].subject_tags = ["math", "geometry"]
    document.relations[-1].confidence = 0.73
    document.view_states = [
        ReviewViewStateDTO(
            renderer="g6:relations",
            view_mode="relations",
            hidden_view_ids=["node:a@relations"],
        )
    ]
    path = tmp_path / "review-v2.xlsx"
    base_id = ReviewDocumentExporter().export_review_document(document, path)
    restored = ReviewDocumentReader().read_document(path)
    assert base_id
    assert restored.business_fingerprint() == document.business_fingerprint()
    assert restored.nodes[-1].subject_tags == ["math", "geometry"]
    assert restored.relations[-1].confidence == pytest.approx(0.73)
    assert restored.view_states[0].view_mode == "relations"
    assert restored.view_states[0].layout_version == 6


def test_view_states_are_independent_per_renderer_and_reading_mode() -> None:
    session = ReviewDocumentSession(_document(accepted=True))
    session.set_view_state(
        ReviewViewStateDTO(renderer="g6", view_mode="relations", focused_scope_id="relations")
    )
    session.set_view_state(
        ReviewViewStateDTO(renderer="g6", view_mode="textbook", focused_scope_id="textbook")
    )

    states = {(item.renderer, item.view_mode): item for item in session.snapshot().view_states}

    assert states[("g6", "relations")].focused_scope_id == "relations"
    assert states[("g6", "textbook")].focused_scope_id == "textbook"


def test_projection_and_recursive_geometry_are_deterministic_and_contained() -> None:
    document = _document()
    projection = ReviewProjectionBuilder().build(document, view_mode=VIEW_MODE_TEXTBOOK)
    geometry = layout_scene(projection, RenderMetricsProfile(renderer="qt"))
    reversed_document = deepcopy(document)
    reversed_document.nodes.reverse()
    reversed_document.scopes.reverse()
    reversed_document.relations.reverse()
    reversed_geometry = layout_scene(
        ReviewProjectionBuilder().build(reversed_document, view_mode=VIEW_MODE_TEXTBOOK),
        RenderMetricsProfile(renderer="qt"),
    )
    assert geometry.node_rects == reversed_geometry.node_rects
    assert geometry.scope_rects == reversed_geometry.scope_rects
    assert not [item for item in geometry.issues if item.code.startswith("geometry_")]
    for instance in projection.node_instances:
        assert geometry.scope_content_rects[f"scope:{instance.scope_id}"].contains(
            geometry.node_rects[instance.view_id], tolerance=1.0
        )
    assert {edge.constraint_status for edge in geometry.edge_instances} == {"rank_enforced"}


def test_projection_uses_one_line_for_multi_membership_without_cartesian_edges() -> None:
    document = _document(multi_membership=True)
    projection = ReviewProjectionBuilder().build(document, view_mode=VIEW_MODE_TEXTBOOK)
    assert len([item for item in projection.node_instances if item.node_id == "a"]) == 2
    assert len([item for item in projection.edge_instances if item.relation_id == "r1"]) == 1
    assert len({item.node_id for item in projection.node_instances}) == 3


def test_g6_payload_consumes_shared_view_ids_and_geometry() -> None:
    projection = ReviewProjectionBuilder().build(_document(), view_mode=VIEW_MODE_TEXTBOOK)
    geometry = layout_scene(projection, RenderMetricsProfile(renderer="g6"))
    payload = GraphReviewPayloadBuilder().build_from_projection(projection=projection, geometry=geometry)
    assert len(payload["combos"]) == 3
    assert len(payload["nodes"]) == 3
    assert len(payload["edges"]) == 2
    assert all(item["id"].startswith("node:") for item in payload["nodes"])
    assert all(item["layoutScope"] == "subsection" for item in payload["edges"])


def test_relations_projection_uses_one_card_per_business_node_and_context_ledger() -> None:
    document = _document(multi_membership=True)
    projection = ReviewProjectionBuilder().build(document, view_mode=VIEW_MODE_RELATIONS)
    a_instances = [item for item in projection.node_instances if item.node_id == "a"]
    assert len(a_instances) == 1
    assert a_instances[0].membership_ids == ["m4", "m1"]
    assert set(a_instances[0].context_scope_ids) == {"section2", "subsection"}
    assert not projection.scopes
    assert len([item for item in projection.edge_instances if item.relation_id == "r1"]) == 1
    assert projection.relation_coverage()["context"] == 7


def test_session_revision_stale_command_content_reset_and_append_only_undo() -> None:
    session = ReviewDocumentSession(_document(accepted=True))
    result = session.execute(
        ReviewDocumentCommand("rename", "node", "a", {"display_name": "三角形（修订）"}),
        expected_revision=0,
    )
    assert result.revision == 1
    assert session.snapshot().node_by_id("a").review_status == "needs_revision"
    with pytest.raises(StaleReviewCommandError):
        session.execute(
            ReviewDocumentCommand("rename", "node", "a", {"display_name": "旧命令"}),
            expected_revision=0,
        )
    undo = session.undo(expected_revision=1)
    restored = session.snapshot()
    assert undo.revision == 2
    assert restored.node_by_id("a").display_name == "三角形"
    assert [item.action for item in restored.review_history] == ["rename", "undo"]


def test_archive_commands_keep_records_and_cascade_without_physical_deletion() -> None:
    session = ReviewDocumentSession(_document(accepted=True))
    result = session.archive_nodes(["a"], expected_revision=0, reason="test")
    archived = session.snapshot()
    assert result.revision == 1
    assert archived.node_by_id("a").lifecycle_state == "archived"
    assert archived.relation_by_id("m1").lifecycle_state == "archived"
    assert archived.relation_by_id("r1").lifecycle_state == "archived"
    assert len(archived.nodes) == 6
    assert not archived.validate()
    undo = session.undo(expected_revision=1)
    assert undo.revision == 2
    assert session.snapshot().node_by_id("a").lifecycle_state == "active"

    relation_session = ReviewDocumentSession(_document(accepted=True))
    relation_session.archive_relations(["r1", "r2"], expected_revision=0)
    archived_relations = relation_session.snapshot()
    assert len(archived_relations.relations) == 7
    assert archived_relations.relation_by_id("r1").lifecycle_state == "archived"
    assert archived_relations.relation_by_id("r2").lifecycle_state == "archived"
    assert not archived_relations.validate()


def test_compatibility_document_replace_is_one_atomic_revision() -> None:
    original = _document(accepted=True)
    session = ReviewDocumentSession(original)
    candidate = session.snapshot()
    candidate.node_by_id("a").display_name = "三角形（表格修订）"
    candidate.node_by_id("a").review_status = "needs_revision"
    result = session.replace_document(
        candidate,
        expected_revision=0,
        action="desktop_ui_transaction",
    )
    committed = session.snapshot()
    assert result.revision == 1
    assert committed.node_by_id("a").display_name == "三角形（表格修订）"
    assert committed.review_history[-1].action == "desktop_ui_transaction"
    assert original.node_by_id("a").display_name == "三角形"


def test_publish_requires_explicit_membership_then_builds_only_accepted_relations(tmp_path: Path) -> None:
    document = _document(accepted=True)
    service = PublishPlanningService()
    undecided = service.preflight(document, ExportProfileDTO("p"))
    assert undecided.status == "needs_decision"
    profile = ExportProfileDTO(
        "p",
        primary_membership_by_node={"a": "m1", "b": "m2", "c": "m3"},
    )
    document.export_profiles = [profile]
    ready = service.preflight(document, profile)
    assert ready.ready
    formal = service.build_formal_projection(document, ready).workbook
    assert {edge.relation_type for edge in formal.edges} == {"contains", "prerequisite", "progressive"}
    result = service.publish(document, ready, expected_revision=0, output_root=tmp_path)
    assert result.status == "completed"
    assert result.formal_path and result.formal_path.exists()
    assert (tmp_path / "current.json").exists()


def test_publish_plan_becomes_stale_after_business_change() -> None:
    document = _document(accepted=True)
    profile = ExportProfileDTO("p", primary_membership_by_node={"a": "m1", "b": "m2", "c": "m3"})
    document.export_profiles = [profile]
    service = PublishPlanningService()
    plan = service.preflight(document, profile)
    document.nodes[-1].display_name = "changed"
    with pytest.raises(ValueError, match="过期"):
        service.build_formal_projection(document, plan)


def test_legacy_prerequisite_list_cannot_bypass_pending_explicit_relation() -> None:
    drafts = [
        DraftKnowledgeItemDTO("d1", "math", "g8", "t1", "1", "A", "a", "a", review_status="accepted"),
        DraftKnowledgeItemDTO(
            "d2", "math", "g8", "t1", "1", "B", "b", "b",
            candidate_prerequisites=["a"],
            candidate_relations=[{"source_node_id": "a", "target_node_id": "b", "relation_type": "prerequisite", "review_status": "pending"}],
            review_status="accepted",
        ),
    ]
    formal = FormalGraphWorkbookBuilder().build(drafts, graph_id="g")
    assert formal.edges == []


def test_excel_import_three_way_merge_preserves_newer_local_edit(tmp_path: Path) -> None:
    base = _document()
    path = tmp_path / "review.xlsx"
    ReviewDocumentExporter().export_review_document(base, path)
    local = deepcopy(base)
    local.metadata.revision = 1
    local.node_by_id("a").display_name = "本地修订"
    # The unchanged exported package must not overwrite the newer local value.
    plan = ExcelImportPlanner().plan(path, local)
    assert plan.applicable
    assert not [change for change in plan.changes if change.object_id == "a" and change.field_name == "display_name"]


def test_excel_merge_rejects_a_different_review_document(tmp_path: Path) -> None:
    imported = _document()
    imported.metadata.document_id = "another-document"
    path = tmp_path / "other.xlsx"
    ReviewDocumentExporter().export_review_document(imported, path)
    plan = ExcelImportPlanner().plan(path, _document())
    assert plan.status == "blocked"
    assert "document_id" in plan.warnings[0]


def test_excel_conflict_requires_explicit_resolution_and_resets_acceptance() -> None:
    local = _document(accepted=True)
    local.metadata.revision = 1
    local.node_by_id("a").display_name = "本地修订"
    imported = deepcopy(local)
    imported.node_by_id("a").display_name = "Excel 修订"
    plan = ExcelImportPlan(
        status="conflicts",
        base_export_id="base",
        base_revision=0,
        current_revision=1,
        conflicts=[
            ImportConflict(
                "node",
                "a",
                "display_name",
                "三角形",
                "本地修订",
                "Excel 修订",
            )
        ],
        imported_document=imported,
    )
    resolved = ExcelImportPlanner.resolve_conflicts(plan, use_imported_values=True)
    merged = ExcelImportPlanner().apply(resolved, local, expected_revision=1)
    assert merged.metadata.revision == 2
    assert merged.node_by_id("a").display_name == "Excel 修订"
    assert merged.node_by_id("a").review_status == "needs_revision"
    assert plan.conflicts
