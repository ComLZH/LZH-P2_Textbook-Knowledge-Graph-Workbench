from __future__ import annotations

import argparse
import json
import math
import sys
import time
import tracemalloc
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from textbook_builder.review_document import (  # noqa: E402
    P2ReviewDocumentDTO,
    RELATION_FAMILY_SEMANTIC,
    ReviewDocumentMetadataDTO,
    ReviewNodeDTO,
    ReviewRelationDTO,
)
from textbook_builder.review_views import VIEW_MODE_RELATIONS  # noqa: E402
from textbook_builder.services import WorkbenchReviewService  # noqa: E402


FULL_LAYOUT_NODE_LIMIT = 299
FULL_LAYOUT_EDGE_LIMIT = 160
FULL_LAYOUT_WORK_LIMIT = 12_000


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "生成确定性关系图，验证 30/100/300/1000 节点布局的完整求解、"
            "有界终止与降级语义。"
        )
    )
    parser.add_argument("--output", required=True, help="新的 JSON 报告路径；拒绝覆盖")
    parser.add_argument("--sizes", nargs="+", type=int, default=[30, 100, 300, 1000])
    parser.add_argument("--cold-runs", type=int, default=5)
    parser.add_argument("--warm-runs", type=int, default=20)
    args = parser.parse_args()
    output_path = Path(args.output).resolve()
    if output_path.exists():
        parser.error(f"输出文件已存在，拒绝覆盖：{output_path}")
    if any(size <= 1 for size in args.sizes):
        parser.error("规模必须大于 1。")
    if args.cold_runs < 1 or args.warm_runs < 0:
        parser.error("冷启动次数至少为 1，热运行次数不能为负数。")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    service = WorkbenchReviewService()
    cases: list[dict[str, object]] = []
    all_passed = True
    case_specs: list[tuple[int, str, P2ReviewDocumentDTO, int, int | None]] = []
    for size in args.sizes:
        case_specs.append(
            (
                size,
                "connected_layered_sparse_mesh",
                _sparse_document(size),
                _layer_count(size),
                None,
            )
        )
        if size == 100:
            case_specs.append(
                (
                    size,
                    "disconnected_ordered_chains_of_ten",
                    _grouped_sparse_document(size),
                    10,
                    None,
                )
            )
        elif size >= 300:
            case_specs.append(
                (
                    size,
                    "disconnected_ordered_chains_of_ten",
                    _grouped_sparse_document(size),
                    10,
                    1,
                )
            )

    for size, topology, document, approximate_depth, repetition_override in case_specs:
        timings: list[float] = []
        bundle = None
        measured_runs = repetition_override or (args.cold_runs + args.warm_runs)
        for _index in range(measured_runs):
            started = time.perf_counter()
            bundle = service.build_review_render_bundle(
                document,
                renderer="benchmark",
                view_mode=VIEW_MODE_RELATIONS,
            )
            timings.append(round((time.perf_counter() - started) * 1000.0, 3))
        assert bundle is not None

        tracemalloc.start()
        memory_started = time.perf_counter()
        memory_bundle = service.build_review_render_bundle(
            document,
            renderer="benchmark-memory",
            view_mode=VIEW_MODE_RELATIONS,
        )
        memory_elapsed_ms = round((time.perf_counter() - memory_started) * 1000.0, 3)
        _current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report = bundle.geometry.readability
        degraded = bundle.geometry.engine == "bounded_diagnostic_grid_v6"
        edge_count = len(document.relations)
        partitioned_large = topology == "disconnected_ordered_chains_of_ten"
        expected_degraded = not partitioned_large and (
            size > FULL_LAYOUT_NODE_LIMIT
            or edge_count > FULL_LAYOUT_EDGE_LIMIT
            or size * edge_count > FULL_LAYOUT_WORK_LIMIT
        )
        p95_ms = _percentile(timings, 0.95)
        performance_gate = size <= 100
        if expected_degraded:
            passed = degraded and report.failed_edges == len(document.relations)
        else:
            passed = (
                not degraded
                and report.hard_violations == 0
                and (not performance_gate or p95_ms <= 5000.0)
            )
        all_passed = all_passed and passed
        cases.append(
            {
                "nodes": size,
                "edges": edge_count,
                "topology": topology,
                "approximate_depth": approximate_depth,
                "engine": bundle.geometry.engine,
                "elapsed_ms_wall": timings,
                "measured_runs": measured_runs,
                "elapsed_ms_p50": _percentile(timings, 0.50),
                "elapsed_ms_p95": p95_ms,
                "memory_probe_elapsed_ms_wall": memory_elapsed_ms,
                "peak_tracemalloc_bytes": peak_bytes,
                "canvas": asdict(bundle.geometry.canvas_rect),
                "readability": asdict(report),
                "degraded": degraded,
                "expected_degraded": expected_degraded,
                "performance_gate_applied": performance_gate,
                "passed": passed,
                "memory_probe_same_degradation": (
                    (memory_bundle.geometry.engine == "bounded_diagnostic_grid_v6") == degraded
                ),
            }
        )

    result = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "measurement": {
            "mode": VIEW_MODE_RELATIONS,
            "cold_runs_per_size": args.cold_runs,
            "warm_runs_per_size": args.warm_runs,
            "memory": "Python tracemalloc peak; does not include child Node process RSS",
            "search_budget": (
                "300 or more nodes, over 160 edges, or node-edge work above 12000 "
                "uses the bounded diagnostic scene unless the graph is split into "
                "independent components of at most 12 nodes and 24 edges"
            ),
        },
        "cases": cases,
        "all_expected_behaviors_passed": all_passed,
    }
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all_passed else 2


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 3)


def _sparse_document(node_count: int) -> P2ReviewDocumentDTO:
    nodes = [
        ReviewNodeDTO(
            node_id=f"n{index:04d}",
            display_name=f"规模验证知识点 {index:04d}",
            node_name=f"scale-node-{index:04d}",
        )
        for index in range(node_count)
    ]
    layer_count = _layer_count(node_count)
    rows = math.ceil(node_count / layer_count)
    pairs = {(0, row) for row in range(1, min(rows, node_count))}
    for layer in range(1, layer_count):
        for row in range(rows):
            target = layer * rows + row
            if target >= node_count:
                continue
            direct_source = (layer - 1) * rows + row
            if direct_source < node_count:
                pairs.add((direct_source, target))
            diagonal_source = (layer - 1) * rows + max(0, row - 1)
            if diagonal_source < node_count and diagonal_source != direct_source:
                pairs.add((diagonal_source, target))
    relations = [
        ReviewRelationDTO(
            relation_id=f"r{index:05d}",
            relation_family=RELATION_FAMILY_SEMANTIC,
            source_node_id=f"n{source:04d}",
            target_node_id=f"n{target:04d}",
            relation_type="progressive",
        )
        for index, (source, target) in enumerate(sorted(pairs))
    ]
    return P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO(f"scale-{node_count}", "generated-fixture"),
        nodes=nodes,
        relations=relations,
    )


def _grouped_sparse_document(node_count: int) -> P2ReviewDocumentDTO:
    """Create independent ten-node chains to exercise complete 100-node group layout."""
    nodes = [
        ReviewNodeDTO(
            node_id=f"g{index:04d}",
            display_name=f"分组验证知识点 {index:04d}",
            node_name=f"group-node-{index:04d}",
        )
        for index in range(node_count)
    ]
    pairs = [
        (base + offset, base + offset + 1)
        for base in range(0, node_count, 10)
        for offset in range(min(9, node_count - base - 1))
    ]
    relations = [
        ReviewRelationDTO(
            relation_id=f"gr{index:05d}",
            relation_family=RELATION_FAMILY_SEMANTIC,
            source_node_id=f"g{source:04d}",
            target_node_id=f"g{target:04d}",
            relation_type="progressive",
        )
        for index, (source, target) in enumerate(pairs)
    ]
    return P2ReviewDocumentDTO(
        ReviewDocumentMetadataDTO(f"scale-grouped-{node_count}", "generated-fixture"),
        nodes=nodes,
        relations=relations,
    )


def _layer_count(node_count: int) -> int:
    return max(2, math.ceil(math.sqrt(node_count * 1.6)))


if __name__ == "__main__":
    raise SystemExit(main())
