from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from ..contracts import DraftKnowledgeItemDTO, FormalGraphWorkbookDTO
from ..utils.graph_semantics import is_container_node_type, normalize_relation_type
from ..utils.hierarchy import project_draft_hierarchy, synchronize_draft_hierarchy
from ..utils.relation_endpoints import build_node_reference_index, resolve_node_reference
from ..utils.review_status import normalize_review_status
from ..review_document import (
    ExportProfileDTO,
    LIFECYCLE_ARCHIVED,
    P2ReviewDocumentDTO,
    RELATION_FAMILY_MEMBERSHIP,
    ReviewDecisionDTO,
    ReviewViewStateDTO,
    document_to_legacy_drafts,
)


@dataclass(slots=True)
class ReviewSessionSnapshot:
    label: str
    drafts: list[DraftKnowledgeItemDTO]
    relation_status_overrides: dict[str, str]
    graph_node_positions: dict[str, tuple[float, float]]
    g6_view_config: dict[str, object]


@dataclass(slots=True)
class StructureAcceptanceResult:
    node_count: int = 0
    relation_count: int = 0
    skipped_issue_count: int = 0
    root_node_ids: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.node_count or self.relation_count)


@dataclass(slots=True)
class WorkbenchReviewSession:
    """Mutable, UI-independent state and commands for one graph review session."""

    drafts: list[DraftKnowledgeItemDTO] = field(default_factory=list)
    relation_status_overrides: dict[str, str] = field(default_factory=dict)
    graph_node_positions: dict[str, tuple[float, float]] = field(default_factory=dict)
    g6_view_config: dict[str, object] = field(default_factory=dict)
    undo_stack: list[ReviewSessionSnapshot] = field(default_factory=list)
    max_undo_steps: int = 12

    def reset(self, drafts: list[DraftKnowledgeItemDTO] | None = None) -> None:
        self.drafts = list(drafts or [])
        self.relation_status_overrides.clear()
        self.graph_node_positions.clear()
        self.g6_view_config.clear()
        self.undo_stack.clear()

    def push_snapshot(self, label: str) -> None:
        self.undo_stack.append(
            ReviewSessionSnapshot(
                label=label,
                drafts=deepcopy(self.drafts),
                relation_status_overrides=deepcopy(self.relation_status_overrides),
                graph_node_positions=deepcopy(self.graph_node_positions),
                g6_view_config=deepcopy(self.g6_view_config),
            )
        )
        self.undo_stack = self.undo_stack[-self.max_undo_steps :]

    def undo(self) -> str | None:
        if not self.undo_stack:
            return None
        snapshot = self.undo_stack.pop()
        self.drafts = deepcopy(snapshot.drafts)
        self.relation_status_overrides = deepcopy(snapshot.relation_status_overrides)
        self.graph_node_positions = deepcopy(snapshot.graph_node_positions)
        self.g6_view_config = deepcopy(snapshot.g6_view_config)
        return snapshot.label

    @staticmethod
    def edge_status_key(source_node_id: str, relation_type: str, target_node_id: str) -> str:
        normalized_type = normalize_relation_type(relation_type)
        return f"{source_node_id}--{normalized_type}--{target_node_id}"

    def apply_status_overrides(self, workbook: FormalGraphWorkbookDTO | None) -> None:
        if workbook is None or not self.relation_status_overrides:
            return
        for edge in workbook.edges:
            key = self.edge_status_key(
                edge.source_node_id,
                edge.relation_type,
                edge.target_node_id,
            )
            if key in self.relation_status_overrides:
                edge.review_status = self.relation_status_overrides[key]

    def draft_by_node_id(self, node_id: str) -> DraftKnowledgeItemDTO | None:
        return next(
            (draft for draft in self.drafts if draft.candidate_node_id == node_id),
            None,
        )

    def sync_parent_display_names(self, parent_node_id: str, parent_name: str) -> None:
        for draft in self.drafts:
            if draft.candidate_parent_node_id == parent_node_id:
                draft.candidate_parent_name = parent_name

    def relation_details_by_edge_id(
        self,
        edge_id: str,
        *,
        workbook: FormalGraphWorkbookDTO | None = None,
    ) -> dict[str, str] | None:
        for draft in self.drafts:
            for source_id in draft.candidate_prerequisites:
                candidate_edge_id = self.edge_status_key(
                    source_id,
                    "prerequisite",
                    draft.candidate_node_id,
                )
                if candidate_edge_id == edge_id:
                    return {
                        "source": source_id,
                        "target": draft.candidate_node_id,
                        "relation_type": "prerequisite",
                        "status": self.relation_status_overrides.get(edge_id, "pending"),
                        "evidence": "",
                    }
            for relation in draft.candidate_relations:
                source = str(
                    relation.get("source_node_id") or draft.candidate_node_id
                ).strip()
                target = str(relation.get("target_node_id") or "").strip()
                relation_type = normalize_relation_type(
                    str(relation.get("relation_type") or "")
                )
                if self.edge_status_key(source, relation_type, target) == edge_id:
                    return {
                        "source": source,
                        "target": target,
                        "relation_type": relation_type,
                        "status": self.relation_status_overrides.get(
                            edge_id,
                            normalize_review_status(
                                str(relation.get("review_status") or "pending")
                            ),
                        ),
                        "evidence": str(relation.get("relation_evidence") or ""),
                    }
        if workbook is not None:
            for edge in workbook.edges:
                relation_type = normalize_relation_type(edge.relation_type)
                if self.edge_status_key(
                    edge.source_node_id,
                    relation_type,
                    edge.target_node_id,
                ) == edge_id:
                    return {
                        "source": edge.source_node_id,
                        "target": edge.target_node_id,
                        "relation_type": relation_type,
                        "status": self.relation_status_overrides.get(
                            edge_id,
                            normalize_review_status(edge.review_status),
                        ),
                        "evidence": edge.relation_evidence,
                    }
        return None

    def find_candidate_relation(
        self,
        draft: DraftKnowledgeItemDTO,
        source_node_id: str,
        relation_type: str,
        target_node_id: str,
    ) -> dict[str, object] | None:
        edge_id = self.edge_status_key(source_node_id, relation_type, target_node_id)
        for relation in draft.candidate_relations:
            source = str(
                relation.get("source_node_id") or draft.candidate_node_id
            ).strip()
            target = str(relation.get("target_node_id") or "").strip()
            relation_key = self.edge_status_key(
                source,
                str(relation.get("relation_type") or ""),
                target,
            )
            if relation_key == edge_id:
                return relation
        return None

    def add_or_update_relation(
        self,
        *,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        status: str,
        evidence: str,
    ) -> str:
        if not source_node_id or not target_node_id:
            raise ValueError("关系起点和终点不能为空。")
        if source_node_id == target_node_id:
            raise ValueError("关系起点和终点不能相同。")
        source_draft = self.draft_by_node_id(source_node_id)
        if source_draft is None:
            raise ValueError("未找到关系起点节点。")
        payload = self.manual_relation_payload(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type=relation_type,
            status=status,
            evidence=evidence,
        )
        existing = self.find_candidate_relation(
            source_draft,
            source_node_id,
            relation_type,
            target_node_id,
        )
        if existing is None:
            source_draft.candidate_relations.append(payload)
        else:
            existing.update(payload)
        edge_id = self.edge_status_key(source_node_id, relation_type, target_node_id)
        self.relation_status_overrides[edge_id] = normalize_review_status(status)
        return edge_id

    def replace_relation(
        self,
        *,
        edge_id: str,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        status: str,
        evidence: str,
    ) -> str:
        self.remove_relation_by_edge_id(edge_id)
        return self.add_or_update_relation(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type=relation_type,
            status=status,
            evidence=evidence,
        )

    @staticmethod
    def manual_relation_payload(
        *,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        status: str,
        evidence: str,
    ) -> dict[str, object]:
        return {
            "source_node_id": source_node_id,
            "target_node_id": target_node_id,
            "relation_type": normalize_relation_type(relation_type),
            "confidence": 1.0,
            "relation_evidence": evidence,
            "relation_source": "teacher_manual",
            "review_status": normalize_review_status(status),
        }

    def update_relation_status(self, edge_id: str, status: str) -> None:
        normalized = normalize_review_status(status)
        self.relation_status_overrides[edge_id] = normalized
        for draft in self.drafts:
            for relation in draft.candidate_relations:
                source = str(
                    relation.get("source_node_id") or draft.candidate_node_id
                ).strip()
                target = str(relation.get("target_node_id") or "").strip()
                relation_type = str(relation.get("relation_type") or "").strip()
                if self.edge_status_key(source, relation_type, target) == edge_id:
                    relation["review_status"] = normalized

    def remove_relation_by_edge_id(self, edge_id: str) -> bool:
        removed = False
        for draft in self.drafts:
            original_prerequisites = list(draft.candidate_prerequisites)
            draft.candidate_prerequisites = [
                source_id
                for source_id in draft.candidate_prerequisites
                if self.edge_status_key(
                    source_id,
                    "prerequisite",
                    draft.candidate_node_id,
                )
                != edge_id
            ]
            removed = removed or len(original_prerequisites) != len(
                draft.candidate_prerequisites
            )
            remaining_relations: list[dict[str, object]] = []
            for relation in draft.candidate_relations:
                source = str(
                    relation.get("source_node_id") or draft.candidate_node_id
                ).strip()
                target = str(relation.get("target_node_id") or "").strip()
                relation_type = str(relation.get("relation_type") or "").strip()
                if self.edge_status_key(source, relation_type, target) == edge_id:
                    removed = True
                    continue
                remaining_relations.append(relation)
            draft.candidate_relations = remaining_relations
        self.relation_status_overrides.pop(edge_id, None)
        return removed

    def count_relations_for_node(self, node_id: str) -> int:
        count = 0
        for draft in self.drafts:
            count += sum(item == node_id for item in draft.candidate_prerequisites)
            for relation in draft.candidate_relations:
                source = str(
                    relation.get("source_node_id") or draft.candidate_node_id
                ).strip()
                target = str(relation.get("target_node_id") or "").strip()
                if source == node_id or target == node_id:
                    count += 1
        return count

    def delete_node(self, node_id: str) -> bool:
        original_count = len(self.drafts)
        self.drafts = [
            draft for draft in self.drafts if draft.candidate_node_id != node_id
        ]
        if len(self.drafts) == original_count:
            return False
        self.cleanup_node_references(node_id)
        return True

    def cleanup_node_references(self, node_id: str) -> None:
        for draft in self.drafts:
            if draft.candidate_parent_node_id == node_id:
                draft.candidate_parent_node_id = ""
                draft.candidate_parent_name = ""
            draft.candidate_prerequisites = [
                item for item in draft.candidate_prerequisites if item != node_id
            ]
            draft.candidate_relations = [
                relation
                for relation in draft.candidate_relations
                if str(
                    relation.get("source_node_id") or draft.candidate_node_id
                ).strip()
                != node_id
                and str(relation.get("target_node_id") or "").strip() != node_id
            ]
        self.relation_status_overrides = {
            key: status
            for key, status in self.relation_status_overrides.items()
            if f"{node_id}--" not in key and f"--{node_id}" not in key
        }
        self.graph_node_positions.pop(node_id, None)
        manual_positions = dict(self.g6_view_config.get("manual_positions", {}))
        manual_positions.pop(node_id, None)
        if "manual_positions" in self.g6_view_config:
            self.g6_view_config["manual_positions"] = manual_positions

    def rejected_relation_edge_ids(
        self,
        workbook: FormalGraphWorkbookDTO | None,
    ) -> list[str]:
        if workbook is None:
            return []
        edge_ids: list[str] = []
        for edge in workbook.edges:
            edge_id = self.edge_status_key(
                edge.source_node_id,
                edge.relation_type,
                edge.target_node_id,
            )
            status = self.relation_status_overrides.get(
                edge_id,
                normalize_review_status(edge.review_status),
            )
            if status == "rejected":
                edge_ids.append(edge_id)
        return edge_ids

    def pending_node_count(self) -> int:
        return sum(
            normalize_review_status(draft.review_status) == "pending"
            for draft in self.drafts
        )

    def pending_relation_count(self, workbook: FormalGraphWorkbookDTO | None) -> int:
        if workbook is None:
            return 0
        count = 0
        for edge in workbook.edges:
            edge_id = self.edge_status_key(
                edge.source_node_id,
                edge.relation_type,
                edge.target_node_id,
            )
            status = self.relation_status_overrides.get(
                edge_id,
                normalize_review_status(edge.review_status),
            )
            if status == "pending":
                count += 1
        return count

    def revision_node_count(self) -> int:
        return sum(
            normalize_review_status(draft.review_status) == "needs_revision"
            for draft in self.drafts
        )

    def revision_relation_count(self, workbook: FormalGraphWorkbookDTO | None) -> int:
        if workbook is None:
            return 0
        count = 0
        for edge in workbook.edges:
            edge_id = self.edge_status_key(
                edge.source_node_id,
                edge.relation_type,
                edge.target_node_id,
            )
            status = self.relation_status_overrides.get(
                edge_id,
                normalize_review_status(edge.review_status),
            )
            if status == "needs_revision":
                count += 1
        return count

    def accept_pending_nodes(self) -> int:
        count = 0
        for draft in self.drafts:
            if normalize_review_status(draft.review_status) != "pending":
                continue
            draft.review_status = "accepted"
            for anchor in draft.evidence_anchors:
                if normalize_review_status(anchor.review_status) == "pending":
                    anchor.review_status = "accepted"
            count += 1
        return count

    def accept_pending_relations(
        self,
        workbook: FormalGraphWorkbookDTO | None,
    ) -> int:
        if workbook is None:
            return 0
        count = 0
        for edge in workbook.edges:
            edge_id = self.edge_status_key(
                edge.source_node_id,
                edge.relation_type,
                edge.target_node_id,
            )
            status = self.relation_status_overrides.get(
                edge_id,
                normalize_review_status(edge.review_status),
            )
            if status != "pending":
                continue
            self.update_relation_status(edge_id, "accepted")
            edge.review_status = "accepted"
            count += 1
        return count

    def pending_structure_relation_count(
        self,
        workbook: FormalGraphWorkbookDTO | None,
    ) -> int:
        if workbook is None:
            return 0
        count = 0
        for edge in workbook.edges:
            if normalize_relation_type(edge.relation_type) != "contains":
                continue
            edge_id = self.edge_status_key(
                edge.source_node_id,
                edge.relation_type,
                edge.target_node_id,
            )
            status = self.relation_status_overrides.get(
                edge_id,
                normalize_review_status(edge.review_status),
            )
            if status == "pending":
                count += 1
        return count

    def accept_catalog_structure(
        self,
        workbook: FormalGraphWorkbookDTO | None,
        *,
        selected_node_id: str = "",
    ) -> StructureAcceptanceResult:
        synchronize_draft_hierarchy(self.drafts)
        hierarchy = project_draft_hierarchy(self.drafts)
        draft_by_id = {
            draft.candidate_node_id: draft
            for draft in self.drafts
        }
        structural_ids = {
            node_id
            for node_id, draft in draft_by_id.items()
            if is_container_node_type(draft.knowledge_type)
        }
        blocked_ids: set[str] = set()
        for issue in hierarchy.issues:
            if issue.node_id:
                blocked_ids.add(issue.node_id)
            if issue.code == "hierarchy_cycle":
                blocked_ids.update(issue.parent_ids)

        if selected_node_id:
            if selected_node_id not in draft_by_id:
                return StructureAcceptanceResult(
                    skipped_issue_count=len(hierarchy.issues),
                )
            root_id = hierarchy.root_for(selected_node_id)
            target_ids = {root_id, *hierarchy.descendants_of(root_id)}
            root_ids = [root_id]
        else:
            root_ids = [
                root_id
                for root_id in hierarchy.root_node_ids
                if hierarchy.children_of(root_id)
            ]
            target_ids = {
                node_id
                for root_id in root_ids
                for node_id in [root_id, *hierarchy.descendants_of(root_id)]
            }

        target_ids &= structural_ids
        target_ids -= blocked_ids
        result = StructureAcceptanceResult(
            skipped_issue_count=len(hierarchy.issues),
            root_node_ids=[root_id for root_id in root_ids if root_id in target_ids],
        )
        for node_id in target_ids:
            draft = draft_by_id[node_id]
            if normalize_review_status(draft.review_status) != "pending":
                continue
            draft.review_status = "accepted"
            for anchor in draft.evidence_anchors:
                if normalize_review_status(anchor.review_status) == "pending":
                    anchor.review_status = "accepted"
            result.node_count += 1

        reference_index = build_node_reference_index(self.drafts)
        accepted_pairs = {
            (parent_id, child_id)
            for child_id, parent_id in hierarchy.parent_by_child.items()
            if parent_id in target_ids and child_id in target_ids
        }
        explicit_pairs: set[tuple[str, str]] = set()
        for draft in self.drafts:
            for relation in draft.candidate_relations:
                if normalize_relation_type(relation.get("relation_type")) != "contains":
                    continue
                source_id = resolve_node_reference(
                    relation.get("source_node_id") or draft.candidate_node_id,
                    reference_index,
                    default=draft.candidate_node_id,
                )
                target_id = resolve_node_reference(
                    relation.get("target_node_id"),
                    reference_index,
                )
                explicit_pairs.add((source_id, target_id))
                if (source_id, target_id) not in accepted_pairs:
                    continue
                if normalize_review_status(relation.get("review_status", "pending")) != "pending":
                    continue
                relation["review_status"] = "accepted"
                edge_id = self.edge_status_key(source_id, "contains", target_id)
                self.relation_status_overrides[edge_id] = "accepted"
                if workbook is not None:
                    for edge in workbook.edges:
                        if self.edge_status_key(
                            edge.source_node_id,
                            edge.relation_type,
                            edge.target_node_id,
                        ) == edge_id:
                            edge.review_status = "accepted"
                result.relation_count += 1
        for source_id, target_id in accepted_pairs - explicit_pairs:
            if (
                normalize_review_status(draft_by_id[source_id].review_status) != "accepted"
                or normalize_review_status(draft_by_id[target_id].review_status) != "accepted"
            ):
                continue
            edge_id = self.edge_status_key(source_id, "contains", target_id)
            self.relation_status_overrides[edge_id] = "accepted"
            if workbook is not None:
                for edge in workbook.edges:
                    if self.edge_status_key(
                        edge.source_node_id,
                        edge.relation_type,
                        edge.target_node_id,
                    ) == edge_id:
                        edge.review_status = "accepted"
            result.relation_count += 1
        synchronize_draft_hierarchy(self.drafts)
        return result


class StaleReviewCommandError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ReviewDocumentCommand:
    action: str
    object_type: str
    object_id: str
    changes: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ReviewCommandResult:
    changed: bool
    revision: int
    decision_id: str = ""


class ReviewDocumentSession:
    """Authoritative schema-v2 session; callers only receive detached snapshots."""

    _NODE_FIELDS = {
        "display_name",
        "node_name",
        "node_type",
        "cognitive_level",
        "subject_tags",
        "review_status",
        "review_notes",
        "lifecycle_state",
    }
    _RELATION_FIELDS = {
        "relation_type",
        "review_status",
        "reasoning_summary",
        "confidence",
        "proposed_relation_type",
        "lifecycle_state",
    }
    _SCOPE_FIELDS = {"structural_role", "reading_order", "review_status", "lifecycle_state"}

    def __init__(self, document: P2ReviewDocumentDTO, *, max_undo_steps: int = 24) -> None:
        errors = document.validate()
        if errors:
            raise ValueError("不能打开无效审查文档：" + ", ".join(errors[:12]))
        self._document = deepcopy(document)
        self._undo_stack: list[P2ReviewDocumentDTO] = []
        self._max_undo_steps = max_undo_steps

    @property
    def revision(self) -> int:
        return self._document.metadata.revision

    def snapshot(self) -> P2ReviewDocumentDTO:
        return deepcopy(self._document)

    def draft_rows(self) -> list[DraftKnowledgeItemDTO]:
        return document_to_legacy_drafts(self._document)

    def execute(
        self,
        command: ReviewDocumentCommand,
        *,
        expected_revision: int,
    ) -> ReviewCommandResult:
        if expected_revision != self.revision:
            raise StaleReviewCommandError(
                f"命令基于 revision {expected_revision}，当前为 {self.revision}。"
            )
        candidate = deepcopy(self._document)
        before: dict[str, Any]
        after: dict[str, Any]
        changed = False
        if command.object_type == "node":
            target = candidate.node_by_id(command.object_id)
            if target is None:
                raise ValueError("未找到节点。")
            before = asdict(target)
            changed = self._apply_fields(target, command.changes, self._NODE_FIELDS)
            content_fields = set(command.changes) - {"review_status", "review_notes", "lifecycle_state"}
            if changed and content_fields and "review_status" not in command.changes:
                target.review_status = "needs_revision"
            if changed:
                target.review_status = normalize_review_status(target.review_status)
                target.revision += 1
            after = asdict(target)
        elif command.object_type == "relation":
            target = candidate.relation_by_id(command.object_id)
            if target is None:
                raise ValueError("未找到关系。")
            before = asdict(target)
            changed = self._apply_fields(target, command.changes, self._RELATION_FIELDS)
            content_fields = set(command.changes) - {"review_status", "lifecycle_state"}
            if changed and content_fields and "review_status" not in command.changes:
                target.review_status = "needs_revision"
            if changed:
                target.review_status = normalize_review_status(target.review_status)
                target.revision += 1
            after = asdict(target)
        elif command.object_type == "scope":
            target = next(
                (item for item in candidate.scopes if item.scope_id == command.object_id), None
            )
            if target is None:
                raise ValueError("未找到教材结构对象。")
            before = asdict(target)
            changed = self._apply_fields(target, command.changes, self._SCOPE_FIELDS)
            if changed:
                target.review_status = normalize_review_status(target.review_status)
                target.revision += 1
            after = asdict(target)
        elif command.object_type == "profile":
            before, after, changed = self._apply_profile(candidate, command)
        else:
            raise ValueError(f"不支持的命令对象类型：{command.object_type}")
        if not changed:
            return ReviewCommandResult(False, self.revision)
        errors = candidate.validate()
        if errors:
            raise ValueError("命令会破坏审查文档完整性：" + ", ".join(errors[:12]))
        self._undo_stack.append(deepcopy(self._document))
        self._undo_stack = self._undo_stack[-self._max_undo_steps :]
        decision = self._commit_decision(
            candidate,
            command=command,
            before=before,
            after=after,
        )
        self._document = candidate
        return ReviewCommandResult(True, self.revision, decision.decision_id)

    def archive_node(
        self,
        node_id: str,
        *,
        expected_revision: int,
        reason: str = "",
    ) -> ReviewCommandResult:
        return self.archive_nodes(
            [node_id],
            expected_revision=expected_revision,
            reason=reason,
        )

    def archive_nodes(
        self,
        node_ids: list[str],
        *,
        expected_revision: int,
        reason: str = "",
    ) -> ReviewCommandResult:
        if expected_revision != self.revision:
            raise StaleReviewCommandError("归档命令已过期。")
        requested_ids = list(dict.fromkeys(str(item).strip() for item in node_ids if str(item).strip()))
        if not requested_ids:
            return ReviewCommandResult(False, self.revision)
        candidate = deepcopy(self._document)
        nodes = [candidate.node_by_id(node_id) for node_id in requested_ids]
        missing_ids = [node_id for node_id, node in zip(requested_ids, nodes) if node is None]
        if missing_ids:
            raise ValueError("未找到节点：" + ", ".join(missing_ids))
        active_nodes = [node for node in nodes if node is not None and node.lifecycle_state != LIFECYCLE_ARCHIVED]
        if not active_nodes:
            return ReviewCommandResult(False, self.revision)
        archived_ids = {node.node_id for node in active_nodes}
        before = {
            "nodes": [asdict(node) for node in active_nodes],
            "scope_ids": [],
            "occurrence_ids": [],
            "relation_ids": [],
        }
        for node in active_nodes:
            node.lifecycle_state = LIFECYCLE_ARCHIVED
            node.revision += 1
        for scope in candidate.scopes:
            if scope.scope_id in archived_ids and scope.lifecycle_state != LIFECYCLE_ARCHIVED:
                scope.lifecycle_state = LIFECYCLE_ARCHIVED
                scope.revision += 1
                before["scope_ids"].append(scope.scope_id)
        for occurrence in candidate.occurrences:
            if occurrence.node_id in archived_ids and occurrence.lifecycle_state != LIFECYCLE_ARCHIVED:
                occurrence.lifecycle_state = LIFECYCLE_ARCHIVED
                before["occurrence_ids"].append(occurrence.occurrence_id)
        for relation in candidate.relations:
            if (
                relation.lifecycle_state != LIFECYCLE_ARCHIVED
                and archived_ids.intersection({relation.source_node_id, relation.target_node_id})
            ):
                relation.lifecycle_state = LIFECYCLE_ARCHIVED
                relation.revision += 1
                before["relation_ids"].append(relation.relation_id)
        errors = candidate.validate()
        if errors:
            raise ValueError("归档会破坏审查文档完整性：" + ", ".join(errors[:12]))
        object_type = "node" if len(requested_ids) == 1 else "document"
        object_id = requested_ids[0] if len(requested_ids) == 1 else candidate.metadata.document_id
        command = ReviewDocumentCommand("archive", object_type, object_id, reason=reason)
        self._undo_stack.append(deepcopy(self._document))
        self._undo_stack = self._undo_stack[-self._max_undo_steps :]
        decision = self._commit_decision(
            candidate,
            command=command,
            before=before,
            after={"node_ids": sorted(archived_ids), "lifecycle_state": LIFECYCLE_ARCHIVED},
        )
        self._document = candidate
        return ReviewCommandResult(True, self.revision, decision.decision_id)

    def archive_relations(
        self,
        relation_ids: list[str],
        *,
        expected_revision: int,
        reason: str = "",
    ) -> ReviewCommandResult:
        if expected_revision != self.revision:
            raise StaleReviewCommandError("归档命令已过期。")
        requested_ids = list(
            dict.fromkeys(str(item).strip() for item in relation_ids if str(item).strip())
        )
        if not requested_ids:
            return ReviewCommandResult(False, self.revision)
        candidate = deepcopy(self._document)
        relations = [candidate.relation_by_id(relation_id) for relation_id in requested_ids]
        missing_ids = [
            relation_id
            for relation_id, relation in zip(requested_ids, relations)
            if relation is None
        ]
        if missing_ids:
            raise ValueError("未找到关系：" + ", ".join(missing_ids))
        active_relations = [
            relation
            for relation in relations
            if relation is not None and relation.lifecycle_state != LIFECYCLE_ARCHIVED
        ]
        if not active_relations:
            return ReviewCommandResult(False, self.revision)
        before = {"relations": [asdict(relation) for relation in active_relations]}
        for relation in active_relations:
            relation.lifecycle_state = LIFECYCLE_ARCHIVED
            relation.revision += 1
        errors = candidate.validate()
        if errors:
            raise ValueError("归档会破坏审查文档完整性：" + ", ".join(errors[:12]))
        archived_ids = sorted(relation.relation_id for relation in active_relations)
        object_type = "relation" if len(requested_ids) == 1 else "document"
        object_id = requested_ids[0] if len(requested_ids) == 1 else candidate.metadata.document_id
        command = ReviewDocumentCommand("archive", object_type, object_id, reason=reason)
        self._undo_stack.append(deepcopy(self._document))
        self._undo_stack = self._undo_stack[-self._max_undo_steps :]
        decision = self._commit_decision(
            candidate,
            command=command,
            before=before,
            after={"relation_ids": archived_ids, "lifecycle_state": LIFECYCLE_ARCHIVED},
        )
        self._document = candidate
        return ReviewCommandResult(True, self.revision, decision.decision_id)

    def replace_document(
        self,
        candidate_document: P2ReviewDocumentDTO,
        *,
        expected_revision: int,
        action: str = "replace_document",
        reason: str = "",
    ) -> ReviewCommandResult:
        """Atomically commit a detached compatibility edit as one document command."""

        if expected_revision != self.revision:
            raise StaleReviewCommandError("文档替换命令已过期。")
        candidate = deepcopy(candidate_document)
        if candidate.metadata.document_id != self._document.metadata.document_id:
            raise ValueError("替换文档的 document_id 与当前会话不一致。")
        candidate.metadata.revision = self.revision
        candidate.metadata.created_at = self._document.metadata.created_at
        candidate.metadata.updated_at = self._document.metadata.updated_at
        before_fingerprint = self._document.business_fingerprint()
        after_fingerprint = candidate.business_fingerprint()
        if after_fingerprint == before_fingerprint:
            return ReviewCommandResult(False, self.revision)
        errors = candidate.validate()
        if errors:
            raise ValueError("替换会破坏审查文档完整性：" + ", ".join(errors[:12]))
        command = ReviewDocumentCommand(
            action=action,
            object_type="document",
            object_id=candidate.metadata.document_id,
            reason=reason,
        )
        self._undo_stack.append(deepcopy(self._document))
        self._undo_stack = self._undo_stack[-self._max_undo_steps :]
        decision = self._commit_decision(
            candidate,
            command=command,
            before={"business_fingerprint": before_fingerprint},
            after={"business_fingerprint": after_fingerprint},
        )
        self._document = candidate
        return ReviewCommandResult(True, self.revision, decision.decision_id)

    def set_view_state(self, state: ReviewViewStateDTO) -> None:
        """Save presentation state without changing the business revision/fingerprint."""

        existing = next(
            (
                item
                for item in self._document.view_states
                if item.renderer == state.renderer and item.view_mode == state.view_mode
            ),
            None,
        )
        state_copy = deepcopy(state)
        if existing is None:
            self._document.view_states.append(state_copy)
        else:
            self._document.view_states[self._document.view_states.index(existing)] = state_copy

    def undo(self, *, expected_revision: int) -> ReviewCommandResult:
        if expected_revision != self.revision:
            raise StaleReviewCommandError("撤销命令已过期。")
        if not self._undo_stack:
            return ReviewCommandResult(False, self.revision)
        current = self._document
        restored = self._undo_stack.pop()
        before_fingerprint = current.business_fingerprint()
        preserved_history = deepcopy(current.review_history)
        restored.metadata.revision = current.metadata.revision + 1
        restored.metadata.updated_at = _now()
        decision = ReviewDecisionDTO(
            decision_id=f"decision_{uuid4().hex}",
            object_type="document",
            object_id=current.metadata.document_id,
            object_revision=restored.metadata.revision,
            action="undo",
            before={"business_fingerprint": before_fingerprint},
            after={"business_fingerprint": restored.business_fingerprint()},
            created_at=_now(),
        )
        restored.review_history = preserved_history + [decision]
        self._document = restored
        return ReviewCommandResult(True, self.revision, decision.decision_id)

    @staticmethod
    def _apply_fields(target: object, changes: dict[str, Any], allowed: set[str]) -> bool:
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError("命令包含不可编辑字段：" + ", ".join(sorted(unknown)))
        changed = False
        for key, value in changes.items():
            if getattr(target, key) != value:
                setattr(target, key, deepcopy(value))
                changed = True
        return changed

    @staticmethod
    def _apply_profile(
        document: P2ReviewDocumentDTO,
        command: ReviewDocumentCommand,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        profile = document.profile_by_id(command.object_id)
        if profile is None:
            profile = ExportProfileDTO(profile_id=command.object_id)
            document.export_profiles.append(profile)
            before: dict[str, Any] = {}
        else:
            before = asdict(profile)
        allowed = {
            "target_protocol",
            "selected_node_ids",
            "selected_relation_ids",
            "primary_membership_by_node",
        }
        changed = ReviewDocumentSession._apply_fields(profile, command.changes, allowed)
        if not before:
            changed = True
        if changed:
            profile.based_on_revision = document.metadata.revision + 1
        return before, asdict(profile), changed

    @staticmethod
    def _commit_decision(
        document: P2ReviewDocumentDTO,
        *,
        command: ReviewDocumentCommand,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> ReviewDecisionDTO:
        document.metadata.revision += 1
        document.metadata.updated_at = _now()
        decision = ReviewDecisionDTO(
            decision_id=f"decision_{uuid4().hex}",
            object_type=command.object_type,
            object_id=command.object_id,
            object_revision=document.metadata.revision,
            action=command.action,
            before=before,
            after=after,
            reason=command.reason,
            created_at=_now(),
        )
        document.review_history.append(decision)
        return decision


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
