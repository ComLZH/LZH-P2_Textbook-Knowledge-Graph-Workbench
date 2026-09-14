from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from ..review_views.projection import ProjectedEdgeInstance
from .recursive_layout import EdgeRoute, Rect, RenderMetricsProfile


ELK_BACKEND_VERSION = "elkjs_0_12_0"


class ElkBackendError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ElkLayoutResult:
    node_rects: dict[str, Rect]
    edge_routes: dict[str, EdgeRoute]
    elapsed_ms: float
    runtime_root: Path


@dataclass(frozen=True, slots=True)
class ElkLayoutJob:
    component_id: str
    node_rects: dict[str, Rect]
    edges: tuple[ProjectedEdgeInstance, ...]
    node_order: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class ElkBatchLayoutResult:
    component_results: dict[str, ElkLayoutResult]
    failures: dict[str, str]
    elapsed_ms: float
    runtime_root: Path
    process_count: int = 1
    batch_count: int = 1
    diagnostics: tuple[str, ...] = ()


def bundled_runtime_root() -> Path:
    override = os.environ.get("P2_LAYOUT_RUNTIME_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parents[3] / "vendor" / "layout_runtime"


def runtime_status(runtime_root: Path | None = None) -> dict[str, object]:
    root = (runtime_root or bundled_runtime_root()).resolve()
    node = root / "node" / "node.exe"
    if not node.is_file():
        system_node = shutil.which("node")
        if system_node:
            node = Path(system_node)
    elk = root / "elk" / "elk.bundled.js"
    runner = root / "elk_layout_runner.mjs"
    return {
        "root": str(root),
        "available": node.is_file() and elk.is_file() and runner.is_file(),
        "node": str(node),
        "elk": str(elk),
        "runner": str(runner),
    }


def layout_flat_graph(
    node_rects: dict[str, Rect],
    edges: Iterable[ProjectedEdgeInstance],
    metrics: RenderMetricsProfile,
    *,
    timeout_seconds: float = 5.0,
    runtime_root: Path | None = None,
    node_order: dict[str, int] | None = None,
) -> ElkLayoutResult:
    status = runtime_status(runtime_root)
    if not status["available"]:
        raise ElkBackendError("bundled_runtime_missing")
    edge_list = sorted(edges, key=lambda item: item.view_id)
    payload = {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.spacing.nodeNode": str(max(metrics.row_gap, 44.0)),
            "elk.layered.spacing.nodeNodeBetweenLayers": str(max(metrics.rank_gap, 96.0)),
            "elk.separateConnectedComponents": "true",
            "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
            "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
            "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
            "elk.layered.crossingMinimization.forceNodeModelOrder": "true",
        },
        "children": [
            {"id": node_id, "width": rect.width, "height": rect.height}
            for node_id, rect in sorted(
                node_rects.items(),
                key=lambda item: (
                    (node_order or {}).get(item[0], 10**9),
                    item[0],
                ),
            )
        ],
        "edges": [
            {
                "id": edge.view_id,
                "sources": [edge.source_view_id],
                "targets": [edge.target_view_id],
            }
            for edge in edge_list
        ],
    }
    root = Path(str(status["root"]))
    node_executable = Path(str(status["node"]))
    runner = Path(str(status["runner"]))
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        with tempfile.TemporaryDirectory(prefix="p2-elk-layout-") as temporary:
            input_path = Path(temporary) / "input.json"
            output_path = Path(temporary) / "output.json"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [str(node_executable), str(runner), str(input_path), str(output_path)],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                creationflags=creation_flags,
                check=False,
            )
            if completed.returncode != 0:
                raise ElkBackendError(f"runtime_exit_{completed.returncode}")
            if not output_path.is_file():
                raise ElkBackendError("runtime_output_missing")
            raw_result = json.loads(output_path.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired as exc:
        raise ElkBackendError("runtime_timeout") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ElkBackendError("runtime_io_or_json_invalid") from exc
    graph = raw_result.get("graph")
    if not isinstance(graph, dict):
        raise ElkBackendError("graph_output_invalid")
    result_nodes = _node_rects(graph)
    if set(result_nodes) != set(node_rects):
        raise ElkBackendError("node_set_mismatch")
    routes = _edge_routes(graph, edge_list)
    if set(routes) != {edge.view_id for edge in edge_list}:
        raise ElkBackendError("edge_set_mismatch")
    elapsed = raw_result.get("elapsed_ms", 0.0)
    if not isinstance(elapsed, (int, float)) or not math.isfinite(float(elapsed)):
        elapsed = 0.0
    return ElkLayoutResult(result_nodes, routes, float(elapsed), root)


def layout_graph_batch(
    jobs: Iterable[ElkLayoutJob],
    metrics: RenderMetricsProfile,
    *,
    timeout_seconds: float = 8.0,
    runtime_root: Path | None = None,
    request_id: str | None = None,
) -> ElkBatchLayoutResult:
    """Solve multiple components in one short-lived Node process with checkpoints."""

    status = runtime_status(runtime_root)
    if not status["available"]:
        raise ElkBackendError("bundled_runtime_missing")
    job_list = sorted(jobs, key=lambda item: item.component_id)
    if not job_list:
        return ElkBatchLayoutResult({}, {}, 0.0, Path(str(status["root"])), 0, 0)
    if len({item.component_id for item in job_list}) != len(job_list):
        raise ElkBackendError("duplicate_component_id")
    actual_request_id = request_id or f"layout-{uuid4().hex}"
    payload = {
        "protocol_version": "p2_elk_batch_v1",
        "request_id": actual_request_id,
        "component_jobs": [
            {
                "component_id": job.component_id,
                "graph": _graph_payload(job.node_rects, job.edges, metrics, job.node_order),
            }
            for job in job_list
        ],
    }
    root = Path(str(status["root"]))
    node_executable = Path(str(status["node"]))
    runner = Path(str(status["runner"]))
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    timed_out = False
    runtime_exit_code: int | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="p2-elk-layout-batch-") as temporary:
            input_path = Path(temporary) / "input.json"
            output_path = Path(temporary) / "output.json"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                completed = subprocess.run(
                    [str(node_executable), str(runner), str(input_path), str(output_path)],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    creationflags=creation_flags,
                    check=False,
                )
                if completed.returncode != 0:
                    runtime_exit_code = int(completed.returncode)
            except subprocess.TimeoutExpired:
                timed_out = True
            if not output_path.is_file():
                if timed_out:
                    raise ElkBackendError("runtime_timeout")
                if runtime_exit_code is not None:
                    raise ElkBackendError(f"runtime_exit_{runtime_exit_code}")
                raise ElkBackendError("runtime_output_missing")
            raw_result = json.loads(output_path.read_text(encoding="utf-8"))
    except ElkBackendError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ElkBackendError("runtime_io_or_json_invalid") from exc
    if raw_result.get("protocol_version") != "p2_elk_batch_v1":
        raise ElkBackendError("batch_protocol_mismatch")
    if raw_result.get("request_id") != actual_request_id:
        raise ElkBackendError("batch_request_id_mismatch")
    expected = {item.component_id: item for item in job_list}
    raw_records = raw_result.get("results")
    if not isinstance(raw_records, list):
        raise ElkBackendError("batch_records_invalid")
    results: dict[str, ElkLayoutResult] = {}
    failures: dict[str, str] = {}
    diagnostics: list[str] = []
    seen: set[str] = set()
    for record in raw_records:
        if not isinstance(record, dict):
            diagnostics.append("batch_record_invalid")
            continue
        component_id = str(record.get("component_id") or "")
        if component_id not in expected:
            diagnostics.append("batch_component_identity_invalid")
            continue
        if component_id in seen:
            results.pop(component_id, None)
            failures[component_id] = "duplicate_component_record"
            diagnostics.append("batch_component_identity_duplicate")
            continue
        seen.add(component_id)
        if record.get("status") != "completed":
            failures[component_id] = str(record.get("error") or "component_failed")[:200]
            continue
        graph = record.get("graph")
        if not isinstance(graph, dict):
            failures[component_id] = "component_graph_invalid"
            continue
        job = expected[component_id]
        try:
            result_nodes = _node_rects(graph)
            if set(result_nodes) != set(job.node_rects):
                raise ElkBackendError("node_set_mismatch")
            routes = _edge_routes(graph, list(job.edges))
            if set(routes) != {edge.view_id for edge in job.edges}:
                raise ElkBackendError("edge_set_mismatch")
        except ElkBackendError as exc:
            failures[component_id] = str(exc)
            continue
        elapsed = record.get("elapsed_ms", 0.0)
        elapsed_number = float(elapsed) if isinstance(elapsed, (int, float)) and math.isfinite(float(elapsed)) else 0.0
        results[component_id] = ElkLayoutResult(result_nodes, routes, elapsed_number, root)
    for component_id in expected.keys() - seen:
        if timed_out:
            failures[component_id] = "runtime_timeout_unfinished"
        elif runtime_exit_code is not None:
            failures[component_id] = f"runtime_exit_{runtime_exit_code}_unfinished"
        else:
            failures[component_id] = "component_result_missing"
    elapsed = raw_result.get("elapsed_ms", 0.0)
    elapsed_number = float(elapsed) if isinstance(elapsed, (int, float)) and math.isfinite(float(elapsed)) else 0.0
    if runtime_exit_code is not None:
        diagnostics.append(f"runtime_exit_{runtime_exit_code}")
    if timed_out:
        diagnostics.append("runtime_timeout_partial")
    return ElkBatchLayoutResult(
        results,
        failures,
        elapsed_number,
        root,
        diagnostics=tuple(diagnostics),
    )


def _graph_payload(
    node_rects: dict[str, Rect],
    edges: Iterable[ProjectedEdgeInstance],
    metrics: RenderMetricsProfile,
    node_order: dict[str, int] | None = None,
) -> dict[str, object]:
    edge_list = sorted(edges, key=lambda item: item.view_id)
    return {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.spacing.nodeNode": str(max(metrics.row_gap, 44.0)),
            "elk.layered.spacing.nodeNodeBetweenLayers": str(max(metrics.rank_gap, 96.0)),
            "elk.separateConnectedComponents": "true",
            "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
            "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
            "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
            "elk.layered.crossingMinimization.forceNodeModelOrder": "true",
        },
        "children": [
            {"id": node_id, "width": rect.width, "height": rect.height}
            for node_id, rect in sorted(
                node_rects.items(),
                key=lambda item: ((node_order or {}).get(item[0], 10**9), item[0]),
            )
        ],
        "edges": [
            {"id": edge.view_id, "sources": [edge.source_view_id], "targets": [edge.target_view_id]}
            for edge in edge_list
        ],
    }


def _node_rects(graph: dict[str, object]) -> dict[str, Rect]:
    results: dict[str, Rect] = {}
    children = graph.get("children", [])
    if not isinstance(children, list):
        raise ElkBackendError("node_output_invalid")
    for child in children:
        if not isinstance(child, dict):
            raise ElkBackendError("node_output_invalid")
        node_id = str(child.get("id") or "")
        values = [child.get(key) for key in ("x", "y", "width", "height")]
        if not node_id or not all(isinstance(value, (int, float)) for value in values):
            raise ElkBackendError("node_geometry_invalid")
        numbers = [float(value) for value in values]
        if not all(math.isfinite(value) for value in numbers) or min(numbers[2:]) <= 0:
            raise ElkBackendError("node_geometry_invalid")
        if node_id in results:
            raise ElkBackendError("duplicate_node_id")
        results[node_id] = Rect(*numbers)
    return results


def _edge_routes(
    graph: dict[str, object],
    edges: list[ProjectedEdgeInstance],
) -> dict[str, EdgeRoute]:
    edge_by_id = {edge.view_id: edge for edge in edges}
    results: dict[str, EdgeRoute] = {}
    raw_edges = graph.get("edges", [])
    if not isinstance(raw_edges, list):
        raise ElkBackendError("edge_output_invalid")
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, dict):
            raise ElkBackendError("edge_output_invalid")
        edge_id = str(raw_edge.get("id") or "")
        edge = edge_by_id.get(edge_id)
        if edge is None or edge_id in results:
            raise ElkBackendError("edge_identity_invalid")
        points: list[tuple[float, float]] = []
        sections = raw_edge.get("sections", [])
        if not isinstance(sections, list):
            raise ElkBackendError("edge_sections_invalid")
        for section in sections:
            if not isinstance(section, dict):
                continue
            candidates = [
                section.get("startPoint"),
                *(section.get("bendPoints", []) if isinstance(section.get("bendPoints", []), list) else []),
                section.get("endPoint"),
            ]
            for point in candidates:
                if not isinstance(point, dict):
                    continue
                x, y = point.get("x"), point.get("y")
                if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                    raise ElkBackendError("edge_point_invalid")
                value = (float(x), float(y))
                if not all(math.isfinite(number) for number in value):
                    raise ElkBackendError("edge_point_invalid")
                if not points or points[-1] != value:
                    points.append(value)
        if len(points) < 2:
            raise ElkBackendError("edge_route_missing")
        results[edge_id] = EdgeRoute(
            edge_id,
            edge.relation_id,
            edge.source_view_id,
            edge.target_view_id,
            tuple(points),
            "elk",
            "elk",
            "routed",
        )
    return results


__all__ = [
    "ELK_BACKEND_VERSION",
    "ElkBackendError",
    "ElkLayoutResult",
    "ElkLayoutJob",
    "ElkBatchLayoutResult",
    "bundled_runtime_root",
    "layout_flat_graph",
    "layout_graph_batch",
    "runtime_status",
]
