from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean

from ..contracts import FormalEdgeDTO, FormalNodeDTO
from .graph_semantics import normalize_relation_type
from .hierarchy import project_formal_hierarchy


DIRECTIONAL_LAYOUT_RELATIONS = frozenset(
    {
        "prerequisite",
        "progressive",
        "derives_to",
        "explains",
        "applies_to",
        "represented_by",
    }
)
SYMMETRIC_LAYOUT_RELATIONS = frozenset({"equivalent", "parallel", "contrast"})
RELATION_LAYOUT_REVISION = 4


def relation_edge_id(source_id: str, relation_type: object, target_id: str) -> str:
    return f"{source_id}--{normalize_relation_type(relation_type)}--{target_id}"


def relation_directionality(relation_type: object) -> str:
    normalized = normalize_relation_type(relation_type)
    if normalized in DIRECTIONAL_LAYOUT_RELATIONS or normalized.startswith("layout_"):
        return "directional"
    if normalized in SYMMETRIC_LAYOUT_RELATIONS:
        return "symmetric"
    if normalized == "contains":
        return "hierarchy"
    return "directional"


@dataclass(slots=True)
class ChapterRelationLayout:
    container_node_id: str
    node_ranks: dict[str, int] = field(default_factory=dict)
    node_orders: dict[str, int] = field(default_factory=dict)
    component_by_node: dict[str, str] = field(default_factory=dict)
    cycle_node_ids: list[str] = field(default_factory=list)
    cycle_edge_ids: list[str] = field(default_factory=list)
    directional_edge_ids: list[str] = field(default_factory=list)
    symmetric_edge_ids: list[str] = field(default_factory=list)
    max_rank: int = 0
    max_rank_size: int = 0
    fallback_mode: str = "relation_driven"

    def nodes_by_rank(self) -> dict[int, list[str]]:
        grouped: dict[int, list[str]] = defaultdict(list)
        for node_id, rank in self.node_ranks.items():
            grouped[rank].append(node_id)
        return {
            rank: sorted(node_ids, key=lambda item: self.node_orders.get(item, 0))
            for rank, node_ids in sorted(grouped.items())
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "container_node_id": self.container_node_id,
            "node_ranks": dict(self.node_ranks),
            "node_orders": dict(self.node_orders),
            "component_by_node": dict(self.component_by_node),
            "cycle_node_ids": list(self.cycle_node_ids),
            "cycle_edge_ids": list(self.cycle_edge_ids),
            "directional_edge_ids": list(self.directional_edge_ids),
            "symmetric_edge_ids": list(self.symmetric_edge_ids),
            "max_rank": self.max_rank,
            "max_rank_size": self.max_rank_size,
            "fallback_mode": self.fallback_mode,
        }


def build_relation_layouts(
    nodes: list[FormalNodeDTO],
    edges: list[FormalEdgeDTO],
) -> dict[str, ChapterRelationLayout]:
    hierarchy = project_formal_hierarchy(nodes, edges)
    return {
        container_id: build_chapter_relation_layout(
            container_node_id=container_id,
            child_ids=child_ids,
            edges=edges,
        )
        for container_id, child_ids in hierarchy.children_by_parent.items()
        if child_ids
    }


def build_chapter_relation_layout(
    *,
    container_node_id: str,
    child_ids: list[str],
    edges: list[FormalEdgeDTO],
) -> ChapterRelationLayout:
    ordered_node_ids = list(dict.fromkeys(child_ids))
    node_id_set = set(ordered_node_ids)
    node_index = {node_id: index for index, node_id in enumerate(ordered_node_ids)}
    internal_edges = [
        edge
        for edge in edges
        if edge.source_node_id in node_id_set
        and edge.target_node_id in node_id_set
    ]
    directional_edges = [
        edge
        for edge in internal_edges
        if relation_directionality(edge.relation_type) == "directional"
    ]
    symmetric_edges = [
        edge
        for edge in internal_edges
        if relation_directionality(edge.relation_type) == "symmetric"
    ]

    union_find = _OrderedUnionFind(ordered_node_ids, node_index)
    for edge in symmetric_edges:
        union_find.union(edge.source_node_id, edge.target_node_id)

    alignment_groups: dict[str, list[str]] = defaultdict(list)
    for node_id in ordered_node_ids:
        alignment_groups[union_find.find(node_id)].append(node_id)
    group_order = {
        group_id: min(node_index[node_id] for node_id in members)
        for group_id, members in alignment_groups.items()
    }
    group_by_node = {
        node_id: group_id
        for group_id, members in alignment_groups.items()
        for node_id in members
    }

    group_adjacency: dict[str, set[str]] = {
        group_id: set() for group_id in alignment_groups
    }
    group_self_loops: set[str] = set()
    for edge in directional_edges:
        source_group = group_by_node[edge.source_node_id]
        target_group = group_by_node[edge.target_node_id]
        if source_group == target_group:
            group_self_loops.add(source_group)
        else:
            group_adjacency[source_group].add(target_group)

    components = _strongly_connected_components(group_adjacency, group_order)
    component_index_by_group: dict[str, int] = {}
    for component_index, group_ids in enumerate(components):
        for group_id in group_ids:
            component_index_by_group[group_id] = component_index

    component_members: dict[int, list[str]] = defaultdict(list)
    for group_id, members in alignment_groups.items():
        component_members[component_index_by_group[group_id]].extend(members)
    for members in component_members.values():
        members.sort(key=node_index.__getitem__)

    component_adjacency: dict[int, set[int]] = {
        component_index: set() for component_index in component_members
    }
    component_predecessors: dict[int, set[int]] = {
        component_index: set() for component_index in component_members
    }
    for source_group, targets in group_adjacency.items():
        source_component = component_index_by_group[source_group]
        for target_group in targets:
            target_component = component_index_by_group[target_group]
            if source_component == target_component:
                continue
            component_adjacency[source_component].add(target_component)
            component_predecessors[target_component].add(source_component)

    component_order_key = {
        component_index: min(node_index[node_id] for node_id in members)
        for component_index, members in component_members.items()
    }
    component_ranks = _longest_path_ranks(
        component_adjacency,
        component_predecessors,
        component_order_key,
    )

    if directional_edges:
        _spread_isolated_components(
            component_ranks=component_ranks,
            adjacency=component_adjacency,
            predecessors=component_predecessors,
            component_order_key=component_order_key,
            node_count=len(ordered_node_ids),
        )
        fallback_mode = "relation_driven"
    else:
        _apply_three_column_fallback(
            component_ranks=component_ranks,
            component_order_key=component_order_key,
        )
        fallback_mode = "symmetric_alignment" if symmetric_edges else "structural_grid"

    ordered_components_by_rank = _order_components_with_barycenters(
        component_ranks=component_ranks,
        predecessors=component_predecessors,
        component_order_key=component_order_key,
    )
    node_ranks: dict[str, int] = {}
    node_orders: dict[str, int] = {}
    component_by_node: dict[str, str] = {}
    for rank, component_ids in ordered_components_by_rank.items():
        order_cursor = 0
        for component_index in component_ids:
            component_label = f"c{component_index}"
            for node_id in component_members[component_index]:
                node_ranks[node_id] = rank
                node_orders[node_id] = order_cursor
                component_by_node[node_id] = component_label
                order_cursor += 1

    cyclic_components: set[int] = set()
    for component_index, group_ids in enumerate(components):
        if len(group_ids) > 1 or any(group_id in group_self_loops for group_id in group_ids):
            cyclic_components.add(component_index)
    cycle_node_ids = [
        node_id
        for node_id in ordered_node_ids
        if component_index_by_group[group_by_node[node_id]] in cyclic_components
    ]
    cycle_edge_ids = [
        relation_edge_id(edge.source_node_id, edge.relation_type, edge.target_node_id)
        for edge in directional_edges
        if component_index_by_group[group_by_node[edge.source_node_id]]
        == component_index_by_group[group_by_node[edge.target_node_id]]
    ]
    max_rank = max(node_ranks.values(), default=0)
    rank_sizes = defaultdict(int)
    for rank in node_ranks.values():
        rank_sizes[rank] += 1
    return ChapterRelationLayout(
        container_node_id=container_node_id,
        node_ranks=node_ranks,
        node_orders=node_orders,
        component_by_node=component_by_node,
        cycle_node_ids=cycle_node_ids,
        cycle_edge_ids=cycle_edge_ids,
        directional_edge_ids=[
            relation_edge_id(edge.source_node_id, edge.relation_type, edge.target_node_id)
            for edge in directional_edges
        ],
        symmetric_edge_ids=[
            relation_edge_id(edge.source_node_id, edge.relation_type, edge.target_node_id)
            for edge in symmetric_edges
        ],
        max_rank=max_rank,
        max_rank_size=max(rank_sizes.values(), default=0),
        fallback_mode=fallback_mode,
    )


class _OrderedUnionFind:
    def __init__(self, node_ids: list[str], order: dict[str, int]) -> None:
        self.parent = {node_id: node_id for node_id in node_ids}
        self.order = order

    def find(self, node_id: str) -> str:
        parent = self.parent[node_id]
        if parent != node_id:
            self.parent[node_id] = self.find(parent)
        return self.parent[node_id]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.order[left_root] <= self.order[right_root]:
            self.parent[right_root] = left_root
        else:
            self.parent[left_root] = right_root


def _strongly_connected_components(
    adjacency: dict[str, set[str]],
    order: dict[str, int],
) -> list[list[str]]:
    current_index = 0
    index_by_node: dict[str, int] = {}
    low_link: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node_id: str) -> None:
        nonlocal current_index
        index_by_node[node_id] = current_index
        low_link[node_id] = current_index
        current_index += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for target_id in sorted(adjacency.get(node_id, set()), key=order.__getitem__):
            if target_id not in index_by_node:
                visit(target_id)
                low_link[node_id] = min(low_link[node_id], low_link[target_id])
            elif target_id in on_stack:
                low_link[node_id] = min(low_link[node_id], index_by_node[target_id])
        if low_link[node_id] != index_by_node[node_id]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node_id:
                break
        component.sort(key=order.__getitem__)
        components.append(component)

    for node_id in sorted(adjacency, key=order.__getitem__):
        if node_id not in index_by_node:
            visit(node_id)
    components.sort(key=lambda members: min(order[item] for item in members))
    return components


def _longest_path_ranks(
    adjacency: dict[int, set[int]],
    predecessors: dict[int, set[int]],
    order: dict[int, int],
) -> dict[int, int]:
    indegree = {node_id: len(predecessors.get(node_id, set())) for node_id in adjacency}
    ready = sorted(
        [node_id for node_id, value in indegree.items() if value == 0],
        key=order.__getitem__,
    )
    ranks = {node_id: 0 for node_id in adjacency}
    while ready:
        node_id = ready.pop(0)
        for target_id in sorted(adjacency[node_id], key=order.__getitem__):
            ranks[target_id] = max(ranks[target_id], ranks[node_id] + 1)
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                ready.append(target_id)
                ready.sort(key=order.__getitem__)
    return ranks


def _spread_isolated_components(
    *,
    component_ranks: dict[int, int],
    adjacency: dict[int, set[int]],
    predecessors: dict[int, set[int]],
    component_order_key: dict[int, int],
    node_count: int,
) -> None:
    max_rank = max(component_ranks.values(), default=0)
    if max_rank <= 0 or node_count <= 1:
        return
    isolated = [
        component_id
        for component_id in component_ranks
        if not adjacency.get(component_id) and not predecessors.get(component_id)
    ]
    for component_id in isolated:
        relative_order = component_order_key[component_id] / max(1, node_count - 1)
        component_ranks[component_id] = min(max_rank, round(relative_order * max_rank))


def _apply_three_column_fallback(
    *,
    component_ranks: dict[int, int],
    component_order_key: dict[int, int],
) -> None:
    ordered_components = sorted(component_ranks, key=component_order_key.__getitem__)
    column_count = min(3, max(1, len(ordered_components)))
    for index, component_id in enumerate(ordered_components):
        component_ranks[component_id] = index % column_count


def _order_components_with_barycenters(
    *,
    component_ranks: dict[int, int],
    predecessors: dict[int, set[int]],
    component_order_key: dict[int, int],
) -> dict[int, list[int]]:
    grouped: dict[int, list[int]] = defaultdict(list)
    for component_id, rank in component_ranks.items():
        grouped[rank].append(component_id)
    order_in_rank: dict[int, int] = {}
    result: dict[int, list[int]] = {}
    for rank in sorted(grouped):
        def sort_key(component_id: int) -> tuple[float, int]:
            predecessor_orders = [
                order_in_rank[pred]
                for pred in predecessors.get(component_id, set())
                if pred in order_in_rank
            ]
            barycenter = mean(predecessor_orders) if predecessor_orders else float("inf")
            return (barycenter, component_order_key[component_id])

        component_ids = sorted(grouped[rank], key=sort_key)
        result[rank] = component_ids
        for index, component_id in enumerate(component_ids):
            order_in_rank[component_id] = index
    return result
