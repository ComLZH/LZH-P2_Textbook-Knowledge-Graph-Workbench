from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import re

from ..contracts import FormalEdgeDTO, FormalNodeDTO
from .graph_semantics import (
    MAIN_BACKBONE_RELATIONS,
    is_auxiliary_node_type,
    is_container_node_type,
    is_main_knowledge_node_type,
    normalize_relation_type,
)
from .hierarchy import project_formal_hierarchy


LAYOUT_MODE_SINGLE_PATH = "single_path"
LAYOUT_MODE_MAIN_PATH_WITH_BRANCHES = "main_path_with_branches"
LAYOUT_MODE_MULTI_CORE_CLUSTERS = "multi_core_clusters"
ALLOWED_LAYOUT_MODES = {
    LAYOUT_MODE_SINGLE_PATH,
    LAYOUT_MODE_MAIN_PATH_WITH_BRANCHES,
    LAYOUT_MODE_MULTI_CORE_CLUSTERS,
}

NODE_BASE_WEIGHTS = {
    "concept": 4,
    "property": 3,
    "rule": 3,
    "method": 2,
}

BACKBONE_RELATION_WEIGHTS = {
    "prerequisite": 3,
    "progressive": 2,
    "derives_to": 2,
}


def structural_order_key(node: FormalNodeDTO) -> tuple[int, int, str]:
    """Return a stable textbook order for top-level structural containers."""

    page_numbers = [
        int(match.group(1))
        for location in node.source_locations
        for match in [re.search(r"(?:page|image)\s*=\s*(\d+)", location)]
        if match
    ]
    chapter_match = re.search(
        r"chapter[_-]?(\d+)",
        f"{node.node_name} {node.node_id}",
        flags=re.IGNORECASE,
    )
    arabic_title_match = re.search(r"(?:第\s*)?(\d+)\s*章", node.display_name)
    chapter_number = int(
        (chapter_match or arabic_title_match).group(1)
    ) if (chapter_match or arabic_title_match) else 10**9
    return (
        min(page_numbers, default=10**9),
        chapter_number,
        node.display_name,
    )


@dataclass(slots=True)
class ChapterLayoutPlan:
    container_node_id: str
    layout_mode: str = LAYOUT_MODE_SINGLE_PATH
    main_path_nodes: list[str] = field(default_factory=list)
    branch_groups: dict[str, list[str]] = field(default_factory=dict)
    auxiliary_attachments: dict[str, list[str]] = field(default_factory=dict)
    weak_edges: list[tuple[str, str, str]] = field(default_factory=list)
    structural_children: list[str] = field(default_factory=list)
    unassigned_nodes: list[str] = field(default_factory=list)
    local_confidence: float = 0.0
    fallback_reason: str = ""
    reasoning_summary: str = ""
    source: str = "local"

    def as_dict(self) -> dict[str, object]:
        return {
            "container_node_id": self.container_node_id,
            "layout_mode": self.layout_mode,
            "main_path_nodes": list(self.main_path_nodes),
            "branch_groups": {key: list(value) for key, value in self.branch_groups.items()},
            "auxiliary_attachments": {
                key: list(value) for key, value in self.auxiliary_attachments.items()
            },
            "weak_edges": [list(edge) for edge in self.weak_edges],
            "structural_children": list(self.structural_children),
            "unassigned_nodes": list(self.unassigned_nodes),
            "local_confidence": self.local_confidence,
            "fallback_reason": self.fallback_reason,
            "reasoning_summary": self.reasoning_summary,
            "source": self.source,
        }


def build_child_map(
    nodes: list[FormalNodeDTO],
    edges: list[FormalEdgeDTO] | None = None,
) -> dict[str, list[str]]:
    return project_formal_hierarchy(nodes, edges).children_by_parent


def build_chapter_layout_plans(
    nodes: list[FormalNodeDTO],
    edges: list[FormalEdgeDTO],
    recommendations: dict[str, dict[str, object]] | None = None,
) -> dict[str, ChapterLayoutPlan]:
    node_by_id = {node.node_id: node for node in nodes}
    child_map = build_child_map(nodes, edges)
    container_ids = [
        node.node_id
        for node in nodes
        if child_map.get(node.node_id)
    ]
    plans = {
        container_id: _build_local_plan(
            container_id=container_id,
            node_by_id=node_by_id,
            child_map=child_map,
            edges=edges,
        )
        for container_id in container_ids
    }
    if not recommendations:
        return plans
    for container_id, raw_recommendation in recommendations.items():
        if container_id not in plans:
            continue
        plans[container_id] = _apply_recommendation(
            local_plan=plans[container_id],
            raw_recommendation=raw_recommendation,
            node_by_id=node_by_id,
            child_map=child_map,
        )
    return plans


def plans_to_payload(plans: dict[str, ChapterLayoutPlan]) -> dict[str, dict[str, object]]:
    return {container_id: plan.as_dict() for container_id, plan in plans.items()}


def should_request_model_advice(plan: ChapterLayoutPlan) -> bool:
    branch_count = sum(len(children) for children in plan.branch_groups.values())
    complexity_score = (
        len(plan.main_path_nodes)
        + branch_count
        + len(plan.unassigned_nodes)
        + len(plan.structural_children)
    )
    return (
        plan.layout_mode == LAYOUT_MODE_MULTI_CORE_CLUSTERS
        or plan.local_confidence < 0.72
        or branch_count >= 3
        or complexity_score >= 7
        or len(plan.unassigned_nodes) >= 2
    )


def _build_local_plan(
    *,
    container_id: str,
    node_by_id: dict[str, FormalNodeDTO],
    child_map: dict[str, list[str]],
    edges: list[FormalEdgeDTO],
) -> ChapterLayoutPlan:
    child_ids = list(child_map.get(container_id, []))
    structural_children = [
        child_id
        for child_id in child_ids
        if child_id in node_by_id
        and is_container_node_type(node_by_id[child_id].node_type or node_by_id[child_id].knowledge_type)
    ]
    knowledge_children = [child_id for child_id in child_ids if child_id not in structural_children]
    main_candidates = [
        child_id
        for child_id in knowledge_children
        if child_id in node_by_id
        and is_main_knowledge_node_type(node_by_id[child_id].node_type or node_by_id[child_id].knowledge_type)
    ]
    auxiliary_nodes = [
        child_id
        for child_id in knowledge_children
        if child_id in node_by_id
        and is_auxiliary_node_type(node_by_id[child_id].node_type or node_by_id[child_id].knowledge_type)
    ]
    child_index = {child_id: index for index, child_id in enumerate(child_ids)}
    internal_edges = [
        edge
        for edge in edges
        if edge.source_node_id in child_ids
        and edge.target_node_id in child_ids
        and normalize_relation_type(edge.relation_type)
    ]
    weak_edges = [
        (
            edge.source_node_id,
            edge.target_node_id,
            normalize_relation_type(edge.relation_type),
        )
        for edge in internal_edges
        if normalize_relation_type(edge.relation_type) not in MAIN_BACKBONE_RELATIONS
    ]

    if not main_candidates:
        fallback_reason = "章节内部缺少足够的主干知识节点，暂采用保守布局。"
        return ChapterLayoutPlan(
            container_node_id=container_id,
            layout_mode=LAYOUT_MODE_SINGLE_PATH,
            auxiliary_attachments=_attach_auxiliary_nodes(
                auxiliary_nodes=auxiliary_nodes,
                candidate_targets=[],
                edges=internal_edges,
                child_index=child_index,
            ),
            weak_edges=weak_edges,
            structural_children=structural_children,
            unassigned_nodes=knowledge_children,
            local_confidence=0.38 if knowledge_children else 0.0,
            fallback_reason=fallback_reason,
            reasoning_summary=fallback_reason,
        )

    backbone_edges = [
        edge
        for edge in internal_edges
        if normalize_relation_type(edge.relation_type) in MAIN_BACKBONE_RELATIONS
        and edge.source_node_id in main_candidates
        and edge.target_node_id in main_candidates
    ]
    incoming: dict[str, list[FormalEdgeDTO]] = defaultdict(list)
    outgoing: dict[str, list[FormalEdgeDTO]] = defaultdict(list)
    for edge in backbone_edges:
        outgoing[edge.source_node_id].append(edge)
        incoming[edge.target_node_id].append(edge)

    node_scores = {
        node_id: NODE_BASE_WEIGHTS.get(
            node_by_id[node_id].node_type or node_by_id[node_id].knowledge_type,
            2,
        )
        for node_id in main_candidates
    }
    for edge in backbone_edges:
        weight = BACKBONE_RELATION_WEIGHTS[normalize_relation_type(edge.relation_type)]
        node_scores[edge.source_node_id] += weight
        node_scores[edge.target_node_id] += weight

    start_node = _pick_start_node(main_candidates, node_scores, incoming, child_index)
    main_path = _build_main_path(
        start_node=start_node,
        node_scores=node_scores,
        outgoing=outgoing,
        incoming=incoming,
        child_index=child_index,
    )
    covered_main_nodes = set(main_path)

    branch_groups: dict[str, list[str]] = defaultdict(list)
    for node_id in main_candidates:
        if node_id in covered_main_nodes:
            continue
        parent_id = _pick_branch_parent(
            node_id=node_id,
            main_path=main_path,
            outgoing=outgoing,
            incoming=incoming,
            child_index=child_index,
        )
        if parent_id:
            branch_groups[parent_id].append(node_id)

    branch_groups = {
        parent_id: sorted(children, key=lambda item: child_index.get(item, 9999))
        for parent_id, children in branch_groups.items()
        if children
    }
    assigned_branch_nodes = {node_id for values in branch_groups.values() for node_id in values}

    candidate_targets = list(main_path) + sorted(
        assigned_branch_nodes,
        key=lambda item: child_index.get(item, 9999),
    )
    auxiliary_attachments = _attach_auxiliary_nodes(
        auxiliary_nodes=auxiliary_nodes,
        candidate_targets=candidate_targets,
        edges=internal_edges,
        child_index=child_index,
    )
    assigned_aux_nodes = {
        node_id for values in auxiliary_attachments.values() for node_id in values
    }
    unassigned_nodes = [
        child_id
        for child_id in knowledge_children
        if child_id not in covered_main_nodes
        and child_id not in assigned_branch_nodes
        and child_id not in assigned_aux_nodes
    ]

    components = _connected_components(main_candidates, backbone_edges)
    branch_count = sum(len(children) for children in branch_groups.values())
    if len([component for component in components if len(component) > 1]) >= 2:
        layout_mode = LAYOUT_MODE_MULTI_CORE_CLUSTERS
    elif branch_count > 0:
        layout_mode = LAYOUT_MODE_MAIN_PATH_WITH_BRANCHES
    else:
        layout_mode = LAYOUT_MODE_SINGLE_PATH

    coverage = (
        (len(covered_main_nodes) + len(assigned_branch_nodes)) / max(1, len(main_candidates))
    )
    confidence = min(
        0.96,
        0.42
        + coverage * 0.34
        + (0.12 if backbone_edges else 0.0)
        + (0.08 if main_path else 0.0)
        - (0.06 if layout_mode == LAYOUT_MODE_MULTI_CORE_CLUSTERS else 0.0)
        - (0.06 if unassigned_nodes else 0.0),
    )
    fallback_reason = ""
    if not backbone_edges and len(main_candidates) > 1:
        fallback_reason = "主干关系不足，当前主路径主要依据节点类型与原始顺序推定。"
        confidence = min(confidence, 0.62)
    reasoning_summary = _build_reasoning_summary(
        layout_mode=layout_mode,
        main_path=main_path,
        branch_groups=branch_groups,
        auxiliary_attachments=auxiliary_attachments,
        fallback_reason=fallback_reason,
    )
    return ChapterLayoutPlan(
        container_node_id=container_id,
        layout_mode=layout_mode,
        main_path_nodes=main_path,
        branch_groups=branch_groups,
        auxiliary_attachments=auxiliary_attachments,
        weak_edges=weak_edges,
        structural_children=structural_children,
        unassigned_nodes=unassigned_nodes,
        local_confidence=round(confidence, 3),
        fallback_reason=fallback_reason,
        reasoning_summary=reasoning_summary,
    )


def _pick_start_node(
    main_candidates: list[str],
    node_scores: dict[str, int],
    incoming: dict[str, list[FormalEdgeDTO]],
    child_index: dict[str, int],
) -> str:
    return sorted(
        main_candidates,
        key=lambda node_id: (
            len(incoming.get(node_id, [])),
            -node_scores.get(node_id, 0),
            child_index.get(node_id, 9999),
        ),
    )[0]


def _build_main_path(
    *,
    start_node: str,
    node_scores: dict[str, int],
    outgoing: dict[str, list[FormalEdgeDTO]],
    incoming: dict[str, list[FormalEdgeDTO]],
    child_index: dict[str, int],
) -> list[str]:
    path = [start_node]
    visited = {start_node}
    current = start_node
    while True:
        candidates = [
            edge
            for edge in outgoing.get(current, [])
            if edge.target_node_id not in visited
        ]
        if not candidates:
            break
        next_edge = sorted(
            candidates,
            key=lambda edge: (
                -BACKBONE_RELATION_WEIGHTS[normalize_relation_type(edge.relation_type)],
                -node_scores.get(edge.target_node_id, 0),
                len(incoming.get(edge.target_node_id, [])),
                child_index.get(edge.target_node_id, 9999),
            ),
        )[0]
        current = next_edge.target_node_id
        path.append(current)
        visited.add(current)
    return path


def _pick_branch_parent(
    *,
    node_id: str,
    main_path: list[str],
    outgoing: dict[str, list[FormalEdgeDTO]],
    incoming: dict[str, list[FormalEdgeDTO]],
    child_index: dict[str, int],
) -> str:
    if not main_path:
        return ""
    path_order = {path_node_id: index for index, path_node_id in enumerate(main_path)}
    direct_sources = [
        edge.source_node_id
        for edge in incoming.get(node_id, [])
        if edge.source_node_id in path_order
    ]
    if direct_sources:
        return sorted(
            direct_sources,
            key=lambda source_id: (path_order[source_id], child_index.get(source_id, 9999)),
        )[0]
    direct_targets = [
        edge.target_node_id
        for edge in outgoing.get(node_id, [])
        if edge.target_node_id in path_order
    ]
    if direct_targets:
        return sorted(
            direct_targets,
            key=lambda target_id: (path_order[target_id], child_index.get(target_id, 9999)),
        )[0]
    return sorted(main_path, key=lambda item: child_index.get(item, 9999))[0]


def _attach_auxiliary_nodes(
    *,
    auxiliary_nodes: list[str],
    candidate_targets: list[str],
    edges: list[FormalEdgeDTO],
    child_index: dict[str, int],
) -> dict[str, list[str]]:
    if not auxiliary_nodes or not candidate_targets:
        return {}
    edge_pairs: list[tuple[str, str]] = []
    for edge in edges:
        relation_type = normalize_relation_type(edge.relation_type)
        if relation_type == "contains":
            continue
        edge_pairs.append((edge.source_node_id, edge.target_node_id))
        edge_pairs.append((edge.target_node_id, edge.source_node_id))

    attachments: dict[str, list[str]] = defaultdict(list)
    for auxiliary_node in auxiliary_nodes:
        explicit_targets = [
            target_id
            for source_id, target_id in edge_pairs
            if source_id == auxiliary_node and target_id in candidate_targets
        ]
        explicit_sources = [
            source_id
            for source_id, target_id in edge_pairs
            if target_id == auxiliary_node and source_id in candidate_targets
        ]
        options = explicit_targets + explicit_sources
        if not options:
            options = list(candidate_targets)
        parent_id = sorted(
            dict.fromkeys(options),
            key=lambda item: abs(child_index.get(item, 9999) - child_index.get(auxiliary_node, 9999)),
        )[0]
        attachments[parent_id].append(auxiliary_node)
    return {
        parent_id: sorted(children, key=lambda item: child_index.get(item, 9999))
        for parent_id, children in attachments.items()
    }


def _connected_components(
    main_candidates: list[str],
    backbone_edges: list[FormalEdgeDTO],
) -> list[list[str]]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in main_candidates}
    for edge in backbone_edges:
        adjacency.setdefault(edge.source_node_id, set()).add(edge.target_node_id)
        adjacency.setdefault(edge.target_node_id, set()).add(edge.source_node_id)
    remaining = set(main_candidates)
    components: list[list[str]] = []
    while remaining:
        start = next(iter(remaining))
        queue = deque([start])
        component: list[str] = []
        remaining.remove(start)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in adjacency.get(current, set()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return components


def _build_reasoning_summary(
    *,
    layout_mode: str,
    main_path: list[str],
    branch_groups: dict[str, list[str]],
    auxiliary_attachments: dict[str, list[str]],
    fallback_reason: str,
) -> str:
    parts = []
    if layout_mode == LAYOUT_MODE_SINGLE_PATH:
        parts.append("章节内部以单主线展示。")
    elif layout_mode == LAYOUT_MODE_MAIN_PATH_WITH_BRANCHES:
        parts.append("章节内部以主线加分支展示。")
    else:
        parts.append("章节内部存在多个核心知识群，采用多核心骨架展示。")
    if main_path:
        parts.append(f"主路径节点数：{len(main_path)}。")
    if branch_groups:
        parts.append(f"分支挂接点数：{len(branch_groups)}。")
    if auxiliary_attachments:
        parts.append(f"辅助挂接点数：{len(auxiliary_attachments)}。")
    if fallback_reason:
        parts.append(fallback_reason)
    return "".join(parts)


def _apply_recommendation(
    *,
    local_plan: ChapterLayoutPlan,
    raw_recommendation: dict[str, object],
    node_by_id: dict[str, FormalNodeDTO],
    child_map: dict[str, list[str]],
) -> ChapterLayoutPlan:
    child_ids = set(child_map.get(local_plan.container_node_id, []))
    if not child_ids:
        return local_plan
    layout_mode = str(
        raw_recommendation.get("recommended_layout_mode")
        or raw_recommendation.get("layout_mode")
        or local_plan.layout_mode
    ).strip()
    if layout_mode not in ALLOWED_LAYOUT_MODES:
        layout_mode = local_plan.layout_mode

    structural_children = list(local_plan.structural_children)
    eligible_main_nodes = {
        child_id
        for child_id in child_ids
        if child_id not in structural_children
        and not is_auxiliary_node_type(node_by_id[child_id].node_type or node_by_id[child_id].knowledge_type)
    }
    auxiliary_nodes = {
        child_id
        for child_id in child_ids
        if child_id not in structural_children
        and is_auxiliary_node_type(node_by_id[child_id].node_type or node_by_id[child_id].knowledge_type)
    }
    main_path = _normalize_id_list(
        raw_recommendation.get("main_path")
        or raw_recommendation.get("main_path_nodes")
        or local_plan.main_path_nodes,
        allowed_ids=eligible_main_nodes,
    )
    if not main_path and local_plan.main_path_nodes:
        main_path = list(local_plan.main_path_nodes)

    branch_groups = _normalize_mapping_list(
        raw_recommendation.get("branch_groups"),
        allowed_parent_ids=set(main_path or eligible_main_nodes),
        allowed_child_ids=eligible_main_nodes,
        blocked_ids=set(main_path),
    )
    if not branch_groups:
        branch_groups = {key: list(value) for key, value in local_plan.branch_groups.items()}

    auxiliary_attachments = _normalize_mapping_list(
        raw_recommendation.get("auxiliary_attachments"),
        allowed_parent_ids=set(main_path or eligible_main_nodes),
        allowed_child_ids=auxiliary_nodes,
        blocked_ids=set(),
    )
    if not auxiliary_attachments:
        auxiliary_attachments = {
            key: list(value) for key, value in local_plan.auxiliary_attachments.items()
        }

    weak_edges = _normalize_weak_edges(
        raw_recommendation.get("weak_edges"),
        allowed_ids=child_ids,
    ) or list(local_plan.weak_edges)
    assigned = set(main_path)
    assigned.update(item for values in branch_groups.values() for item in values)
    assigned.update(item for values in auxiliary_attachments.values() for item in values)
    unassigned_nodes = [
        child_id
        for child_id in child_ids
        if child_id not in structural_children and child_id not in assigned
    ]
    reasoning_summary = str(
        raw_recommendation.get("reasoning_summary") or local_plan.reasoning_summary
    ).strip()
    confidence = raw_recommendation.get("confidence", local_plan.local_confidence)
    try:
        normalized_confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        normalized_confidence = local_plan.local_confidence
    return ChapterLayoutPlan(
        container_node_id=local_plan.container_node_id,
        layout_mode=layout_mode,
        main_path_nodes=main_path,
        branch_groups=branch_groups,
        auxiliary_attachments=auxiliary_attachments,
        weak_edges=weak_edges,
        structural_children=structural_children,
        unassigned_nodes=unassigned_nodes,
        local_confidence=round(normalized_confidence, 3),
        fallback_reason=local_plan.fallback_reason,
        reasoning_summary=reasoning_summary,
        source="model_suggested",
    )


def _normalize_id_list(raw_value: object, *, allowed_ids: set[str]) -> list[str]:
    if not isinstance(raw_value, list):
        return []
    result: list[str] = []
    for item in raw_value:
        value = str(item or "").strip()
        if value and value in allowed_ids and value not in result:
            result.append(value)
    return result


def _normalize_mapping_list(
    raw_value: object,
    *,
    allowed_parent_ids: set[str],
    allowed_child_ids: set[str],
    blocked_ids: set[str],
) -> dict[str, list[str]]:
    if not isinstance(raw_value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    used_children: set[str] = set()
    for raw_parent_id, raw_children in raw_value.items():
        parent_id = str(raw_parent_id or "").strip()
        if parent_id not in allowed_parent_ids or not isinstance(raw_children, list):
            continue
        children: list[str] = []
        for child in raw_children:
            child_id = str(child or "").strip()
            if (
                child_id
                and child_id in allowed_child_ids
                and child_id not in blocked_ids
                and child_id not in used_children
            ):
                children.append(child_id)
                used_children.add(child_id)
        if children:
            normalized[parent_id] = children
    return normalized


def _normalize_weak_edges(
    raw_value: object,
    *,
    allowed_ids: set[str],
) -> list[tuple[str, str, str]]:
    if not isinstance(raw_value, list):
        return []
    normalized: list[tuple[str, str, str]] = []
    for item in raw_value:
        if isinstance(item, dict):
            source_id = str(item.get("source_node_id") or "").strip()
            target_id = str(item.get("target_node_id") or "").strip()
            relation_type = normalize_relation_type(item.get("relation_type"))
        elif isinstance(item, list) and len(item) >= 3:
            source_id = str(item[0] or "").strip()
            target_id = str(item[1] or "").strip()
            relation_type = normalize_relation_type(item[2])
        else:
            continue
        if source_id in allowed_ids and target_id in allowed_ids and relation_type:
            normalized.append((source_id, target_id, relation_type))
    return normalized
