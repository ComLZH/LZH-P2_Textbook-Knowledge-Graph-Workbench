from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from ..review_document import (
    LIFECYCLE_ACTIVE,
    P2ReviewDocumentDTO,
    RELATION_FAMILY_MEMBERSHIP,
    RELATION_FAMILY_SEMANTIC,
    RELATION_FAMILY_STRUCTURE,
    RELATION_FAMILY_UNRESOLVED,
    ReviewRelationDTO,
)
from ..utils.review_status import normalize_review_status
from .reading_order import ReadingSequence, build_reading_sequences
from .relation_groups import VisualRelationGroup, build_relation_groups


PROJECTION_SCHEMA_VERSION = 2
UNASSIGNED_SCOPE_ID = "virtual:unassigned"
VIEW_MODE_RELATIONS = "relations"
VIEW_MODE_TEXTBOOK = "textbook"
ALLOWED_VIEW_MODES = {VIEW_MODE_RELATIONS, VIEW_MODE_TEXTBOOK}


@dataclass(slots=True)
class ProjectedScope:
    view_id: str
    scope_id: str
    node_id: str
    label: str
    structural_role: str
    parent_scope_id: str = ""
    reading_order: int = 0
    review_status: str = "pending"
    is_virtual: bool = False


@dataclass(slots=True)
class ProjectedNodeInstance:
    view_id: str
    node_id: str
    membership_id: str
    scope_id: str
    label: str
    node_type: str
    review_status: str
    membership_status: str = ""
    is_reference: bool = False
    is_virtual: bool = False
    membership_ids: list[str] = field(default_factory=list)
    context_scope_ids: list[str] = field(default_factory=list)
    evidence_anchor_ids: list[str] = field(default_factory=list)
    reasoning_summary: str = ""
    source_location: str = ""


@dataclass(slots=True)
class ProjectedEdgeInstance:
    view_id: str
    relation_id: str
    source_node_id: str
    target_node_id: str
    source_view_id: str
    target_view_id: str
    relation_family: str
    relation_type: str
    review_status: str
    display_class: str
    layout_scope: str = ""
    constraint_status: str = "unclassified"
    represented_relation_ids: list[str] = field(default_factory=list)
    evidence_anchor_ids: list[str] = field(default_factory=list)
    occurrence_ids: list[str] = field(default_factory=list)
    reasoning_summary: str = ""
    confidence: float = 1.0


@dataclass(slots=True)
class RepresentationLedgerEntry:
    relation_id: str
    representation: str
    view_ids: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(slots=True)
class ProjectionIssue:
    code: str
    message: str
    object_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReviewViewProjection:
    document_id: str
    document_revision: int
    projection_schema_version: int
    structure_fingerprint: str
    scopes: list[ProjectedScope]
    node_instances: list[ProjectedNodeInstance]
    edge_instances: list[ProjectedEdgeInstance]
    representation_ledger: list[RepresentationLedgerEntry]
    issues: list[ProjectionIssue] = field(default_factory=list)
    view_mode: str = VIEW_MODE_RELATIONS
    reading_sequences: list[ReadingSequence] = field(default_factory=list)
    visual_groups: list[VisualRelationGroup] = field(default_factory=list)

    def relation_coverage(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.representation_ledger:
            if not entry.relation_id:
                continue
            counts[entry.representation] = counts.get(entry.representation, 0) + 1
        return counts


class ReviewProjectionBuilder:
    def build(
        self,
        document: P2ReviewDocumentDTO,
        *,
        focused_scope_id: str = "",
        include_rejected: bool = False,
        hidden_view_ids: set[str] | None = None,
        view_mode: str = VIEW_MODE_RELATIONS,
    ) -> ReviewViewProjection:
        if view_mode not in ALLOWED_VIEW_MODES:
            raise ValueError(f"不支持的审查阅读模式：{view_mode}")
        hidden_view_ids = set(hidden_view_ids or set())
        nodes = {
            node.node_id: node
            for node in document.nodes
            if node.lifecycle_state == LIFECYCLE_ACTIVE
        }
        scope_records = {
            scope.scope_id: scope
            for scope in document.scopes
            if scope.lifecycle_state == LIFECYCLE_ACTIVE and scope.scope_id in nodes
        }
        relations = [
            relation
            for relation in document.relations
            if relation.lifecycle_state == LIFECYCLE_ACTIVE
        ]
        visible_relations = [
            relation
            for relation in relations
            if include_rejected or normalize_review_status(relation.review_status) != "rejected"
        ]
        issues: list[ProjectionIssue] = []
        parent_by_scope: dict[str, str] = {}
        for relation in visible_relations:
            if relation.relation_family != RELATION_FAMILY_STRUCTURE:
                continue
            child_id = relation.target_node_id
            parent_id = relation.source_node_id
            if parent_id not in scope_records or child_id not in scope_records:
                issues.append(
                    ProjectionIssue(
                        code="structure_projection_endpoint_missing",
                        message="结构关系端点不是有效教材结构对象。",
                        object_ids=[relation.relation_id],
                    )
                )
                continue
            previous = parent_by_scope.get(child_id)
            if previous and previous != parent_id:
                issues.append(
                    ProjectionIssue(
                        code="structure_projection_multiple_parents",
                        message="教材结构存在多个有效父级，未静默选择。",
                        object_ids=[child_id, previous, parent_id],
                    )
                )
                parent_by_scope.pop(child_id, None)
                continue
            parent_by_scope[child_id] = parent_id
        scopes = [
            ProjectedScope(
                view_id=_scope_view_id(scope.scope_id),
                scope_id=scope.scope_id,
                node_id=scope.scope_id,
                label=nodes[scope.scope_id].display_name,
                structural_role=scope.structural_role,
                parent_scope_id=parent_by_scope.get(scope.scope_id, ""),
                reading_order=scope.reading_order,
                review_status=scope.review_status,
            )
            for scope in sorted(
                scope_records.values(), key=lambda item: (item.reading_order, item.scope_id)
            )
        ]
        memberships_by_node: dict[str, list[ReviewRelationDTO]] = {}
        for relation in visible_relations:
            if relation.relation_family != RELATION_FAMILY_MEMBERSHIP:
                continue
            if relation.source_node_id not in scope_records or relation.target_node_id not in nodes:
                issues.append(
                    ProjectionIssue(
                        code="membership_projection_endpoint_missing",
                        message="教材归属端点无效。",
                        object_ids=[relation.relation_id],
                    )
                )
                continue
            memberships_by_node.setdefault(relation.target_node_id, []).append(relation)
        if view_mode == VIEW_MODE_RELATIONS:
            return self._build_relations_projection(
                document=document,
                nodes=nodes,
                scope_records=scope_records,
                relations=relations,
                visible_relations=visible_relations,
                memberships_by_node=memberships_by_node,
                include_rejected=include_rejected,
                hidden_view_ids=hidden_view_ids,
                issues=issues,
            )
        node_instances: list[ProjectedNodeInstance] = []
        for node in document.nodes:
            if node.lifecycle_state != LIFECYCLE_ACTIVE or node.node_id in scope_records:
                continue
            memberships = memberships_by_node.get(node.node_id, [])
            if not memberships:
                view_id = _node_view_id(node.node_id, "unassigned")
                node_instances.append(
                    ProjectedNodeInstance(
                        view_id=view_id,
                        node_id=node.node_id,
                        membership_id="",
                        scope_id=UNASSIGNED_SCOPE_ID,
                        label=node.display_name,
                        node_type=node.node_type,
                        review_status=node.review_status,
                        is_virtual=True,
                        evidence_anchor_ids=list(node.evidence_anchor_ids),
                        reasoning_summary=node.reasoning_summary,
                        source_location=node.source_location,
                    )
                )
                issues.append(
                    ProjectionIssue(
                        code="node_membership_unresolved",
                        message="知识对象没有有效教材归属，已放入待确认区域。",
                        object_ids=[node.node_id],
                    )
                )
                continue
            for index, membership in enumerate(
                sorted(memberships, key=lambda item: (item.source_node_id, item.relation_id))
            ):
                node_instances.append(
                    ProjectedNodeInstance(
                        view_id=_node_view_id(node.node_id, membership.relation_id),
                        node_id=node.node_id,
                        membership_id=membership.relation_id,
                        scope_id=membership.source_node_id,
                        label=node.display_name,
                        node_type=node.node_type,
                        review_status=node.review_status,
                        membership_status=membership.review_status,
                        is_reference=index > 0,
                        evidence_anchor_ids=list(node.evidence_anchor_ids),
                        reasoning_summary=node.reasoning_summary,
                        source_location=node.source_location,
                    )
                )
        if any(item.scope_id == UNASSIGNED_SCOPE_ID for item in node_instances):
            scopes.append(
                ProjectedScope(
                    view_id=_scope_view_id(UNASSIGNED_SCOPE_ID),
                    scope_id=UNASSIGNED_SCOPE_ID,
                    node_id="",
                    label="待确认归属",
                    structural_role="virtual",
                    reading_order=10**9,
                    review_status="pending",
                    is_virtual=True,
                )
            )
        instances_by_node: dict[str, list[ProjectedNodeInstance]] = {}
        for instance in node_instances:
            if instance.view_id not in hidden_view_ids:
                instances_by_node.setdefault(instance.node_id, []).append(instance)
        edge_instances: list[ProjectedEdgeInstance] = []
        ledger: list[RepresentationLedgerEntry] = []
        for relation in relations:
            status = normalize_review_status(relation.review_status)
            if status == "rejected" and not include_rejected:
                ledger.append(
                    RepresentationLedgerEntry(
                        relation_id=relation.relation_id,
                        representation="filtered",
                        reason="rejected_filter",
                    )
                )
                continue
            if relation.relation_family in {
                RELATION_FAMILY_STRUCTURE,
                RELATION_FAMILY_MEMBERSHIP,
            }:
                nested_view_ids = self._nested_view_ids(relation, instances_by_node)
                if relation.relation_family == RELATION_FAMILY_MEMBERSHIP and not nested_view_ids:
                    ledger.append(
                        RepresentationLedgerEntry(
                            relation_id=relation.relation_id,
                            representation="filtered",
                            reason="view_hidden_or_endpoint_not_visible",
                        )
                    )
                    continue
                ledger.append(
                    RepresentationLedgerEntry(
                        relation_id=relation.relation_id,
                        representation="nested",
                        view_ids=nested_view_ids,
                    )
                )
                continue
            endpoints = self._select_endpoints(
                relation,
                instances_by_node=instances_by_node,
                scope_ids=set(scope_records) | {UNASSIGNED_SCOPE_ID},
                focused_scope_id=focused_scope_id,
            )
            if endpoints is None:
                ledger.append(
                    RepresentationLedgerEntry(
                        relation_id=relation.relation_id,
                        representation="filtered",
                        reason="endpoint_not_visible",
                    )
                )
                issues.append(
                    ProjectionIssue(
                        code="semantic_projection_endpoint_missing",
                        message="语义关系没有可见端点实例。",
                        object_ids=[relation.relation_id],
                    )
                )
                continue
            source_view_id, target_view_id = endpoints
            edge_view_id = f"edge:{relation.relation_id}"
            edge_instances.append(
                ProjectedEdgeInstance(
                    view_id=edge_view_id,
                    relation_id=relation.relation_id,
                    source_node_id=relation.source_node_id,
                    target_node_id=relation.target_node_id,
                    source_view_id=source_view_id,
                    target_view_id=target_view_id,
                    relation_family=relation.relation_family,
                    relation_type=relation.relation_type,
                    review_status=relation.review_status,
                    display_class=(
                        "unresolved"
                        if relation.relation_family == RELATION_FAMILY_UNRESOLVED
                        else "semantic"
                    ),
                    evidence_anchor_ids=list(relation.evidence_anchor_ids),
                    occurrence_ids=list(relation.occurrence_ids),
                    reasoning_summary=relation.reasoning_summary,
                    confidence=relation.confidence,
                )
            )
            ledger.append(
                RepresentationLedgerEntry(
                    relation_id=relation.relation_id,
                    representation="line",
                    view_ids=[edge_view_id],
                )
            )
        for view_id in hidden_view_ids:
            if any(item.view_id == view_id for item in node_instances):
                ledger.append(
                    RepresentationLedgerEntry(
                        relation_id="",
                        representation="view_hidden",
                        view_ids=[view_id],
                        reason="user_hidden",
                    )
                )
        fingerprint = _fingerprint(
            {
                "scopes": [asdict(item) for item in scopes],
                "nodes": [asdict(item) for item in node_instances],
                "edges": [asdict(item) for item in edge_instances],
            }
        )
        return ReviewViewProjection(
            document_id=document.metadata.document_id,
            document_revision=document.metadata.revision,
            projection_schema_version=PROJECTION_SCHEMA_VERSION,
            structure_fingerprint=fingerprint,
            scopes=scopes,
            node_instances=[
                item for item in node_instances if item.view_id not in hidden_view_ids
            ],
            edge_instances=[
                item
                for item in edge_instances
                if item.source_view_id not in hidden_view_ids
                and item.target_view_id not in hidden_view_ids
            ],
            representation_ledger=ledger,
            issues=issues,
            view_mode=VIEW_MODE_TEXTBOOK,
        )

    def _build_relations_projection(
        self,
        *,
        document: P2ReviewDocumentDTO,
        nodes: dict[str, Any],
        scope_records: dict[str, Any],
        relations: list[ReviewRelationDTO],
        visible_relations: list[ReviewRelationDTO],
        memberships_by_node: dict[str, list[ReviewRelationDTO]],
        include_rejected: bool,
        hidden_view_ids: set[str],
        issues: list[ProjectionIssue],
    ) -> ReviewViewProjection:
        """Build one card per knowledge object while keeping textbook facts as context."""

        semantic_scope_ids = {
            endpoint
            for relation in visible_relations
            if relation.relation_family
            not in {RELATION_FAMILY_STRUCTURE, RELATION_FAMILY_MEMBERSHIP}
            for endpoint in (relation.source_node_id, relation.target_node_id)
            if endpoint in scope_records
        }
        node_instances: list[ProjectedNodeInstance] = []
        for node in sorted(document.nodes, key=lambda item: item.node_id):
            if node.lifecycle_state != LIFECYCLE_ACTIVE:
                continue
            if node.node_id in scope_records and node.node_id not in semantic_scope_ids:
                continue
            memberships = sorted(
                memberships_by_node.get(node.node_id, []),
                key=lambda item: (item.source_node_id, item.relation_id),
            )
            if node.node_id in scope_records:
                view_id = f"scope-endpoint:{node.node_id}@relations"
                node_type = scope_records[node.node_id].structural_role
                is_virtual = True
            else:
                view_id = _relations_node_view_id(node.node_id)
                node_type = node.node_type
                is_virtual = False
            node_instances.append(
                ProjectedNodeInstance(
                    view_id=view_id,
                    node_id=node.node_id,
                    membership_id="",
                    scope_id="",
                    label=node.display_name,
                    node_type=node_type,
                    review_status=node.review_status,
                    membership_status=_combined_membership_status(memberships),
                    is_virtual=is_virtual,
                    membership_ids=[item.relation_id for item in memberships],
                    context_scope_ids=list(dict.fromkeys(item.source_node_id for item in memberships)),
                    evidence_anchor_ids=list(node.evidence_anchor_ids),
                    reasoning_summary=node.reasoning_summary,
                    source_location=node.source_location,
                )
            )
            if not memberships and node.node_id not in scope_records:
                issues.append(
                    ProjectionIssue(
                        code="node_membership_unresolved",
                        message="知识对象没有有效教材归属；关系模式仍保留独立知识卡片。",
                        object_ids=[node.node_id],
                    )
                )

        reading_sequences = build_reading_sequences(document)
        visual_groups = build_relation_groups(
            document_id=document.metadata.document_id,
            node_instances=node_instances,
            relations=relations,
            reading_sequences=reading_sequences,
            include_rejected=include_rejected,
        )
        visible_by_node = {
            item.node_id: item for item in node_instances if item.view_id not in hidden_view_ids
        }
        edge_instances: list[ProjectedEdgeInstance] = []
        ledger: list[RepresentationLedgerEntry] = []
        for relation in sorted(relations, key=lambda item: item.relation_id):
            status = normalize_review_status(relation.review_status)
            if status == "rejected" and not include_rejected:
                ledger.append(
                    RepresentationLedgerEntry(
                        relation_id=relation.relation_id,
                        representation="filtered",
                        reason="rejected_filter",
                    )
                )
                continue
            if relation.relation_family in {
                RELATION_FAMILY_STRUCTURE,
                RELATION_FAMILY_MEMBERSHIP,
            }:
                ledger.append(
                    RepresentationLedgerEntry(
                        relation_id=relation.relation_id,
                        representation="context",
                        view_ids=[
                            visible_by_node[relation.target_node_id].view_id
                        ] if relation.target_node_id in visible_by_node else [],
                        reason="textbook_context_in_relations_mode",
                    )
                )
                continue
            source = visible_by_node.get(relation.source_node_id)
            target = visible_by_node.get(relation.target_node_id)
            if source is None or target is None:
                ledger.append(
                    RepresentationLedgerEntry(
                        relation_id=relation.relation_id,
                        representation="filtered",
                        reason="endpoint_hidden_or_missing",
                    )
                )
                issues.append(
                    ProjectionIssue(
                        code="semantic_projection_endpoint_missing",
                        message="语义关系没有可见端点实例。",
                        object_ids=[relation.relation_id],
                    )
                )
                continue
            edge_view_id = f"edge:{relation.relation_id}"
            edge_instances.append(
                ProjectedEdgeInstance(
                    view_id=edge_view_id,
                    relation_id=relation.relation_id,
                    source_node_id=relation.source_node_id,
                    target_node_id=relation.target_node_id,
                    source_view_id=source.view_id,
                    target_view_id=target.view_id,
                    relation_family=relation.relation_family,
                    relation_type=relation.relation_type,
                    review_status=relation.review_status,
                    display_class=(
                        "unresolved"
                        if relation.relation_family == RELATION_FAMILY_UNRESOLVED
                        else "semantic"
                    ),
                    evidence_anchor_ids=list(relation.evidence_anchor_ids),
                    occurrence_ids=list(relation.occurrence_ids),
                    reasoning_summary=relation.reasoning_summary,
                    confidence=relation.confidence,
                )
            )
            ledger.append(
                RepresentationLedgerEntry(
                    relation_id=relation.relation_id,
                    representation="line",
                    view_ids=[edge_view_id],
                )
            )
        for view_id in sorted(hidden_view_ids):
            if any(item.view_id == view_id for item in node_instances):
                ledger.append(
                    RepresentationLedgerEntry(
                        relation_id="",
                        representation="view_hidden",
                        view_ids=[view_id],
                        reason="user_hidden",
                    )
                )
        visible_nodes = [
            item for item in node_instances if item.view_id not in hidden_view_ids
        ]
        fingerprint = _fingerprint(
            {
                "view_mode": VIEW_MODE_RELATIONS,
                "scopes": [],
                "nodes": [asdict(item) for item in visible_nodes],
                "edges": [asdict(item) for item in edge_instances],
                "reading_sequences": [asdict(item) for item in reading_sequences],
                "visual_groups": [asdict(item) for item in visual_groups],
            }
        )
        return ReviewViewProjection(
            document_id=document.metadata.document_id,
            document_revision=document.metadata.revision,
            projection_schema_version=PROJECTION_SCHEMA_VERSION,
            structure_fingerprint=fingerprint,
            scopes=[],
            node_instances=visible_nodes,
            edge_instances=edge_instances,
            representation_ledger=ledger,
            issues=issues,
            view_mode=VIEW_MODE_RELATIONS,
            reading_sequences=reading_sequences,
            visual_groups=visual_groups,
        )

    @staticmethod
    def _nested_view_ids(
        relation: ReviewRelationDTO,
        instances_by_node: dict[str, list[ProjectedNodeInstance]],
    ) -> list[str]:
        if relation.relation_family == RELATION_FAMILY_STRUCTURE:
            return [
                _scope_view_id(relation.source_node_id),
                _scope_view_id(relation.target_node_id),
            ]
        instance = next(
            (
                item
                for item in instances_by_node.get(relation.target_node_id, [])
                if item.membership_id == relation.relation_id
            ),
            None,
        )
        return (
            [_scope_view_id(relation.source_node_id), instance.view_id]
            if instance
            else []
        )

    @staticmethod
    def _select_endpoints(
        relation: ReviewRelationDTO,
        *,
        instances_by_node: dict[str, list[ProjectedNodeInstance]],
        scope_ids: set[str],
        focused_scope_id: str,
    ) -> tuple[str, str] | None:
        def candidates(node_id: str) -> list[tuple[str, str]]:
            if node_id in scope_ids:
                return [(_scope_view_id(node_id), node_id)]
            return [
                (instance.view_id, instance.scope_id)
                for instance in instances_by_node.get(node_id, [])
            ]

        source_candidates = candidates(relation.source_node_id)
        target_candidates = candidates(relation.target_node_id)
        if not source_candidates or not target_candidates:
            return None
        same_scope = [
            (source, target)
            for source in source_candidates
            for target in target_candidates
            if source[1] == target[1]
        ]
        if focused_scope_id:
            focused = [pair for pair in same_scope if pair[0][1] == focused_scope_id]
            if focused:
                return focused[0][0][0], focused[0][1][0]
        if same_scope:
            return same_scope[0][0][0], same_scope[0][1][0]
        if focused_scope_id:
            source = next(
                (item for item in source_candidates if item[1] == focused_scope_id),
                source_candidates[0],
            )
            target = next(
                (item for item in target_candidates if item[1] == focused_scope_id),
                target_candidates[0],
            )
            return source[0], target[0]
        return source_candidates[0][0], target_candidates[0][0]


def _scope_view_id(scope_id: str) -> str:
    return f"scope:{scope_id}"


def _node_view_id(node_id: str, membership_id: str) -> str:
    return f"node:{node_id}@{membership_id}"


def _relations_node_view_id(node_id: str) -> str:
    return f"node:{node_id}@relations"


def _combined_membership_status(memberships: list[ReviewRelationDTO]) -> str:
    statuses = {normalize_review_status(item.review_status) for item in memberships}
    for status in ("needs_revision", "needs_expert_review", "pending", "accepted"):
        if status in statuses:
            return status
    return ""


def _fingerprint(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "ALLOWED_VIEW_MODES",
    "ProjectedEdgeInstance",
    "ProjectedNodeInstance",
    "ProjectedScope",
    "ProjectionIssue",
    "RepresentationLedgerEntry",
    "ReviewProjectionBuilder",
    "ReviewViewProjection",
    "ReadingSequence",
    "VisualRelationGroup",
    "UNASSIGNED_SCOPE_ID",
    "VIEW_MODE_RELATIONS",
    "VIEW_MODE_TEXTBOOK",
]
