from __future__ import annotations

import heapq
import math
import time
from dataclasses import dataclass, field, replace

import networkx as nx

from ..review_views.projection import (
    VIEW_MODE_RELATIONS,
    ProjectedEdgeInstance,
    ReviewViewProjection,
)
from ..review_views.semantic_style import relation_display_spec
from ..utils.graph_semantics import normalize_relation_type


@dataclass(frozen=True, slots=True)
class RenderMetricsProfile:
    renderer: str = "logical"
    specification_version: int = 3
    card_width: float = 250.0
    card_height: float = 86.0
    scope_min_width: float = 310.0
    scope_header_height: float = 54.0
    scope_padding_x: float = 28.0
    scope_padding_y: float = 24.0
    rank_gap: float = 80.0
    row_gap: float = 28.0
    unit_gap: float = 34.0
    root_gap: float = 54.0
    border_tolerance: float = 1.0
    component_gap: float = 72.0
    route_clearance: float = 14.0
    route_lane_gap: float = 12.0
    route_turn_penalty: float = 24.0
    route_congestion_penalty: float = 900.0
    label_width: float = 156.0
    label_height: float = 28.0
    target_aspect_ratio: float = 1.6
    terminal_stub_length: float = 24.0
    arrow_length: float = 12.0
    arrow_half_width: float = 6.0
    arrow_tip_gap: float = 3.0
    port_min_spacing: float = 12.0
    group_header_height: float = 42.0
    group_padding: float = 28.0


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2.0, self.y + self.height / 2.0

    def contains(self, other: "Rect", *, tolerance: float = 0.0) -> bool:
        return (
            other.x >= self.x - tolerance
            and other.y >= self.y - tolerance
            and other.right <= self.right + tolerance
            and other.bottom <= self.bottom + tolerance
        )

    def intersects(self, other: "Rect", *, tolerance: float = 0.0) -> bool:
        return not (
            self.right <= other.x + tolerance
            or other.right <= self.x + tolerance
            or self.bottom <= other.y + tolerance
            or other.bottom <= self.y + tolerance
        )


@dataclass(frozen=True, slots=True)
class GeometryIssue:
    code: str
    message: str
    object_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EdgeRoute:
    edge_view_id: str
    relation_id: str
    source_view_id: str
    target_view_id: str
    points: tuple[tuple[float, float], ...]
    source_port: str
    target_port: str
    status: str = "routed"
    source_escape: tuple[float, float] | None = None
    target_escape: tuple[float, float] | None = None
    source_normal: tuple[float, float] | None = None
    target_normal: tuple[float, float] | None = None
    source_arrow: tuple[tuple[float, float], ...] = ()
    target_arrow: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True, slots=True)
class EdgeLabel:
    relation_id: str
    text: str
    rect: Rect | None
    visible: bool
    suppression_reason: str = ""


@dataclass(frozen=True, slots=True)
class VisualGroupGeometry:
    group_id: str
    kind: str
    title: str
    member_view_ids: tuple[str, ...]
    frame_rect: Rect
    content_rect: Rect
    paint_bounds: Rect
    grouping_basis: str


@dataclass(slots=True)
class ReadabilityReport:
    node_overlap: int = 0
    edge_node_intrusion: int = 0
    edge_crossing: int = 0
    ambiguous_overlap: int = 0
    label_overlap: int = 0
    routed_edges: int = 0
    failed_edges: int = 0
    terminal_direction_violation: int = 0
    terminal_stub_short: int = 0
    arrow_occlusion: int = 0
    port_spacing_violation: int = 0
    group_overlap: int = 0
    group_overflow: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)
    issue_ids: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)

    @property
    def hard_violations(self) -> int:
        return (
            self.node_overlap
            + self.edge_node_intrusion
            + self.ambiguous_overlap
            + self.label_overlap
            + self.failed_edges
            + self.terminal_direction_violation
            + self.terminal_stub_short
            + self.arrow_occlusion
            + self.port_spacing_violation
            + self.group_overlap
            + self.group_overflow
        )


@dataclass(slots=True)
class SceneGeometry:
    document_id: str
    document_revision: int
    structure_fingerprint: str
    renderer: str
    specification_version: int
    scope_rects: dict[str, Rect]
    scope_content_rects: dict[str, Rect]
    node_rects: dict[str, Rect]
    edge_instances: list[ProjectedEdgeInstance]
    issues: list[GeometryIssue] = field(default_factory=list)
    view_mode: str = "textbook"
    layout_version: int = 6
    engine: str = "python_networkx_v6"
    edge_routes: dict[str, EdgeRoute] = field(default_factory=dict)
    edge_labels: dict[str, EdgeLabel] = field(default_factory=dict)
    readability: ReadabilityReport = field(default_factory=ReadabilityReport)
    visual_groups: dict[str, VisualGroupGeometry] = field(default_factory=dict)

    @property
    def all_rects(self) -> dict[str, Rect]:
        return {**self.scope_rects, **self.node_rects}

    @property
    def canvas_rect(self) -> Rect:
        rects = (
            list(self.scope_rects.values())
            + list(self.node_rects.values())
            + [group.frame_rect for group in self.visual_groups.values()]
        )
        for route in self.edge_routes.values():
            for polygon in (route.source_arrow, route.target_arrow):
                if polygon:
                    xs = [point[0] for point in polygon]
                    ys = [point[1] for point in polygon]
                    rects.append(Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
        rects.extend(label.rect for label in self.edge_labels.values() if label.rect is not None)
        for route in self.edge_routes.values():
            if route.points:
                xs = [point[0] for point in route.points]
                ys = [point[1] for point in route.points]
                rects.append(Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
        if not rects:
            return Rect(0.0, 0.0, 0.0, 0.0)
        left = min(item.x for item in rects)
        top = min(item.y for item in rects)
        right = max(item.right for item in rects)
        bottom = max(item.bottom for item in rects)
        return Rect(left, top, right - left, bottom - top)


@dataclass(slots=True)
class _LocalLayout:
    width: float
    height: float
    node_rects: dict[str, Rect]
    scope_rects: dict[str, Rect]
    content_rects: dict[str, Rect]


_SYMMETRIC_RELATIONS = {"parallel", "equivalent", "contrast"}
_DIRECTIONAL_RELATIONS = {
    "prerequisite",
    "progressive",
    "derives_to",
    "explains",
    "applies_to",
    "represented_by",
}


def layout_scene(
    projection: ReviewViewProjection,
    metrics: RenderMetricsProfile | None = None,
    overrides: dict[str, dict[str, float]] | None = None,
) -> SceneGeometry:
    """Lay out a review projection deterministically without GUI or I/O dependencies."""

    metrics = metrics or RenderMetricsProfile()
    overrides = overrides or {}
    started = time.perf_counter()
    if projection.view_mode == VIEW_MODE_RELATIONS:
        from .elk_backend import ElkBackendError

        node_count = len(projection.node_instances)
        edge_count = len(projection.edge_instances)
        if (
            node_count >= 300
            or edge_count > 160
            or node_count * edge_count > 12_000
        ) and not _fits_partitioned_large_graph_budget(projection):
            return _layout_relations_degraded_scene(projection, metrics, started)
        if node_count >= 80:
            scene = _layout_relations_scene(projection, metrics, overrides, started)
            scene.issues.insert(
                0,
                GeometryIssue(
                    "layout_backend_selected",
                    "当前规模使用内置确定性布局，避免外部引擎启动及超时挤占全局预算。",
                    (str(node_count), str(edge_count)),
                ),
            )
            return scene
        try:
            return _layout_relations_scene_elk(projection, metrics, overrides, started)
        except ElkBackendError as exc:
            if len(projection.node_instances) > 150 or len(projection.edge_instances) > 600:
                scene = _layout_relations_degraded_scene(projection, metrics, started)
            else:
                scene = _layout_relations_scene(projection, metrics, overrides, started)
            scene.issues.insert(
                0,
                GeometryIssue(
                    "layout_backend_fallback",
                    "ELK 离线布局运行时不可用、输出未通过门禁或拓扑退化，已使用内置 Python 布局。",
                    (type(exc).__name__, str(exc)[:80]),
                ),
            )
            return scene
    scopes = {item.scope_id: item for item in projection.scopes}
    children: dict[str, list[str]] = {scope_id: [] for scope_id in scopes}
    roots: list[str] = []
    for scope in projection.scopes:
        if scope.parent_scope_id and scope.parent_scope_id in scopes:
            children[scope.parent_scope_id].append(scope.scope_id)
        else:
            roots.append(scope.scope_id)
    sort_key = lambda scope_id: (scopes[scope_id].reading_order, scope_id)
    for values in children.values():
        values.sort(key=sort_key)
    roots.sort(key=sort_key)

    instances_by_scope: dict[str, list[str]] = {scope_id: [] for scope_id in scopes}
    instance_scope: dict[str, str] = {}
    for instance in projection.node_instances:
        instance_scope[instance.view_id] = instance.scope_id
        if instance.scope_id in scopes:
            instances_by_scope[instance.scope_id].append(instance.view_id)
    for values in instances_by_scope.values():
        values.sort()

    issues: list[GeometryIssue] = []
    local_cache: dict[str, _LocalLayout] = {}
    visiting: set[str] = set()

    def measure(scope_id: str) -> _LocalLayout:
        if scope_id in local_cache:
            return local_cache[scope_id]
        if scope_id in visiting:
            issues.append(
                GeometryIssue(
                    "layout_structure_cycle",
                    "教材结构循环，无法递归布局。",
                    (scope_id,),
                )
            )
            return _empty_scope(metrics)
        visiting.add(scope_id)
        node_ids = instances_by_scope.get(scope_id, [])
        local_edges = [
            edge
            for edge in projection.edge_instances
            if edge.source_view_id in node_ids and edge.target_view_id in node_ids
        ]
        _ranks, cycle_nodes = _rank_nodes(node_ids, local_edges)
        local_node_rects, _local_width, _local_height = _layout_node_set(
            node_ids,
            local_edges,
            metrics,
        )
        node_rects = {
            view_id: Rect(
                rect.x + metrics.scope_padding_x,
                rect.y + metrics.scope_header_height + metrics.scope_padding_y,
                rect.width,
                rect.height,
            )
            for view_id, rect in local_node_rects.items()
        }
        max_node_bottom = max(
            (rect.bottom for rect in node_rects.values()),
            default=metrics.scope_header_height + metrics.scope_padding_y,
        )
        node_width = (
            max((rect.right for rect in node_rects.values()), default=metrics.scope_padding_x)
            + metrics.scope_padding_x
        )

        child_layouts = [(child_id, measure(child_id)) for child_id in children.get(scope_id, [])]
        child_y = max_node_bottom
        if node_rects and child_layouts:
            child_y += metrics.unit_gap
        child_scope_rects: dict[str, Rect] = {}
        child_content_rects: dict[str, Rect] = {}
        descendant_node_rects: dict[str, Rect] = {}
        descendant_scope_rects: dict[str, Rect] = {}
        descendant_content_rects: dict[str, Rect] = {}
        max_child_width = 0.0
        for child_id, child_layout in child_layouts:
            child_x = metrics.scope_padding_x
            child_scope_rects[f"scope:{child_id}"] = Rect(
                child_x, child_y, child_layout.width, child_layout.height
            )
            _merge_shifted(descendant_node_rects, child_layout.node_rects, child_x, child_y)
            _merge_shifted(descendant_scope_rects, child_layout.scope_rects, child_x, child_y)
            _merge_shifted(descendant_content_rects, child_layout.content_rects, child_x, child_y)
            max_child_width = max(max_child_width, child_layout.width)
            child_y += child_layout.height + metrics.unit_gap

        content_bottom = max(
            max_node_bottom,
            child_y - metrics.unit_gap if child_layouts else metrics.scope_header_height + metrics.scope_padding_y,
        )
        width = max(
            metrics.scope_min_width,
            node_width,
            max_child_width + 2 * metrics.scope_padding_x,
        )
        height = max(
            metrics.scope_header_height + 2 * metrics.scope_padding_y + metrics.card_height,
            content_bottom + metrics.scope_padding_y,
        )
        content = Rect(
            metrics.scope_padding_x,
            metrics.scope_header_height,
            width - 2 * metrics.scope_padding_x,
            height - metrics.scope_header_height - metrics.scope_padding_y,
        )
        layout = _LocalLayout(
            width=width,
            height=height,
            node_rects={**node_rects, **descendant_node_rects},
            scope_rects={
                f"scope:{scope_id}": Rect(0.0, 0.0, width, height),
                **child_scope_rects,
                **descendant_scope_rects,
            },
            content_rects={
                f"scope:{scope_id}": content,
                **child_content_rects,
                **descendant_content_rects,
            },
        )
        local_cache[scope_id] = layout
        visiting.remove(scope_id)
        if cycle_nodes:
            issues.append(
                GeometryIssue(
                    "semantic_cycle",
                    "同一教材作用域存在方向性语义环，已保留关系方向并稳定排布。",
                    tuple(sorted(cycle_nodes)),
                )
            )
        return layout

    scope_rects: dict[str, Rect] = {}
    content_rects: dict[str, Rect] = {}
    node_rects: dict[str, Rect] = {}
    y_cursor = 0.0
    for root_id in roots:
        local = measure(root_id)
        _merge_shifted(scope_rects, local.scope_rects, 0.0, y_cursor)
        _merge_shifted(content_rects, local.content_rects, 0.0, y_cursor)
        _merge_shifted(node_rects, local.node_rects, 0.0, y_cursor)
        y_cursor += local.height + metrics.root_gap

    for view_id, raw in sorted(overrides.items()):
        if view_id not in node_rects:
            continue
        original = node_rects[view_id]
        try:
            candidate = Rect(float(raw["x"]), float(raw["y"]), original.width, original.height)
        except (KeyError, TypeError, ValueError):
            issues.append(GeometryIssue("manual_position_invalid", "手工坐标无效，已忽略。", (view_id,)))
            continue
        scope_id = instance_scope.get(view_id, "")
        parent_content = content_rects.get(f"scope:{scope_id}")
        siblings = [rect for key, rect in node_rects.items() if key != view_id and instance_scope.get(key) == scope_id]
        if parent_content is None or not parent_content.contains(candidate, tolerance=metrics.border_tolerance):
            issues.append(GeometryIssue("manual_position_out_of_scope", "手工位置越出当前教材框，已恢复。", (view_id,)))
        elif any(candidate.intersects(rect, tolerance=metrics.border_tolerance) for rect in siblings):
            issues.append(GeometryIssue("manual_position_overlap", "手工位置与同级对象重叠，已恢复。", (view_id,)))
        else:
            node_rects[view_id] = candidate

    classified_edges = _classify_edges(projection, instance_scope, scopes)
    issues.extend(_validate_geometry(scope_rects, content_rects, node_rects, instance_scope, metrics))
    for scope in projection.scopes:
        if not scope.parent_scope_id:
            continue
        child_rect = scope_rects.get(scope.view_id)
        parent_content = content_rects.get(f"scope:{scope.parent_scope_id}")
        if child_rect is None or parent_content is None:
            issues.append(
                GeometryIssue(
                    "layout_scope_coordinate_missing",
                    "教材子框或父内容区缺少坐标。",
                    (scope.scope_id, scope.parent_scope_id),
                )
            )
        elif not parent_content.contains(child_rect, tolerance=metrics.border_tolerance):
            issues.append(
                GeometryIssue(
                    "geometry_scope_out_of_bounds",
                    "教材子框越出父框内容区。",
                    (scope.scope_id, scope.parent_scope_id),
                )
            )
    missing = sorted(
        instance.view_id for instance in projection.node_instances if instance.view_id not in node_rects
    )
    if missing:
        issues.append(GeometryIssue("layout_coordinate_missing", "投影实例缺少布局坐标。", tuple(missing)))
    scene = SceneGeometry(
        document_id=projection.document_id,
        document_revision=projection.document_revision,
        structure_fingerprint=projection.structure_fingerprint,
        renderer=metrics.renderer,
        specification_version=metrics.specification_version,
        scope_rects=scope_rects,
        scope_content_rects=content_rects,
        node_rects=node_rects,
        edge_instances=classified_edges,
        issues=issues,
        view_mode=projection.view_mode,
    )
    _finish_routes_and_readability(scene, metrics, started)
    return scene


def _layout_relations_degraded_scene(
    projection: ReviewViewProjection,
    metrics: RenderMetricsProfile,
    started: float,
) -> SceneGeometry:
    """Return a bounded diagnostic scene instead of freezing on an oversized full graph."""

    node_ids = sorted(item.view_id for item in projection.node_instances)
    columns = max(1, math.ceil(math.sqrt(max(len(node_ids), 1) * metrics.target_aspect_ratio)))
    x_step = metrics.card_width + metrics.rank_gap
    y_step = metrics.card_height + metrics.row_gap
    node_rects = {
        node_id: Rect(
            (index % columns) * x_step,
            (index // columns) * y_step,
            metrics.card_width,
            metrics.card_height,
        )
        for index, node_id in enumerate(node_ids)
    }
    classified_edges = [
        replace(edge, constraint_status="layout_budget_exceeded")
        for edge in projection.edge_instances
    ]
    routes: dict[str, EdgeRoute] = {}
    for edge in classified_edges:
        source = node_rects.get(edge.source_view_id)
        target = node_rects.get(edge.target_view_id)
        if source is None or target is None:
            points: tuple[tuple[float, float], ...] = ()
            source_name = target_name = ""
        else:
            source_point, source_name = _select_port(source, target.center)
            target_point, target_name = _select_port(target, source.center)
            points = (source_point, target_point)
        routes[edge.view_id] = EdgeRoute(
            edge.view_id,
            edge.relation_id,
            edge.source_view_id,
            edge.target_view_id,
            points,
            source_name,
            target_name,
            "budget_exceeded",
        )
    scene = SceneGeometry(
        document_id=projection.document_id,
        document_revision=projection.document_revision,
        structure_fingerprint=projection.structure_fingerprint,
        renderer=metrics.renderer,
        specification_version=metrics.specification_version,
        scope_rects={},
        scope_content_rects={},
        node_rects=node_rects,
        edge_instances=classified_edges,
        issues=[
            GeometryIssue(
                "layout_scale_degraded",
                "完整图超过当前布局预算；已返回有界诊断场景，路径未标记为合法成功。请筛选连通块或从知识点展开邻域。",
                (str(len(node_ids)), str(len(classified_edges))),
            )
        ],
        view_mode=VIEW_MODE_RELATIONS,
        engine="bounded_diagnostic_grid_v6",
        edge_routes=routes,
    )
    _place_edge_labels(scene, metrics)
    scene.readability = _readability_report(scene, metrics)
    scene.readability.timings_ms = {
        "total": round((time.perf_counter() - started) * 1000.0, 3)
    }
    _pack_relation_visual_groups(scene, projection, metrics)
    return scene


def _empty_scope(metrics: RenderMetricsProfile) -> _LocalLayout:
    height = metrics.scope_header_height + 2 * metrics.scope_padding_y
    return _LocalLayout(metrics.scope_min_width, height, {}, {}, {})


def _merge_shifted(target: dict[str, Rect], source: dict[str, Rect], dx: float, dy: float) -> None:
    for key, rect in source.items():
        target[key] = Rect(rect.x + dx, rect.y + dy, rect.width, rect.height)


def _rank_nodes(
    node_ids: list[str],
    edges: list[ProjectedEdgeInstance],
) -> tuple[dict[str, int], set[str]]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        relation_type = normalize_relation_type(edge.relation_type)
        if relation_type in _DIRECTIONAL_RELATIONS:
            adjacency[edge.source_view_id].add(edge.target_view_id)
    components = _strongly_connected_components(adjacency)
    component_by_node = {
        node_id: index for index, component in enumerate(components) for node_id in component
    }
    cycle_nodes = {
        node_id
        for component in components
        if len(component) > 1
        for node_id in component
    }
    dag: dict[int, set[int]] = {index: set() for index in range(len(components))}
    indegree = {index: 0 for index in dag}
    for source_id, targets in adjacency.items():
        source_component = component_by_node[source_id]
        for target_id in targets:
            target_component = component_by_node[target_id]
            if source_component == target_component or target_component in dag[source_component]:
                continue
            dag[source_component].add(target_component)
            indegree[target_component] += 1
    queue = sorted(index for index, value in indegree.items() if value == 0)
    component_rank = {index: 0 for index in dag}
    while queue:
        source = queue.pop(0)
        for target in sorted(dag[source]):
            component_rank[target] = max(component_rank[target], component_rank[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
                queue.sort()
    return {
        node_id: component_rank[component_by_node[node_id]] for node_id in node_ids
    }, cycle_nodes


def _strongly_connected_components(adjacency: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    results: list[list[str]] = []

    def visit(node_id: str) -> None:
        nonlocal index
        indexes[node_id] = index
        lowlinks[node_id] = index
        index += 1
        stack.append(node_id)
        on_stack.add(node_id)
        for target_id in sorted(adjacency.get(node_id, set())):
            if target_id not in indexes:
                visit(target_id)
                lowlinks[node_id] = min(lowlinks[node_id], lowlinks[target_id])
            elif target_id in on_stack:
                lowlinks[node_id] = min(lowlinks[node_id], indexes[target_id])
        if lowlinks[node_id] != indexes[node_id]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node_id:
                break
        results.append(sorted(component))

    for node_id in sorted(adjacency):
        if node_id not in indexes:
            visit(node_id)
    return results


def _classify_edges(
    projection: ReviewViewProjection,
    instance_scope: dict[str, str],
    scopes: dict[str, object],
) -> list[ProjectedEdgeInstance]:
    parent = {scope.scope_id: scope.parent_scope_id for scope in projection.scopes}
    directional_pairs = {
        (edge.source_node_id, edge.target_node_id)
        for edge in projection.edge_instances
        if normalize_relation_type(edge.relation_type) in _DIRECTIONAL_RELATIONS
    }
    local_directional: dict[str, dict[str, set[str]]] = {}
    for edge in projection.edge_instances:
        relation_type = normalize_relation_type(edge.relation_type)
        source_scope = instance_scope.get(edge.source_view_id, edge.source_node_id if edge.source_node_id in scopes else "")
        target_scope = instance_scope.get(edge.target_view_id, edge.target_node_id if edge.target_node_id in scopes else "")
        layout_scope = _lowest_common_scope(source_scope, target_scope, parent)
        if relation_type in _DIRECTIONAL_RELATIONS and source_scope == target_scope:
            local_directional.setdefault(source_scope, {}).setdefault(edge.source_view_id, set()).add(edge.target_view_id)
    cycle_edge_ids: set[str] = set()
    for adjacency in local_directional.values():
        all_nodes = set(adjacency)
        for targets in adjacency.values():
            all_nodes.update(targets)
        complete = {node_id: set(adjacency.get(node_id, set())) for node_id in all_nodes}
        component_by_node: dict[str, int] = {}
        for index, component in enumerate(_strongly_connected_components(complete)):
            for node_id in component:
                component_by_node[node_id] = index if len(component) > 1 else -1
        for edge in projection.edge_instances:
            if (
                edge.source_view_id in component_by_node
                and component_by_node[edge.source_view_id] >= 0
                and component_by_node[edge.source_view_id] == component_by_node.get(edge.target_view_id)
            ):
                cycle_edge_ids.add(edge.relation_id)
    results: list[ProjectedEdgeInstance] = []
    for edge in projection.edge_instances:
        relation_type = normalize_relation_type(edge.relation_type)
        source_scope = instance_scope.get(edge.source_view_id, edge.source_node_id if edge.source_node_id in scopes else "")
        target_scope = instance_scope.get(edge.target_view_id, edge.target_node_id if edge.target_node_id in scopes else "")
        layout_scope = _lowest_common_scope(source_scope, target_scope, parent)
        if edge.relation_family == "unresolved":
            status = "unresolved_semantics"
        elif relation_type in _SYMMETRIC_RELATIONS:
            conflict = (
                (edge.source_node_id, edge.target_node_id) in directional_pairs
                or (edge.target_node_id, edge.source_node_id) in directional_pairs
            )
            status = "symmetry_conflict" if conflict else "same_rank"
        elif edge.relation_id in cycle_edge_ids:
            status = "semantic_cycle"
        elif source_scope == target_scope and source_scope:
            status = "rank_enforced"
        elif layout_scope:
            status = "cross_scope"
        else:
            status = "order_conflict"
        results.append(replace(edge, layout_scope=layout_scope, constraint_status=status))
    return results


def _lowest_common_scope(source: str, target: str, parent: dict[str, str]) -> str:
    if not source or not target:
        return ""
    source_path: list[str] = []
    current = source
    seen: set[str] = set()
    while current and current not in seen:
        source_path.append(current)
        seen.add(current)
        current = parent.get(current, "")
    target_ancestors: set[str] = set()
    current = target
    while current and current not in target_ancestors:
        target_ancestors.add(current)
        current = parent.get(current, "")
    return next((scope_id for scope_id in source_path if scope_id in target_ancestors), "")


def _validate_geometry(
    scope_rects: dict[str, Rect],
    content_rects: dict[str, Rect],
    node_rects: dict[str, Rect],
    instance_scope: dict[str, str],
    metrics: RenderMetricsProfile,
) -> list[GeometryIssue]:
    issues: list[GeometryIssue] = []
    for view_id, rect in node_rects.items():
        parent_id = instance_scope.get(view_id, "")
        content = content_rects.get(f"scope:{parent_id}")
        if content is not None and not content.contains(rect, tolerance=metrics.border_tolerance):
            issues.append(GeometryIssue("geometry_child_out_of_bounds", "知识卡片越出父框内容区。", (view_id, parent_id)))
    by_parent: dict[str, list[str]] = {}
    for view_id, scope_id in instance_scope.items():
        if view_id in node_rects:
            by_parent.setdefault(scope_id, []).append(view_id)
    for scope_id, view_ids in by_parent.items():
        for index, left_id in enumerate(sorted(view_ids)):
            for right_id in sorted(view_ids)[index + 1 :]:
                if node_rects[left_id].intersects(node_rects[right_id], tolerance=metrics.border_tolerance):
                    issues.append(GeometryIssue("geometry_sibling_overlap", "同级知识卡片重叠。", (left_id, right_id, scope_id)))
    for scope_key, rect in scope_rects.items():
        content = content_rects.get(scope_key)
        if content is not None and not rect.contains(content, tolerance=metrics.border_tolerance):
            issues.append(GeometryIssue("geometry_content_out_of_bounds", "内容区越出教材框。", (scope_key,)))
    return issues


def _layout_relations_scene(
    projection: ReviewViewProjection,
    metrics: RenderMetricsProfile,
    overrides: dict[str, dict[str, float]],
    started: float,
) -> SceneGeometry:
    node_ids = sorted(item.view_id for item in projection.node_instances)
    node_rects, _width, _height = _layout_node_set(
        node_ids,
        projection.edge_instances,
        metrics,
    )
    _apply_reading_sequence_positions(node_rects, projection, metrics)

    issues: list[GeometryIssue] = []
    for view_id, raw in sorted(overrides.items()):
        if view_id not in node_rects:
            continue
        original = node_rects[view_id]
        try:
            candidate = Rect(float(raw["x"]), float(raw["y"]), original.width, original.height)
        except (KeyError, TypeError, ValueError):
            issues.append(GeometryIssue("manual_position_invalid", "手工坐标无效，已忽略。", (view_id,)))
            continue
        siblings = [rect for key, rect in node_rects.items() if key != view_id]
        if any(candidate.intersects(rect, tolerance=metrics.border_tolerance) for rect in siblings):
            issues.append(GeometryIssue("manual_position_overlap", "手工位置与其他知识卡片重叠，已恢复。", (view_id,)))
        else:
            node_rects[view_id] = candidate

    classified_edges = _classify_edges(projection, {}, {})
    scene = SceneGeometry(
        document_id=projection.document_id,
        document_revision=projection.document_revision,
        structure_fingerprint=projection.structure_fingerprint,
        renderer=metrics.renderer,
        specification_version=metrics.specification_version,
        scope_rects={},
        scope_content_rects={},
        node_rects=node_rects,
        edge_instances=classified_edges,
        issues=issues,
        view_mode=VIEW_MODE_RELATIONS,
    )
    _finish_routes_and_readability(scene, metrics, started)
    _pack_relation_visual_groups(scene, projection, metrics)
    return scene


def _layout_relations_scene_elk(
    projection: ReviewViewProjection,
    metrics: RenderMetricsProfile,
    overrides: dict[str, dict[str, float]],
    started: float,
) -> SceneGeometry:
    from .elk_backend import ElkBackendError, ElkLayoutJob, layout_graph_batch

    if overrides:
        raise ElkBackendError("manual_positions_require_reroute")
    template_rects = {
        item.view_id: Rect(0.0, 0.0, metrics.card_width, metrics.card_height)
        for item in projection.node_instances
    }
    routing_only_relation_ids = {
        relation_id
        for sequence in projection.reading_sequences
        for relation_id in sequence.routing_only_relation_ids
    }
    layout_edges = [
        edge
        for edge in projection.edge_instances
        if edge.relation_id not in routing_only_relation_ids
    ]
    node_order = {
        item.node_id: item.ordinal
        for sequence in projection.reading_sequences
        for item in sequence.items
    }
    order_by_view = {
        instance.view_id: node_order[instance.node_id]
        for instance in projection.node_instances
        if instance.node_id in node_order
    }
    ordered_views = {
        instance.view_id
        for instance in projection.node_instances
        if instance.node_id in node_order
    }
    base_graph = _undirected_graph(
        list(template_rects),
        projection.edge_instances,
    )
    components = [sorted(component) for component in nx.connected_components(base_graph)]
    components.sort(key=lambda values: (-len(values), values[0] if values else ""))
    jobs: list[ElkLayoutJob] = []
    edges_by_component: dict[str, list[ProjectedEdgeInstance]] = {}
    for index, component in enumerate(components):
        component_id = f"component-{index:04d}"
        member_set = set(component)
        component_edges = [
            edge
            for edge in layout_edges
            if edge.source_view_id in member_set and edge.target_view_id in member_set
        ]
        edges_by_component[component_id] = component_edges
        jobs.append(
            ElkLayoutJob(
                component_id=component_id,
                node_rects={view_id: template_rects[view_id] for view_id in component},
                edges=tuple(component_edges),
                node_order={view_id: order_by_view[view_id] for view_id in component if view_id in order_by_view},
            )
        )
    result = layout_graph_batch(jobs, metrics, timeout_seconds=8.0)
    local_units: list[tuple[dict[str, Rect], float, float]] = []
    component_fallbacks: list[str] = []
    for index, component in enumerate(components):
        component_id = f"component-{index:04d}"
        component_result = result.component_results.get(component_id)
        component_all_edges = [
            edge
            for edge in projection.edge_instances
            if edge.source_view_id in component and edge.target_view_id in component
        ]
        if component_result is not None and not _has_unordered_tall_column(
            component_result.node_rects,
            ordered_views,
        ):
            positions = _normalize_positions(component_result.node_rects)
        else:
            positions = _layout_component(component, component_all_edges, metrics)
            component_fallbacks.append(
                f"{component_id}:{result.failures.get(component_id, 'unordered_column_height_exceeded')}"
            )
        width = max((rect.right for rect in positions.values()), default=metrics.card_width)
        height = max((rect.bottom for rect in positions.values()), default=metrics.card_height)
        local_units.append((positions, width, height))
    node_rects, _width, _height = _pack_local_units(local_units, metrics)
    _apply_reading_sequence_positions(node_rects, projection, metrics)
    classified_edges = _classify_edges(projection, {}, {})
    scene = SceneGeometry(
        document_id=projection.document_id,
        document_revision=projection.document_revision,
        structure_fingerprint=projection.structure_fingerprint,
        renderer=metrics.renderer,
        specification_version=metrics.specification_version,
        scope_rects={},
        scope_content_rects={},
        node_rects=node_rects,
        edge_instances=classified_edges,
        issues=[
            GeometryIssue(
                "layout_component_fallback",
                "部分关系组件未满足 ELK 约束，已在同一请求内使用确定性 Python 布局。",
                tuple(component_fallbacks),
            )
        ] if component_fallbacks else [],
        view_mode=VIEW_MODE_RELATIONS,
        engine=(
            "elkjs_0_12_0_batch_plus_python_component_router_v6"
            if component_fallbacks
            else "elkjs_0_12_0_batch_plus_python_router_v6"
        ),
    )
    _finish_routes_and_readability(scene, metrics, started)
    scene.readability.timings_ms.update(
        {
            "elk_reported": round(result.elapsed_ms, 3),
            "node_process_count": float(result.process_count),
            "node_batch_count": float(result.batch_count),
            "component_count": float(len(components)),
            "component_fallback_count": float(len(component_fallbacks)),
        }
    )
    _pack_relation_visual_groups(scene, projection, metrics)
    if scene.readability.hard_violations:
        raise ElkBackendError(
            f"readability_hard_violations_{scene.readability.hard_violations}"
        )
    return scene


def _apply_reading_sequence_positions(
    node_rects: dict[str, Rect],
    projection: ReviewViewProjection,
    metrics: RenderMetricsProfile,
) -> None:
    """Give evidence-backed fanout items one deterministic vertical reading band."""

    view_by_node = {item.node_id: item.view_id for item in projection.node_instances}
    for sequence in sorted(projection.reading_sequences, key=lambda item: item.sequence_id):
        parent_view_id = view_by_node.get(sequence.parent_node_id)
        parent = node_rects.get(parent_view_id or "")
        ordered_view_ids = [
            view_by_node[item.node_id]
            for item in sequence.items
            if item.node_id in view_by_node and view_by_node[item.node_id] in node_rects
        ]
        if parent is None or len(ordered_view_ids) < 2:
            continue
        total_height = (
            sum(node_rects[view_id].height for view_id in ordered_view_ids)
            + metrics.row_gap * (len(ordered_view_ids) - 1)
        )
        y_start = max(0.0, parent.center[1] - total_height / 2.0)
        x = parent.right + max(metrics.rank_gap, 96.0)
        excluded = {parent_view_id, *ordered_view_ids}
        while True:
            candidates: list[Rect] = []
            y_cursor = y_start
            for view_id in ordered_view_ids:
                current = node_rects[view_id]
                candidates.append(Rect(x, y_cursor, current.width, current.height))
                y_cursor += current.height + metrics.row_gap
            blockers = [
                rect
                for view_id, rect in node_rects.items()
                if view_id not in excluded
                and any(_inflate(candidate, metrics.route_clearance).intersects(rect) for candidate in candidates)
            ]
            if not blockers:
                break
            x = max(rect.right for rect in blockers) + max(metrics.rank_gap, 96.0)
        y_cursor = y_start
        for view_id in ordered_view_ids:
            current = node_rects[view_id]
            node_rects[view_id] = Rect(x, y_cursor, current.width, current.height)
            y_cursor += current.height + metrics.row_gap


def _layout_node_set(
    node_ids: list[str],
    edges: list[ProjectedEdgeInstance],
    metrics: RenderMetricsProfile,
) -> tuple[dict[str, Rect], float, float]:
    graph = nx.Graph()
    graph.add_nodes_from(node_ids)
    graph.add_edges_from(
        (edge.source_view_id, edge.target_view_id)
        for edge in edges
        if edge.source_view_id in graph and edge.target_view_id in graph
    )
    components = [sorted(component) for component in nx.connected_components(graph)]
    components.sort(key=lambda values: (-len(values), values[0] if values else ""))
    local_units: list[tuple[dict[str, Rect], float, float]] = []
    for component in components:
        component_edges = [
            edge
            for edge in edges
            if edge.source_view_id in component and edge.target_view_id in component
        ]
        positions = _layout_component(component, component_edges, metrics)
        width = max((rect.right for rect in positions.values()), default=metrics.card_width)
        height = max((rect.bottom for rect in positions.values()), default=metrics.card_height)
        local_units.append((positions, width, height))
    return _pack_local_units(local_units, metrics)


def _pack_local_units(
    local_units: list[tuple[dict[str, Rect], float, float]],
    metrics: RenderMetricsProfile,
) -> tuple[dict[str, Rect], float, float]:
    total_area = sum(
        (width + metrics.component_gap) * (height + metrics.component_gap)
        for _positions, width, height in local_units
    )
    widest = max((width for _positions, width, _height in local_units), default=0.0)
    target_width = max(widest, math.sqrt(max(total_area, 1.0) * metrics.target_aspect_ratio))
    node_rects: dict[str, Rect] = {}
    x_cursor = 0.0
    y_cursor = 0.0
    row_height = 0.0
    for positions, width, height in local_units:
        if x_cursor and x_cursor + width > target_width:
            x_cursor = 0.0
            y_cursor += row_height + metrics.component_gap
            row_height = 0.0
        _merge_shifted(node_rects, positions, x_cursor, y_cursor)
        x_cursor += width + metrics.component_gap
        row_height = max(row_height, height)
    actual_width = max((rect.right for rect in node_rects.values()), default=0.0)
    actual_height = max((rect.bottom for rect in node_rects.values()), default=0.0)
    return node_rects, actual_width, actual_height


def _normalize_positions(positions: dict[str, Rect]) -> dict[str, Rect]:
    if not positions:
        return {}
    min_x = min(rect.x for rect in positions.values())
    min_y = min(rect.y for rect in positions.values())
    return {
        view_id: Rect(rect.x - min_x, rect.y - min_y, rect.width, rect.height)
        for view_id, rect in positions.items()
    }


def _has_unordered_tall_column(
    positions: dict[str, Rect],
    ordered_views: set[str],
) -> bool:
    columns: dict[float, list[str]] = {}
    for view_id, rect in positions.items():
        columns.setdefault(round(rect.x, 3), []).append(view_id)
    return any(
        len(view_ids) > 5 and len(set(view_ids) - ordered_views) > 5
        for view_ids in columns.values()
    )


def _layout_component(
    node_ids: list[str],
    edges: list[ProjectedEdgeInstance],
    metrics: RenderMetricsProfile,
) -> dict[str, Rect]:
    if not node_ids:
        return {}
    if len(node_ids) == 1:
        return {node_ids[0]: Rect(0.0, 0.0, metrics.card_width, metrics.card_height)}
    undirected = _undirected_graph(node_ids, edges)
    hub_id, hub_degree = max(undirected.degree, key=lambda item: (item[1], item[0]))
    if hub_degree >= max(6, math.ceil((len(node_ids) - 1) * 0.6)):
        return _layout_star_component(node_ids, hub_id, metrics)
    ranks, _cycle_nodes = _rank_nodes(node_ids, edges)
    rank_values = set(ranks.values())
    directional_count = sum(
        normalize_relation_type(edge.relation_type) in _DIRECTIONAL_RELATIONS
        and edge.relation_family != "unresolved"
        for edge in edges
    )
    if len(rank_values) > 1 and directional_count >= max(2, len(node_ids) // 3):
        groups: dict[int, list[str]] = {}
        spring = nx.spring_layout(undirected, seed=41, iterations=120, weight="weight")
        for node_id in node_ids:
            groups.setdefault(ranks[node_id], []).append(node_id)
        for values in groups.values():
            values.sort(key=lambda node_id: (float(spring[node_id][1]), node_id))
        # A semantic rank is a left-to-right band, not an infinitely tall column.
        # Keep the rank order while wrapping wide ranks into deterministic subcolumns.
        max_rows_per_column = 5
        band_columns = {
            rank: max(1, math.ceil(len(values) / max_rows_per_column))
            for rank, values in groups.items()
        }
        band_rows = {
            rank: max(1, math.ceil(len(values) / band_columns[rank]))
            for rank, values in groups.items()
        }
        max_rows = max(band_rows.values())
        result: dict[str, Rect] = {}
        rank_x = 0.0
        x_step = metrics.card_width + metrics.rank_gap
        y_step = metrics.card_height + metrics.row_gap
        for rank in sorted(groups):
            values = groups[rank]
            column_count = band_columns[rank]
            row_count = band_rows[rank]
            y_offset = (max_rows - row_count) * y_step / 2.0
            rows = [
                values[index : index + column_count]
                for index in range(0, len(values), column_count)
            ]
            for row, row_nodes in enumerate(rows):
                row_nodes.sort(key=lambda node_id: (float(spring[node_id][0]), node_id))
                x_offset = (column_count - len(row_nodes)) * x_step / 2.0
                for column, node_id in enumerate(row_nodes):
                    result[node_id] = Rect(
                        rank_x + x_offset + column * x_step,
                        y_offset + row * y_step,
                        metrics.card_width,
                        metrics.card_height,
                    )
            rank_x += column_count * x_step
        return result

    spring = nx.spring_layout(undirected, seed=41, iterations=180, weight="weight")
    x_step = metrics.card_width + max(metrics.rank_gap, 96.0)
    y_step = metrics.card_height + max(metrics.row_gap, 54.0)
    columns = max(
        2,
        min(
            len(node_ids),
            math.ceil(math.sqrt(len(node_ids) * metrics.target_aspect_ratio * y_step / x_step)),
        ),
    )
    ordered = sorted(node_ids, key=lambda node_id: (float(spring[node_id][1]), float(spring[node_id][0]), node_id))
    rows = [ordered[index : index + columns] for index in range(0, len(ordered), columns)]
    result: dict[str, Rect] = {}
    for row_index, row_nodes in enumerate(rows):
        row_nodes.sort(key=lambda node_id: (float(spring[node_id][0]), node_id))
        offset = (columns - len(row_nodes)) * x_step / 2.0
        for column, node_id in enumerate(row_nodes):
            result[node_id] = Rect(
                offset + column * x_step,
                row_index * y_step,
                metrics.card_width,
                metrics.card_height,
            )
    return result


def _layout_star_component(
    node_ids: list[str],
    hub_id: str,
    metrics: RenderMetricsProfile,
) -> dict[str, Rect]:
    """Place a high-degree hub inside a rectangular ring instead of a long leaf rank."""

    leaves = sorted(node_id for node_id in node_ids if node_id != hub_id)
    side_capacity = max(2, math.ceil(len(leaves) / 4))
    x_step = metrics.card_width + max(metrics.rank_gap, 96.0)
    y_step = metrics.card_height + max(metrics.row_gap, 54.0)
    width_cells = side_capacity + 1
    height_cells = max(4, math.ceil(len(leaves) / max(side_capacity * 2, 1)) + 2)
    while 2 * width_cells + 2 * (height_cells - 2) < len(leaves):
        height_cells += 1
    slots: list[tuple[int, int]] = []
    slots.extend((column, 0) for column in range(width_cells))
    slots.extend((column, height_cells - 1) for column in range(width_cells))
    slots.extend((0, row) for row in range(1, height_cells - 1))
    slots.extend((width_cells - 1, row) for row in range(1, height_cells - 1))
    slots = slots[: len(leaves)]
    result = {
        hub_id: Rect(
            (width_cells - 1) * x_step / 2.0,
            (height_cells - 1) * y_step / 2.0,
            metrics.card_width,
            metrics.card_height,
        )
    }
    for node_id, (column, row) in zip(leaves, slots):
        result[node_id] = Rect(
            column * x_step,
            row * y_step,
            metrics.card_width,
            metrics.card_height,
        )
    return result


def _undirected_graph(
    node_ids: list[str], edges: list[ProjectedEdgeInstance]
) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(node_ids)
    for edge in edges:
        if edge.source_view_id not in graph or edge.target_view_id not in graph:
            continue
        weight = 2.0 if normalize_relation_type(edge.relation_type) in {"prerequisite", "progressive"} else 1.0
        graph.add_edge(edge.source_view_id, edge.target_view_id, weight=weight)
    return graph


def _finish_routes_and_readability(
    scene: SceneGeometry,
    metrics: RenderMetricsProfile,
    started: float,
) -> None:
    route_started = time.perf_counter()
    all_rects = scene.all_rects
    component_by_view, members_by_component = _routing_components(scene)
    occupied_by_component: dict[
        str, list[tuple[tuple[float, float], tuple[float, float], str]]
    ] = {}
    allocated_ports = _allocate_edge_ports(scene.edge_instances, all_rects, metrics)
    for edge in sorted(scene.edge_instances, key=_edge_route_priority):
        source = all_rects.get(edge.source_view_id)
        target = all_rects.get(edge.target_view_id)
        if source is None or target is None:
            scene.edge_routes[edge.view_id] = EdgeRoute(
                edge.view_id,
                edge.relation_id,
                edge.source_view_id,
                edge.target_view_id,
                (),
                "",
                "",
                "missing_endpoint",
            )
            scene.issues.append(GeometryIssue("edge_route_missing_endpoint", "关系路径缺少端点坐标。", (edge.relation_id,)))
            continue
        source_port, source_name, target_port, target_name = allocated_ports.get(
            edge.view_id,
            (*_select_port(source, target.center), *_select_port(target, source.center)),
        )
        display_spec = relation_display_spec(edge.relation_family, edge.relation_type)
        component_key = component_by_view.get(edge.source_view_id, "__all__")
        if component_by_view.get(edge.target_view_id, component_key) != component_key:
            component_key = "__all__"
        occupied_segments = occupied_by_component.setdefault(component_key, [])
        if edge.source_view_id == edge.target_view_id:
            gap = metrics.route_clearance + metrics.route_lane_gap
            source_normal = (1.0, 0.0)
            target_normal = (0.0, -1.0)
            points = (
                (source.right + metrics.arrow_tip_gap, source.y + source.height * 0.35),
                (source.right + gap * 2, source.y + source.height * 0.35),
                (source.right + gap * 2, source.y - gap * 2),
                (source.x + source.width * 0.65, source.y - gap * 2),
                (source.x + source.width * 0.65, source.y - metrics.arrow_tip_gap),
            )
            status = "routed"
            source_escape = points[1]
            target_escape = points[-2]
        else:
            obstacles = [
                _inflate(rect, metrics.route_clearance)
                for view_id, rect in all_rects.items()
                if view_id.startswith("node:")
                and (
                    component_key == "__all__"
                    or view_id in members_by_component.get(component_key, set())
                )
            ]
            source_normal = _side_normal(source_name)
            target_normal = _side_normal(target_name)
            source_tip_gap = metrics.arrow_tip_gap if display_spec.arrow_mode == "both" else 0.0
            target_tip_gap = metrics.arrow_tip_gap if display_spec.arrow_mode in {"end", "both"} else 0.0
            source_escape = _offset_point(
                source_port,
                source_normal,
                max(metrics.terminal_stub_length, metrics.route_clearance + metrics.route_lane_gap)
                + source_tip_gap,
            )
            target_escape = _offset_point(
                target_port,
                target_normal,
                max(metrics.terminal_stub_length, metrics.route_clearance + metrics.route_lane_gap)
                + target_tip_gap,
            )
            source_draw = _offset_point(
                source_port,
                source_normal,
                source_tip_gap,
            )
            target_draw = _offset_point(
                target_port,
                target_normal,
                target_tip_gap,
            )
            core = _orthogonal_route(
                source_escape,
                target_escape,
                obstacles,
                occupied_segments,
                metrics,
            )
            status = "routed" if core else "failed"
            if core:
                points = (source_draw, *core, target_draw)
                points = tuple(
                    point
                    for index, point in enumerate(points)
                    if index == 0 or point != points[index - 1]
                )
            else:
                points = (source_draw, target_draw)
                scene.issues.append(
                    GeometryIssue(
                        "edge_route_failed",
                        "关系没有找到合法避障通道，保留诊断路径且不标记为成功。",
                        (edge.relation_id,),
                    )
                )
        source_arrow = (
            _arrow_polygon(points[0], points[1], metrics)
            if status == "routed" and len(points) >= 2 and display_spec.arrow_mode == "both"
            else ()
        )
        target_arrow = (
            _arrow_polygon(points[-1], points[-2], metrics)
            if status == "routed" and len(points) >= 2 and display_spec.arrow_mode in {"end", "both"}
            else ()
        )
        route = EdgeRoute(
            edge.view_id,
            edge.relation_id,
            edge.source_view_id,
            edge.target_view_id,
            tuple(points),
            source_name,
            target_name,
            status,
            source_escape,
            target_escape,
            source_normal,
            target_normal,
            source_arrow,
            target_arrow,
        )
        scene.edge_routes[edge.view_id] = route
        if status == "routed":
            for first, second in zip(route.points, route.points[1:]):
                occupied_segments.append((first, second, edge.view_id))
    route_finished = time.perf_counter()
    _place_edge_labels(scene, metrics)
    label_finished = time.perf_counter()
    scene.readability = _readability_report(scene, metrics)
    validate_finished = time.perf_counter()
    scene.readability.timings_ms = {
        "layout": round((route_started - started) * 1000.0, 3),
        "routing": round((route_finished - route_started) * 1000.0, 3),
        "labels": round((label_finished - route_finished) * 1000.0, 3),
        "validation": round((validate_finished - label_finished) * 1000.0, 3),
        "total": round((validate_finished - started) * 1000.0, 3),
    }


def _routing_components(
    scene: SceneGeometry,
) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Scope route obstacles and occupied lanes to independent relation components."""

    if scene.view_mode != VIEW_MODE_RELATIONS:
        all_nodes = {view_id for view_id in scene.node_rects if view_id.startswith("node:")}
        return ({view_id: "__all__" for view_id in all_nodes}, {"__all__": all_nodes})
    graph = nx.Graph()
    graph.add_nodes_from(
        view_id for view_id in scene.node_rects if view_id.startswith("node:")
    )
    for edge in scene.edge_instances:
        if edge.source_view_id in graph and edge.target_view_id in graph:
            graph.add_edge(edge.source_view_id, edge.target_view_id)
    component_by_view: dict[str, str] = {}
    members_by_component: dict[str, set[str]] = {}
    components = [sorted(component) for component in nx.connected_components(graph)]
    components.sort(key=lambda members: (-len(members), members))
    for index, members in enumerate(components):
        component_id = f"route-component-{index:04d}"
        member_set = set(members)
        members_by_component[component_id] = member_set
        component_by_view.update({view_id: component_id for view_id in members})
    return component_by_view, members_by_component


def _fits_partitioned_large_graph_budget(projection: ReviewViewProjection) -> bool:
    """Allow many bounded independent components without invoking one global search."""

    node_ids = [item.view_id for item in projection.node_instances]
    if len(node_ids) > 1000 or len(projection.edge_instances) > 2000:
        return False
    graph = _undirected_graph(node_ids, projection.edge_instances)
    components = list(nx.connected_components(graph))
    if len(components) < 2 or max((len(component) for component in components), default=0) > 12:
        return False
    component_by_view = {
        view_id: index
        for index, component in enumerate(components)
        for view_id in component
    }
    edge_counts = [0] * len(components)
    for edge in projection.edge_instances:
        component_index = component_by_view.get(edge.source_view_id)
        if component_index is None or component_index != component_by_view.get(edge.target_view_id):
            return False
        edge_counts[component_index] += 1
    return max(edge_counts, default=0) <= 24


def _edge_route_priority(edge: ProjectedEdgeInstance) -> tuple[int, int, str]:
    relation_type = normalize_relation_type(edge.relation_type)
    semantic_priority = {
        "contains": 0,
        "explains": 1,
        "prerequisite": 2,
        "progressive": 2,
        "derives_to": 3,
    }.get(relation_type, 2)
    unresolved = 1 if edge.relation_family == "unresolved" else 0
    return semantic_priority, unresolved, edge.view_id


def _select_port(rect: Rect, other_center: tuple[float, float]) -> tuple[tuple[float, float], str]:
    center_x, center_y = rect.center
    dx = other_center[0] - center_x
    dy = other_center[1] - center_y
    if abs(dx) >= abs(dy):
        return ((rect.right, center_y), "right") if dx >= 0 else ((rect.x, center_y), "left")
    return ((center_x, rect.bottom), "bottom") if dy >= 0 else ((center_x, rect.y), "top")


def _allocate_edge_ports(
    edges: list[ProjectedEdgeInstance],
    rects: dict[str, Rect],
    metrics: RenderMetricsProfile,
) -> dict[str, tuple[tuple[float, float], str, tuple[float, float], str]]:
    """Allocate stable, distinct ports so incident edges do not share long stubs."""

    sides: dict[str, tuple[str, str]] = {}
    for edge in sorted(edges, key=lambda item: item.view_id):
        source = rects.get(edge.source_view_id)
        target = rects.get(edge.target_view_id)
        if source is None or target is None or edge.source_view_id == edge.target_view_id:
            continue
        source_side, target_side = _select_edge_sides(source, target)
        sides[edge.view_id] = (source_side, target_side)
    sides = _spread_congested_port_sides(sides, edges, rects, metrics)
    groups: dict[tuple[str, str], list[tuple[float, str, bool]]] = {}
    for edge in sorted(edges, key=lambda item: item.view_id):
        if edge.view_id not in sides:
            continue
        source = rects[edge.source_view_id]
        target = rects[edge.target_view_id]
        source_side, target_side = sides[edge.view_id]
        source_order = target.center[1] if source_side in {"left", "right"} else target.center[0]
        target_order = source.center[1] if target_side in {"left", "right"} else source.center[0]
        groups.setdefault((edge.source_view_id, source_side), []).append(
            (source_order, edge.view_id, True)
        )
        groups.setdefault((edge.target_view_id, target_side), []).append(
            (target_order, edge.view_id, False)
        )
    endpoint_points: dict[tuple[str, bool], tuple[float, float]] = {}
    for (view_id, side), entries in sorted(groups.items()):
        rect = rects[view_id]
        ordered = sorted(entries, key=lambda item: (item[0], item[1], item[2]))
        for index, (_order, edge_id, is_source) in enumerate(ordered):
            fraction = (index + 1) / (len(ordered) + 1)
            endpoint_points[(edge_id, is_source)] = _port_on_side(rect, side, fraction)
    result: dict[str, tuple[tuple[float, float], str, tuple[float, float], str]] = {}
    for edge_id, (source_side, target_side) in sides.items():
        result[edge_id] = (
            endpoint_points[(edge_id, True)],
            source_side,
            endpoint_points[(edge_id, False)],
            target_side,
        )
    return result


def _spread_congested_port_sides(
    sides: dict[str, tuple[str, str]],
    edges: list[ProjectedEdgeInstance],
    rects: dict[str, Rect],
    metrics: RenderMetricsProfile,
) -> dict[str, tuple[str, str]]:
    """Move overflow endpoints to adjacent sides before ports become indistinguishable."""

    edge_by_id = {edge.view_id: edge for edge in edges}
    endpoints: dict[str, list[tuple[str, bool, str, float]]] = {}
    for edge_id, (source_side, target_side) in sides.items():
        edge = edge_by_id[edge_id]
        source = rects[edge.source_view_id]
        target = rects[edge.target_view_id]
        endpoints.setdefault(edge.source_view_id, []).append(
            (
                edge_id,
                True,
                source_side,
                target.center[1] if source_side in {"left", "right"} else target.center[0],
            )
        )
        endpoints.setdefault(edge.target_view_id, []).append(
            (
                edge_id,
                False,
                target_side,
                source.center[1] if target_side in {"left", "right"} else source.center[0],
            )
        )
    result = dict(sides)
    alternatives = {
        "right": ("right", "top", "bottom", "left"),
        "left": ("left", "top", "bottom", "right"),
        "top": ("top", "left", "right", "bottom"),
        "bottom": ("bottom", "left", "right", "top"),
    }
    for view_id, values in sorted(endpoints.items()):
        rect = rects[view_id]
        capacities = {
            "left": max(1, int(rect.height // metrics.port_min_spacing) - 1),
            "right": max(1, int(rect.height // metrics.port_min_spacing) - 1),
            "top": max(1, int(rect.width // metrics.port_min_spacing) - 1),
            "bottom": max(1, int(rect.width // metrics.port_min_spacing) - 1),
        }
        loads = {side: 0 for side in capacities}
        for edge_id, is_source, preferred, _order in sorted(
            values,
            key=lambda item: (item[2], item[3], item[0], item[1]),
        ):
            choices = alternatives[preferred]
            available = [side for side in choices if loads[side] < capacities[side]]
            if preferred in available:
                chosen = preferred
            else:
                chosen = min(
                    available or list(choices),
                    key=lambda side: (
                        loads[side] / capacities[side],
                        choices.index(side),
                        side,
                    ),
                )
            loads[chosen] += 1
            source_side, target_side = result[edge_id]
            result[edge_id] = (
                chosen if is_source else source_side,
                target_side if is_source else chosen,
            )
    return result


def _select_edge_sides(source: Rect, target: Rect) -> tuple[str, str]:
    source_x, source_y = source.center
    target_x, target_y = target.center
    dx = target_x - source_x
    dy = target_y - source_y
    if abs(dx) >= abs(dy):
        return ("right", "left") if dx >= 0 else ("left", "right")
    # Closely stacked nodes need a shared outer corridor. Bottom-to-top escape
    # stubs would collide when the visual row gap is smaller than two stubs.
    if abs(dx) <= max(source.width, target.width) * 0.6:
        return ("right", "right") if dx >= -1e-6 else ("left", "left")
    return ("bottom", "top") if dy >= 0 else ("top", "bottom")


def _port_on_side(rect: Rect, side: str, fraction: float) -> tuple[float, float]:
    if side == "left":
        return rect.x, rect.y + rect.height * fraction
    if side == "right":
        return rect.right, rect.y + rect.height * fraction
    if side == "top":
        return rect.x + rect.width * fraction, rect.y
    return rect.x + rect.width * fraction, rect.bottom


def _side_normal(side: str) -> tuple[float, float]:
    return {
        "left": (-1.0, 0.0),
        "right": (1.0, 0.0),
        "top": (0.0, -1.0),
        "bottom": (0.0, 1.0),
    }.get(side, (0.0, 0.0))


def _offset_point(
    point: tuple[float, float],
    normal: tuple[float, float],
    distance: float,
) -> tuple[float, float]:
    return point[0] + normal[0] * distance, point[1] + normal[1] * distance


def _arrow_polygon(
    tip: tuple[float, float],
    tail: tuple[float, float],
    metrics: RenderMetricsProfile,
) -> tuple[tuple[float, float], ...]:
    dx = tip[0] - tail[0]
    dy = tip[1] - tail[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return ()
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    base_x = tip[0] - ux * metrics.arrow_length
    base_y = tip[1] - uy * metrics.arrow_length
    return (
        tip,
        (
            base_x + nx * metrics.arrow_half_width,
            base_y + ny * metrics.arrow_half_width,
        ),
        (
            base_x - nx * metrics.arrow_half_width,
            base_y - ny * metrics.arrow_half_width,
        ),
    )


def _inflate(rect: Rect, amount: float) -> Rect:
    return Rect(rect.x - amount, rect.y - amount, rect.width + 2 * amount, rect.height + 2 * amount)


def _orthogonal_route(
    start: tuple[float, float],
    end: tuple[float, float],
    obstacles: list[Rect],
    occupied: list[tuple[tuple[float, float], tuple[float, float], str]],
    metrics: RenderMetricsProfile,
) -> tuple[tuple[float, float], ...]:
    fast_route = _fast_orthogonal_route(start, end, obstacles, occupied, metrics)
    if fast_route:
        return fast_route
    margin = metrics.route_clearance + metrics.route_lane_gap
    xs = {start[0], end[0]}
    ys = {start[1], end[1]}
    for rect in obstacles:
        # Keep one extra parallel lane around each inflated obstacle. This gives the
        # congestion-aware search a real alternative instead of forcing collinear edges.
        lane = metrics.route_lane_gap
        xs.update((rect.x - lane, rect.x, rect.right, rect.right + lane))
        ys.update((rect.y - lane, rect.y, rect.bottom, rect.bottom + lane))
    if obstacles:
        xs.update((min(rect.x for rect in obstacles) - 2 * margin, max(rect.right for rect in obstacles) + 2 * margin))
        ys.update((min(rect.y for rect in obstacles) - 2 * margin, max(rect.bottom for rect in obstacles) + 2 * margin))
    x_values = sorted(xs)
    y_values = sorted(ys)
    points = {
        (x, y)
        for x in x_values
        for y in y_values
        if (x, y) in {start, end} or not any(_point_inside((x, y), rect) for rect in obstacles)
    }
    adjacency: dict[tuple[float, float], list[tuple[float, float]]] = {point: [] for point in points}
    by_x: dict[float, list[tuple[float, float]]] = {}
    by_y: dict[float, list[tuple[float, float]]] = {}
    for point in points:
        by_x.setdefault(point[0], []).append(point)
        by_y.setdefault(point[1], []).append(point)
    for values in by_x.values():
        values.sort(key=lambda point: point[1])
        _connect_visible_neighbors(values, adjacency, obstacles)
    for values in by_y.values():
        values.sort(key=lambda point: point[0])
        _connect_visible_neighbors(values, adjacency, obstacles)

    queue: list[tuple[float, float, tuple[float, float], str]] = [(0.0, 0.0, start, "")]
    best: dict[tuple[tuple[float, float], str], float] = {(start, ""): 0.0}
    previous: dict[tuple[tuple[float, float], str], tuple[tuple[float, float], str] | None] = {(start, ""): None}
    end_state: tuple[tuple[float, float], str] | None = None
    expansions = 0
    while queue and expansions < 25000:
        _estimated, cost, point, direction = heapq.heappop(queue)
        state = (point, direction)
        if cost > best.get(state, math.inf) + 1e-9:
            continue
        expansions += 1
        if point == end:
            end_state = state
            break
        for neighbor in adjacency.get(point, []):
            next_direction = "h" if abs(neighbor[0] - point[0]) > 1e-9 else "v"
            distance = abs(neighbor[0] - point[0]) + abs(neighbor[1] - point[1])
            next_cost = cost + distance
            if direction and direction != next_direction:
                next_cost += metrics.route_turn_penalty
            next_cost += _occupied_segment_cost(point, neighbor, occupied, metrics)
            next_state = (neighbor, next_direction)
            if next_cost + 1e-9 >= best.get(next_state, math.inf):
                continue
            best[next_state] = next_cost
            previous[next_state] = state
            heuristic = abs(end[0] - neighbor[0]) + abs(end[1] - neighbor[1])
            heapq.heappush(queue, (next_cost + heuristic, next_cost, neighbor, next_direction))
    if end_state is None:
        return ()
    reversed_points: list[tuple[float, float]] = []
    current: tuple[tuple[float, float], str] | None = end_state
    while current is not None:
        reversed_points.append(current[0])
        current = previous[current]
    return tuple(_simplify_orthogonal(list(reversed(reversed_points))))


def _fast_orthogonal_route(
    start: tuple[float, float],
    end: tuple[float, float],
    obstacles: list[Rect],
    occupied: list[tuple[tuple[float, float], tuple[float, float], str]],
    metrics: RenderMetricsProfile,
) -> tuple[tuple[float, float], ...]:
    """Try bounded one/two-bend channels before constructing the full grid."""

    lane = metrics.route_lane_gap
    x_channels = {(start[0] + end[0]) / 2.0}
    y_channels = {(start[1] + end[1]) / 2.0}
    for rect in obstacles:
        x_channels.update((rect.x - lane, rect.right + lane))
        y_channels.update((rect.y - lane, rect.bottom + lane))
    for first, second, _edge_id in occupied:
        if abs(first[0] - second[0]) < 1e-9:
            x_channels.update((first[0] - lane, first[0] + lane))
        if abs(first[1] - second[1]) < 1e-9:
            y_channels.update((first[1] - lane, first[1] + lane))
    candidates: list[tuple[tuple[float, float], ...]] = [
        (start, (end[0], start[1]), end),
        (start, (start[0], end[1]), end),
    ]
    candidates.extend(
        (start, (x, start[1]), (x, end[1]), end)
        for x in sorted(x_channels, key=lambda value: (abs(value - (start[0] + end[0]) / 2.0), value))
    )
    candidates.extend(
        (start, (start[0], y), (end[0], y), end)
        for y in sorted(y_channels, key=lambda value: (abs(value - (start[1] + end[1]) / 2.0), value))
    )
    quality_mode = len(obstacles) <= 50
    if not quality_mode:
        nearby_x = sorted(
            x_channels,
            key=lambda value: (min(abs(value - start[0]), abs(value - end[0])), value),
        )[:24]
        nearby_y = sorted(
            y_channels,
            key=lambda value: (min(abs(value - start[1]), abs(value - end[1])), value),
        )[:24]
        candidates.extend(
            (start, (start[0], y), (x, y), (x, end[1]), end)
            for y in nearby_y
            for x in nearby_x
        )
    best: tuple[tuple[float, float], ...] = ()
    best_key = (math.inf, math.inf, math.inf)
    best_overlap = math.inf
    best_crossings = math.inf
    best_cost = math.inf
    for raw in candidates:
        points = tuple(_simplify_orthogonal(list(raw)))
        if len(points) < 2:
            continue
        segments = list(zip(points, points[1:]))
        if any(
            _segment_hits_rect(first, second, rect)
            for first, second in segments
            for rect in obstacles
        ):
            continue
        distance = sum(
            abs(second[0] - first[0]) + abs(second[1] - first[1])
            for first, second in segments
        )
        overlap_length = 0.0
        crossing_count = 0
        for first, second in segments:
            for used_first, used_second, _edge_id in occupied:
                overlap = _collinear_overlap_length(first, second, used_first, used_second)
                if overlap > 1e-6:
                    overlap_length += overlap
                elif _segments_cross(first, second, used_first, used_second):
                    crossing_count += 1
        cost = distance + max(0, len(points) - 2) * metrics.route_turn_penalty
        # Long collinear ambiguity is a hard failure; an ordinary crossing is a
        # soft readability cost. Prefer the first nearby zero-overlap channel
        # instead of invoking an expensive full grid merely to avoid crossings.
        candidate_key = (
            overlap_length * 2.0
            + (metrics.route_congestion_penalty if overlap_length > 1e-6 else 0.0)
            + crossing_count * metrics.route_congestion_penalty,
            cost,
            overlap_length,
        ) if quality_mode else (overlap_length, crossing_count, cost)
        if candidate_key < best_key:
            best = points
            best_key = candidate_key
            best_overlap = overlap_length
            best_crossings = crossing_count
            best_cost = cost
            if (
                quality_mode
                and overlap_length <= 1e-9
                and crossing_count == 0
                and cost < metrics.route_congestion_penalty
            ):
                return best
            if not quality_mode and overlap_length <= 1e-9:
                return best
    # Exact long overlaps are deliberately left to the full grid search, which
    # has more alternative lanes. Crossing penalties alone are a soft target.
    if quality_mode:
        if (
            best
            and best_overlap <= 1e-9
            and best_crossings == 0
            and best_cost < metrics.route_congestion_penalty
        ):
            return best
        return ()
    if best:
        return best
    return ()


def _connect_visible_neighbors(
    values: list[tuple[float, float]],
    adjacency: dict[tuple[float, float], list[tuple[float, float]]],
    obstacles: list[Rect],
) -> None:
    for first, second in zip(values, values[1:]):
        if any(_segment_hits_rect(first, second, rect) for rect in obstacles):
            continue
        adjacency[first].append(second)
        adjacency[second].append(first)


def _point_inside(point: tuple[float, float], rect: Rect, tolerance: float = 1e-6) -> bool:
    return rect.x + tolerance < point[0] < rect.right - tolerance and rect.y + tolerance < point[1] < rect.bottom - tolerance


def _segment_hits_rect(
    first: tuple[float, float], second: tuple[float, float], rect: Rect
) -> bool:
    if abs(first[0] - second[0]) < 1e-9:
        x = first[0]
        low, high = sorted((first[1], second[1]))
        return rect.x < x < rect.right and max(low, rect.y) < min(high, rect.bottom)
    if abs(first[1] - second[1]) < 1e-9:
        y = first[1]
        low, high = sorted((first[0], second[0]))
        return rect.y < y < rect.bottom and max(low, rect.x) < min(high, rect.right)
    return _line_intersects_rect(first, second, rect)


def _occupied_segment_cost(
    first: tuple[float, float],
    second: tuple[float, float],
    occupied: list[tuple[tuple[float, float], tuple[float, float], str]],
    metrics: RenderMetricsProfile,
) -> float:
    cost = 0.0
    for used_first, used_second, _edge_id in occupied:
        overlap = _collinear_overlap_length(first, second, used_first, used_second)
        if overlap > 1e-6:
            # A long shared lane makes unrelated relations visually
            # indistinguishable and is therefore a hard readability failure.
            # Keep ordinary crossings as a soft cost, but make any available
            # non-overlapping detour preferable even on a wide textbook scene.
            cost += metrics.route_congestion_penalty * 25.0 + overlap * 10.0
        elif _segments_cross(first, second, used_first, used_second):
            cost += metrics.route_congestion_penalty
    return cost


def _simplify_orthogonal(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    result = [points[0]]
    for point in points[1:]:
        if len(result) >= 2:
            first, second = result[-2], result[-1]
            if (abs(first[0] - second[0]) < 1e-9 and abs(second[0] - point[0]) < 1e-9) or (
                abs(first[1] - second[1]) < 1e-9 and abs(second[1] - point[1]) < 1e-9
            ):
                result[-1] = point
                continue
        result.append(point)
    return result


def _place_edge_labels(scene: SceneGeometry, metrics: RenderMetricsProfile) -> None:
    placed: list[Rect] = []
    dense = len(scene.edge_instances) > 12
    for edge in sorted(scene.edge_instances, key=_edge_route_priority):
        route = scene.edge_routes.get(edge.view_id)
        spec = relation_display_spec(edge.relation_family, edge.relation_type)
        text = f"{spec.label} · {_status_label(edge.review_status)}"
        important = edge.review_status in {"needs_revision", "needs_expert_review"} or spec.semantics_pending
        if route is None or route.status != "routed" or len(route.points) < 2:
            scene.edge_labels[edge.view_id] = EdgeLabel(edge.relation_id, text, None, False, "route_unavailable")
            continue
        segments = sorted(
            zip(route.points, route.points[1:]),
            key=lambda pair: _segment_length(pair[0], pair[1]),
            reverse=True,
        )
        label_rect: Rect | None = None
        for first, second in segments:
            if _segment_length(first, second) < min(metrics.label_width, 90.0):
                continue
            center_x = (first[0] + second[0]) / 2.0
            center_y = (first[1] + second[1]) / 2.0
            candidate = Rect(
                center_x - metrics.label_width / 2.0,
                center_y - metrics.label_height / 2.0 - 6.0,
                metrics.label_width,
                metrics.label_height,
            )
            if any(candidate.intersects(rect, tolerance=-2.0) for rect in scene.all_rects.values()):
                continue
            if any(candidate.intersects(rect, tolerance=-2.0) for rect in placed):
                continue
            label_rect = candidate
            break
        if label_rect is None:
            scene.edge_labels[edge.view_id] = EdgeLabel(edge.relation_id, text, None, False, "no_safe_slot")
        else:
            placed.append(label_rect)
            visible = not (dense and not important)
            scene.edge_labels[edge.view_id] = EdgeLabel(
                edge.relation_id,
                text,
                label_rect,
                visible,
                "" if visible else "overview_density",
            )


def _pack_relation_visual_groups(
    scene: SceneGeometry,
    projection: ReviewViewProjection,
    metrics: RenderMetricsProfile,
) -> None:
    """Pack complete component paint bounds and translate every dependent geometry."""

    units: list[tuple[object, tuple[str, ...], Rect]] = []
    for group in projection.visual_groups:
        members = tuple(
            view_id for view_id in group.member_view_ids if view_id in scene.node_rects
        )
        if not members:
            continue
        member_set = set(members)
        bounds: list[Rect] = [scene.node_rects[view_id] for view_id in members]
        for route in scene.edge_routes.values():
            if route.source_view_id not in member_set or route.target_view_id not in member_set:
                continue
            bounds.extend(_route_paint_rects(route))
            label = scene.edge_labels.get(route.edge_view_id)
            if label is not None and label.visible and label.rect is not None:
                bounds.append(label.rect)
        paint = _union_rects(bounds)
        frame = Rect(
            paint.x - metrics.group_padding,
            paint.y - metrics.group_padding - metrics.group_header_height,
            paint.width + 2 * metrics.group_padding,
            paint.height + 2 * metrics.group_padding + metrics.group_header_height,
        )
        units.append((group, members, frame))
    if not units:
        return
    total_area = sum(
        (frame.width + metrics.component_gap) * (frame.height + metrics.component_gap)
        for _group, _members, frame in units
    )
    # A square-root estimate can still degenerate into a portrait stack when
    # two wide groups narrowly miss the same shelf.  Evaluate a small,
    # deterministic family of widths and keep the packing closest to the
    # configured landscape ratio.  This is presentation-only: identities and
    # business semantics remain unchanged.
    target_width = _choose_group_shelf_width(units, total_area, metrics)
    x_cursor = 0.0
    y_cursor = 0.0
    row_height = 0.0
    for group, members, frame in units:
        if x_cursor and x_cursor + frame.width > target_width:
            x_cursor = 0.0
            y_cursor += row_height + metrics.component_gap
            row_height = 0.0
        dx = x_cursor - frame.x
        dy = y_cursor - frame.y
        member_set = set(members)
        for view_id in members:
            scene.node_rects[view_id] = _shift_rect(scene.node_rects[view_id], dx, dy)
        for edge_view_id, route in list(scene.edge_routes.items()):
            if route.source_view_id not in member_set or route.target_view_id not in member_set:
                continue
            scene.edge_routes[edge_view_id] = replace(
                route,
                points=tuple(_shift_point(point, dx, dy) for point in route.points),
                source_escape=(
                    _shift_point(route.source_escape, dx, dy)
                    if route.source_escape is not None
                    else None
                ),
                target_escape=(
                    _shift_point(route.target_escape, dx, dy)
                    if route.target_escape is not None
                    else None
                ),
                source_arrow=tuple(_shift_point(point, dx, dy) for point in route.source_arrow),
                target_arrow=tuple(_shift_point(point, dx, dy) for point in route.target_arrow),
            )
            label = scene.edge_labels.get(edge_view_id)
            if label is not None and label.rect is not None:
                scene.edge_labels[edge_view_id] = replace(
                    label,
                    rect=_shift_rect(label.rect, dx, dy),
                )
        packed_frame = Rect(x_cursor, y_cursor, frame.width, frame.height)
        content = Rect(
            packed_frame.x + metrics.group_padding,
            packed_frame.y + metrics.group_header_height + metrics.group_padding,
            packed_frame.width - 2 * metrics.group_padding,
            packed_frame.height - metrics.group_header_height - 2 * metrics.group_padding,
        )
        paint_bounds = Rect(
            content.x,
            content.y,
            content.width,
            content.height,
        )
        scene.visual_groups[group.group_id] = VisualGroupGeometry(
            group_id=group.group_id,
            kind=group.kind,
            title=group.title,
            member_view_ids=members,
            frame_rect=packed_frame,
            content_rect=content,
            paint_bounds=paint_bounds,
            grouping_basis=group.grouping_basis,
        )
        x_cursor += frame.width + metrics.component_gap
        row_height = max(row_height, frame.height)
    timings = dict(scene.readability.timings_ms)
    scene.readability = _readability_report(scene, metrics)
    scene.readability.timings_ms = timings


def _choose_group_shelf_width(
    units: list[tuple[object, tuple[str, ...], Rect]],
    total_area: float,
    metrics: RenderMetricsProfile,
) -> float:
    widths = sorted((frame.width for _group, _members, frame in units), reverse=True)
    widest = widths[0]
    ideal = max(widest, math.sqrt(max(total_area, 1.0) * metrics.target_aspect_ratio))
    candidates = {
        widest,
        ideal,
        ideal * 1.25,
        ideal * 1.5,
        ideal * 1.75,
        ideal * 2.0,
        sum(widths[:2]) + metrics.component_gap if len(widths) >= 2 else widest,
        sum(widths[:3]) + metrics.component_gap * 2 if len(widths) >= 3 else widest,
    }
    best_width = ideal
    best_score = math.inf
    frame_area = sum(frame.width * frame.height for _group, _members, frame in units)
    for candidate in sorted(max(widest, value) for value in candidates):
        packed_width, packed_height = _simulate_group_shelves(
            units,
            candidate,
            metrics.component_gap,
        )
        if packed_width <= 0 or packed_height <= 0:
            continue
        ratio = packed_width / packed_height
        ratio_error = abs(math.log(max(ratio, 1e-9) / metrics.target_aspect_ratio))
        empty_ratio = max(0.0, packed_width * packed_height - frame_area) / max(frame_area, 1.0)
        score = ratio_error + 0.035 * empty_ratio
        if score < best_score - 1e-9 or (
            abs(score - best_score) <= 1e-9 and candidate < best_width
        ):
            best_width = candidate
            best_score = score
    return best_width


def _simulate_group_shelves(
    units: list[tuple[object, tuple[str, ...], Rect]],
    target_width: float,
    gap: float,
) -> tuple[float, float]:
    x_cursor = 0.0
    y_cursor = 0.0
    row_height = 0.0
    packed_width = 0.0
    for _group, _members, frame in units:
        if x_cursor and x_cursor + frame.width > target_width:
            packed_width = max(packed_width, x_cursor - gap)
            x_cursor = 0.0
            y_cursor += row_height + gap
            row_height = 0.0
        x_cursor += frame.width + gap
        row_height = max(row_height, frame.height)
    packed_width = max(packed_width, max(0.0, x_cursor - gap))
    return packed_width, y_cursor + row_height


def _route_paint_rects(route: EdgeRoute) -> list[Rect]:
    rects: list[Rect] = []
    if route.points:
        xs = [point[0] for point in route.points]
        ys = [point[1] for point in route.points]
        rects.append(Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    for polygon in (route.source_arrow, route.target_arrow):
        if polygon:
            xs = [point[0] for point in polygon]
            ys = [point[1] for point in polygon]
            rects.append(Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    return rects


def _union_rects(rects: list[Rect]) -> Rect:
    left = min(rect.x for rect in rects)
    top = min(rect.y for rect in rects)
    right = max(rect.right for rect in rects)
    bottom = max(rect.bottom for rect in rects)
    return Rect(left, top, right - left, bottom - top)


def _shift_point(
    point: tuple[float, float], dx: float, dy: float
) -> tuple[float, float]:
    return point[0] + dx, point[1] + dy


def _shift_rect(rect: Rect, dx: float, dy: float) -> Rect:
    return Rect(rect.x + dx, rect.y + dy, rect.width, rect.height)


def _readability_report(scene: SceneGeometry, metrics: RenderMetricsProfile) -> ReadabilityReport:
    report = ReadabilityReport()
    view_ids = sorted(scene.node_rects)
    for index, left_id in enumerate(view_ids):
        for right_id in view_ids[index + 1 :]:
            if scene.node_rects[left_id].intersects(scene.node_rects[right_id], tolerance=metrics.border_tolerance):
                report.node_overlap += 1
                report.issue_ids.setdefault("node_overlap", []).append((left_id, right_id))
    routed = [route for route in scene.edge_routes.values() if route.status == "routed"]
    report.routed_edges = len(routed)
    report.failed_edges = len(scene.edge_routes) - len(routed)
    for route in routed:
        for node_id, rect in scene.node_rects.items():
            segments = list(zip(route.points, route.points[1:]))
            if node_id == route.source_view_id:
                segments = segments[1:]
            if node_id == route.target_view_id:
                segments = segments[:-1]
            if any(_segment_hits_rect(first, second, rect) for first, second in segments):
                report.edge_node_intrusion += 1
                report.issue_ids.setdefault("edge_node_intrusion", []).append((route.edge_view_id, node_id))
        if len(route.points) >= 2:
            source_vector = (
                route.points[1][0] - route.points[0][0],
                route.points[1][1] - route.points[0][1],
            )
            target_vector = (
                route.points[-1][0] - route.points[-2][0],
                route.points[-1][1] - route.points[-2][1],
            )
            if route.source_normal is not None and not _vector_follows(
                source_vector, route.source_normal
            ):
                report.terminal_direction_violation += 1
                report.issue_ids.setdefault("terminal_direction_violation", []).append(
                    (route.edge_view_id, "source")
                )
            if route.target_normal is not None and not _vector_follows(
                target_vector, (-route.target_normal[0], -route.target_normal[1])
            ):
                report.terminal_direction_violation += 1
                report.issue_ids.setdefault("terminal_direction_violation", []).append(
                    (route.edge_view_id, "target")
                )
            if _segment_length(route.points[0], route.points[1]) + 1e-6 < metrics.terminal_stub_length:
                report.terminal_stub_short += 1
                report.issue_ids.setdefault("terminal_stub_short", []).append((route.edge_view_id, "source"))
            if _segment_length(route.points[-2], route.points[-1]) + 1e-6 < metrics.terminal_stub_length:
                report.terminal_stub_short += 1
                report.issue_ids.setdefault("terminal_stub_short", []).append((route.edge_view_id, "target"))
        for endpoint, polygon in (
            (route.source_view_id, route.source_arrow),
            (route.target_view_id, route.target_arrow),
        ):
            if not polygon:
                continue
            polygon_rect = _polygon_rect(polygon)
            for node_id, rect in scene.node_rects.items():
                if polygon_rect.intersects(rect, tolerance=0.0):
                    report.arrow_occlusion += 1
                    report.issue_ids.setdefault("arrow_occlusion", []).append(
                        (route.edge_view_id, endpoint, node_id)
                    )
    route_bounds = {
        route.edge_view_id: Rect(
            min(point[0] for point in route.points),
            min(point[1] for point in route.points),
            max(point[0] for point in route.points) - min(point[0] for point in route.points),
            max(point[1] for point in route.points) - min(point[1] for point in route.points),
        )
        for route in routed
    }
    for index, first_route in enumerate(routed):
        for second_route in routed[index + 1 :]:
            if not route_bounds[first_route.edge_view_id].intersects(
                route_bounds[second_route.edge_view_id], tolerance=-1e-6
            ):
                continue
            common_endpoint = bool(
                {first_route.source_view_id, first_route.target_view_id}
                & {second_route.source_view_id, second_route.target_view_id}
            )
            overlaps = sum(
                _collinear_overlap_length(a, b, c, d)
                for a, b in zip(first_route.points, first_route.points[1:])
                for c, d in zip(second_route.points, second_route.points[1:])
            )
            endpoint_stub_allowance = metrics.route_clearance + metrics.route_lane_gap
            non_endpoint_tolerance = max(2.0, metrics.arrow_tip_gap)
            if overlaps > (
                endpoint_stub_allowance if common_endpoint else non_endpoint_tolerance
            ):
                report.ambiguous_overlap += 1
                report.issue_ids.setdefault("ambiguous_overlap", []).append((first_route.edge_view_id, second_route.edge_view_id))
            crossings = sum(
                _segments_cross(a, b, c, d)
                for a, b in zip(first_route.points, first_route.points[1:])
                for c, d in zip(second_route.points, second_route.points[1:])
            )
            if crossings:
                report.edge_crossing += crossings
                report.issue_ids.setdefault("edge_crossing", []).append((first_route.edge_view_id, second_route.edge_view_id))
    visible_labels = [label for label in scene.edge_labels.values() if label.visible and label.rect is not None]
    for index, label in enumerate(visible_labels):
        if any(label.rect.intersects(rect, tolerance=-2.0) for rect in scene.node_rects.values()):
            report.label_overlap += 1
            report.issue_ids.setdefault("label_overlap", []).append((label.relation_id, "node"))
        for other in visible_labels[index + 1 :]:
            if label.rect.intersects(other.rect, tolerance=-2.0):
                report.label_overlap += 1
                report.issue_ids.setdefault("label_overlap", []).append((label.relation_id, other.relation_id))
    endpoint_ports: dict[tuple[str, str], list[tuple[str, tuple[float, float]]]] = {}
    for route in routed:
        if not route.points:
            continue
        endpoint_ports.setdefault((route.source_view_id, route.source_port), []).append(
            (route.edge_view_id, route.points[0])
        )
        endpoint_ports.setdefault((route.target_view_id, route.target_port), []).append(
            (route.edge_view_id, route.points[-1])
        )
    for (_view_id, side), entries in endpoint_ports.items():
        coordinate = 1 if side in {"left", "right"} else 0
        ordered = sorted(entries, key=lambda item: (item[1][coordinate], item[0]))
        for first, second in zip(ordered, ordered[1:]):
            if abs(second[1][coordinate] - first[1][coordinate]) + 1e-6 < metrics.port_min_spacing:
                report.port_spacing_violation += 1
                report.issue_ids.setdefault("port_spacing_violation", []).append(
                    (first[0], second[0])
                )
    group_values = sorted(scene.visual_groups.values(), key=lambda item: item.group_id)
    for index, group in enumerate(group_values):
        for other in group_values[index + 1 :]:
            if group.frame_rect.intersects(other.frame_rect):
                report.group_overlap += 1
                report.issue_ids.setdefault("group_overlap", []).append((group.group_id, other.group_id))
        member_set = set(group.member_view_ids)
        member_bounds = [scene.node_rects[view_id] for view_id in member_set if view_id in scene.node_rects]
        for route in routed:
            if route.source_view_id in member_set and route.target_view_id in member_set:
                member_bounds.extend(_route_paint_rects(route))
                label = scene.edge_labels.get(route.edge_view_id)
                if label is not None and label.visible and label.rect is not None:
                    member_bounds.append(label.rect)
        if member_bounds and not group.content_rect.contains(_union_rects(member_bounds), tolerance=metrics.border_tolerance):
            report.group_overflow += 1
            report.issue_ids.setdefault("group_overflow", []).append((group.group_id,))
    return report


def _vector_follows(
    vector: tuple[float, float], expected: tuple[float, float]
) -> bool:
    length = math.hypot(vector[0], vector[1])
    if length <= 1e-9:
        return False
    unit = (vector[0] / length, vector[1] / length)
    return unit[0] * expected[0] + unit[1] * expected[1] >= 1.0 - 1e-6


def _polygon_rect(points: tuple[tuple[float, float], ...]) -> Rect:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _status_label(status: str) -> str:
    return {
        "accepted": "已通过",
        "rejected": "已拒绝",
        "needs_revision": "待修",
        "needs_expert_review": "需专家复核",
        "pending": "待审",
    }.get(str(status or "pending"), str(status or "待审"))


def _segment_length(first: tuple[float, float], second: tuple[float, float]) -> float:
    return abs(second[0] - first[0]) + abs(second[1] - first[1])


def _collinear_overlap_length(
    first: tuple[float, float],
    second: tuple[float, float],
    other_first: tuple[float, float],
    other_second: tuple[float, float],
) -> float:
    if abs(first[1] - second[1]) < 1e-9 and abs(other_first[1] - other_second[1]) < 1e-9 and abs(first[1] - other_first[1]) < 1e-9:
        a1, a2 = sorted((first[0], second[0]))
        b1, b2 = sorted((other_first[0], other_second[0]))
        return max(0.0, min(a2, b2) - max(a1, b1))
    if abs(first[0] - second[0]) < 1e-9 and abs(other_first[0] - other_second[0]) < 1e-9 and abs(first[0] - other_first[0]) < 1e-9:
        a1, a2 = sorted((first[1], second[1]))
        b1, b2 = sorted((other_first[1], other_second[1]))
        return max(0.0, min(a2, b2) - max(a1, b1))
    return 0.0


def _segments_cross(
    first: tuple[float, float],
    second: tuple[float, float],
    other_first: tuple[float, float],
    other_second: tuple[float, float],
) -> bool:
    if {first, second} & {other_first, other_second}:
        return False
    first_horizontal = abs(first[1] - second[1]) < 1e-9
    second_horizontal = abs(other_first[1] - other_second[1]) < 1e-9
    if first_horizontal == second_horizontal:
        return False
    horizontal = (first, second) if first_horizontal else (other_first, other_second)
    vertical = (other_first, other_second) if first_horizontal else (first, second)
    x1, x2 = sorted((horizontal[0][0], horizontal[1][0]))
    y1, y2 = sorted((vertical[0][1], vertical[1][1]))
    return x1 < vertical[0][0] < x2 and y1 < horizontal[0][1] < y2


def _line_intersects_rect(
    first: tuple[float, float], second: tuple[float, float], rect: Rect
) -> bool:
    # Diagnostic fallback for a failed non-orthogonal route.
    steps = max(2, int(math.dist(first, second) / 4.0))
    for index in range(1, steps):
        ratio = index / steps
        point = (
            first[0] + (second[0] - first[0]) * ratio,
            first[1] + (second[1] - first[1]) * ratio,
        )
        if _point_inside(point, rect):
            return True
    return False


__all__ = [
    "EdgeLabel",
    "EdgeRoute",
    "GeometryIssue",
    "ReadabilityReport",
    "Rect",
    "RenderMetricsProfile",
    "SceneGeometry",
    "VisualGroupGeometry",
    "layout_scene",
]
