from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..contracts import (
    DraftKnowledgeItemDTO,
    FormalEdgeDTO,
    FormalNodeDTO,
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_NEEDS_EXPERT_REVIEW,
    REVIEW_STATUS_NEEDS_REVISION,
    REVIEW_STATUS_PENDING,
)
from .graph_semantics import is_container_node_type, normalize_relation_type
from .relation_endpoints import build_node_reference_index, resolve_node_reference
from .review_status import normalize_review_status


CANDIDATE_HIERARCHY_STATUSES = {
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_REVISION,
    REVIEW_STATUS_NEEDS_EXPERT_REVIEW,
}


@dataclass(slots=True)
class HierarchyIssue:
    code: str
    message: str
    node_id: str = ""
    parent_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HierarchyProjection:
    parent_by_child: dict[str, str]
    children_by_parent: dict[str, list[str]]
    depth_by_node: dict[str, int]
    role_by_node: dict[str, str]
    root_node_ids: list[str]
    issues: list[HierarchyIssue] = field(default_factory=list)

    def parent_for(self, node_id: str) -> str:
        return self.parent_by_child.get(node_id, "")

    def children_of(self, node_id: str) -> list[str]:
        return list(self.children_by_parent.get(node_id, []))

    def root_for(self, node_id: str) -> str:
        current = node_id
        seen = {current}
        while current in self.parent_by_child:
            current = self.parent_by_child[current]
            if current in seen:
                return node_id
            seen.add(current)
        return current

    def descendants_of(self, node_id: str) -> list[str]:
        descendants: list[str] = []
        queue = list(self.children_by_parent.get(node_id, []))
        while queue:
            current = queue.pop(0)
            descendants.append(current)
            queue.extend(self.children_by_parent.get(current, []))
        return descendants


def project_draft_hierarchy(
    drafts: list[DraftKnowledgeItemDTO],
    *,
    relation_statuses: set[str] | None = None,
    included_node_ids: set[str] | None = None,
) -> HierarchyProjection:
    active_statuses = relation_statuses or CANDIDATE_HIERARCHY_STATUSES
    reference_index = build_node_reference_index(drafts)
    all_node_ids = {draft.candidate_node_id for draft in drafts}
    included = set(included_node_ids or all_node_ids)
    node_order = [
        draft.candidate_node_id
        for draft in drafts
        if draft.candidate_node_id in included
    ]
    container_ids = {
        draft.candidate_node_id
        for draft in drafts
        if draft.candidate_node_id in included
        and is_container_node_type(draft.knowledge_type)
    }
    candidate_parents: dict[str, list[str]] = defaultdict(list)
    explicit_contains_children: set[str] = set()
    issues: list[HierarchyIssue] = []

    for draft in drafts:
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
            if target_id:
                explicit_contains_children.add(target_id)
            status = normalize_review_status(relation.get("review_status", "pending"))
            if status not in active_statuses:
                continue
            if not source_id or not target_id or source_id not in all_node_ids or target_id not in all_node_ids:
                issues.append(
                    HierarchyIssue(
                        code="hierarchy_endpoint_missing",
                        node_id=target_id,
                        parent_ids=[source_id] if source_id else [],
                        message="候选包含关系的父级或子级端点不存在。",
                    )
                )
                continue
            if source_id not in included or target_id not in included:
                continue
            candidate_parents[target_id].append(source_id)

    for draft in drafts:
        child_id = draft.candidate_node_id
        if child_id not in included or child_id in explicit_contains_children:
            continue
        parent_id = resolve_node_reference(
            draft.candidate_parent_node_id,
            reference_index,
        )
        if not parent_id:
            continue
        if normalize_review_status(draft.review_status) not in active_statuses:
            continue
        if parent_id not in all_node_ids:
            issues.append(
                HierarchyIssue(
                    code="hierarchy_endpoint_missing",
                    node_id=child_id,
                    parent_ids=[parent_id],
                    message="节点父级字段引用了不存在的节点。",
                )
            )
            continue
        if parent_id in included:
            candidate_parents[child_id].append(parent_id)

    return _build_projection(
        node_order=node_order,
        container_ids=container_ids,
        candidate_parents=candidate_parents,
        issues=issues,
    )


def synchronize_draft_hierarchy(
    drafts: list[DraftKnowledgeItemDTO],
    *,
    relation_statuses: set[str] | None = None,
) -> HierarchyProjection:
    projection = project_draft_hierarchy(
        drafts,
        relation_statuses=relation_statuses,
    )
    names = {
        draft.candidate_node_id: draft.candidate_display_name
        for draft in drafts
    }
    for draft in drafts:
        parent_id = projection.parent_for(draft.candidate_node_id)
        draft.candidate_parent_node_id = parent_id
        draft.candidate_parent_name = names.get(parent_id, "") if parent_id else ""
    return projection


def project_formal_hierarchy(
    nodes: list[FormalNodeDTO],
    edges: list[FormalEdgeDTO] | None = None,
    *,
    relation_statuses: set[str] | None = None,
) -> HierarchyProjection:
    active_statuses = relation_statuses or CANDIDATE_HIERARCHY_STATUSES
    node_ids = {node.node_id for node in nodes}
    node_order = [node.node_id for node in nodes]
    container_ids = {
        node.node_id
        for node in nodes
        if is_container_node_type(node.node_type or node.knowledge_type)
    }
    candidate_parents: dict[str, list[str]] = defaultdict(list)
    explicit_contains_children: set[str] = set()
    issues: list[HierarchyIssue] = []

    for edge in edges or []:
        if normalize_relation_type(edge.relation_type) != "contains":
            continue
        explicit_contains_children.add(edge.target_node_id)
        if normalize_review_status(edge.review_status) not in active_statuses:
            continue
        if edge.source_node_id not in node_ids or edge.target_node_id not in node_ids:
            issues.append(
                HierarchyIssue(
                    code="hierarchy_endpoint_missing",
                    node_id=edge.target_node_id,
                    parent_ids=[edge.source_node_id],
                    message="包含关系的父级或子级端点不存在。",
                )
            )
            continue
        candidate_parents[edge.target_node_id].append(edge.source_node_id)

    for node in nodes:
        if node.node_id in explicit_contains_children or not node.parent_node_id:
            continue
        if node.parent_node_id not in node_ids:
            issues.append(
                HierarchyIssue(
                    code="hierarchy_endpoint_missing",
                    node_id=node.node_id,
                    parent_ids=[node.parent_node_id],
                    message="正式节点父级字段引用了不存在的节点。",
                )
            )
            continue
        candidate_parents[node.node_id].append(node.parent_node_id)

    return _build_projection(
        node_order=node_order,
        container_ids=container_ids,
        candidate_parents=candidate_parents,
        issues=issues,
    )


def _build_projection(
    *,
    node_order: list[str],
    container_ids: set[str],
    candidate_parents: dict[str, list[str]],
    issues: list[HierarchyIssue],
) -> HierarchyProjection:
    node_ids = set(node_order)
    parent_by_child: dict[str, str] = {}
    for child_id in node_order:
        parents = [
            parent_id
            for parent_id in dict.fromkeys(candidate_parents.get(child_id, []))
            if parent_id in node_ids
        ]
        if child_id in parents:
            issues.append(
                HierarchyIssue(
                    code="hierarchy_self_parent",
                    node_id=child_id,
                    parent_ids=[child_id],
                    message="结构节点不能包含自身。",
                )
            )
            parents = [parent_id for parent_id in parents if parent_id != child_id]
        if len(parents) == 1:
            parent_by_child[child_id] = parents[0]
        elif len(parents) > 1:
            issues.append(
                HierarchyIssue(
                    code="hierarchy_multiple_parents",
                    node_id=child_id,
                    parent_ids=parents,
                    message="同一结构节点存在多个候选父级，未自动选择。",
                )
            )

    cycle_groups = _find_cycles(node_order, parent_by_child)
    for cycle in cycle_groups:
        issues.append(
            HierarchyIssue(
                code="hierarchy_cycle",
                node_id=cycle[0],
                parent_ids=list(cycle),
                message="包含关系形成层级循环，循环节点未进入父级投影。",
            )
        )
        for node_id in cycle:
            parent_by_child.pop(node_id, None)

    children_by_parent: dict[str, list[str]] = defaultdict(list)
    for child_id in node_order:
        parent_id = parent_by_child.get(child_id)
        if parent_id:
            children_by_parent[parent_id].append(child_id)

    depth_by_node: dict[str, int] = {}
    for node_id in node_order:
        depth = 0
        current = node_id
        seen = {current}
        while current in parent_by_child:
            current = parent_by_child[current]
            if current in seen:
                break
            seen.add(current)
            depth += 1
        depth_by_node[node_id] = depth

    role_by_node: dict[str, str] = {}
    for node_id in node_order:
        if node_id not in container_ids:
            role_by_node[node_id] = "knowledge"
            continue
        depth = depth_by_node[node_id]
        if depth == 0:
            role_by_node[node_id] = "chapter"
        elif depth == 1:
            role_by_node[node_id] = "section"
        else:
            role_by_node[node_id] = "subsection"

    root_node_ids = [node_id for node_id in node_order if node_id not in parent_by_child]
    return HierarchyProjection(
        parent_by_child=parent_by_child,
        children_by_parent=dict(children_by_parent),
        depth_by_node=depth_by_node,
        role_by_node=role_by_node,
        root_node_ids=root_node_ids,
        issues=issues,
    )


def _find_cycles(
    node_order: list[str],
    parent_by_child: dict[str, str],
) -> list[list[str]]:
    cycles: list[list[str]] = []
    completed: set[str] = set()
    seen_cycle_keys: set[frozenset[str]] = set()
    for start in node_order:
        if start in completed:
            continue
        path: list[str] = []
        index_by_node: dict[str, int] = {}
        current = start
        while current in parent_by_child and current not in completed:
            if current in index_by_node:
                cycle = path[index_by_node[current] :]
                key = frozenset(cycle)
                if key and key not in seen_cycle_keys:
                    cycles.append(cycle)
                    seen_cycle_keys.add(key)
                break
            index_by_node[current] = len(path)
            path.append(current)
            current = parent_by_child[current]
        completed.update(path)
    return cycles
