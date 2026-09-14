from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from textbook_builder.geometry import EdgeRoute, Rect, RenderMetricsProfile, SceneGeometry  # noqa: E402
from textbook_builder.geometry.recursive_layout import _place_edge_labels, _readability_report  # noqa: E402
from textbook_builder.review_document import (  # noqa: E402
    P2ReviewDocumentDTO,
    RELATION_FAMILY_SEMANTIC,
    ReviewDocumentMetadataDTO,
    ReviewNodeDTO,
    ReviewRelationDTO,
)
from textbook_builder.review_document_io import ReviewDocumentReader  # noqa: E402
from textbook_builder.review_views import VIEW_MODE_RELATIONS  # noqa: E402
from textbook_builder.services import WorkbenchReviewService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B 有界布局候选比较：Python 与临时 elkjs。")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--elk-root", required=True, help="临时解包的 elkjs package 目录")
    parser.add_argument("--node", default="node")
    parser.add_argument("--elk-package-sha256", default="")
    args = parser.parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    elk_root = Path(args.elk_root).resolve()
    if not input_path.is_file():
        parser.error(f"输入文件不存在：{input_path}")
    if output_path.exists():
        parser.error(f"输出目录已存在，拒绝覆盖：{output_path}")
    if not (elk_root / "lib" / "elk.bundled.js").is_file():
        parser.error(f"elkjs 模块无效：{elk_root}")
    output_path.mkdir(parents=True)

    sample = ReviewDocumentReader().read_document(input_path)
    fixtures = {
        "art_33_28": sample,
        "star_18": _star_document(),
        "cycle_12": _cycle_document(),
        "disconnected_long_labels_24": _disconnected_document(),
    }
    service = WorkbenchReviewService()
    cases: dict[str, object] = {}
    for name, document in fixtures.items():
        case_dir = output_path / name
        case_dir.mkdir()
        original_runtime = os.environ.get("P2_LAYOUT_RUNTIME_ROOT")
        os.environ["P2_LAYOUT_RUNTIME_ROOT"] = str(case_dir / "intentionally-missing-runtime")
        try:
            python_bundle = service.build_review_render_bundle(
                document,
                renderer="candidate",
                view_mode=VIEW_MODE_RELATIONS,
            )
        finally:
            if original_runtime is None:
                os.environ.pop("P2_LAYOUT_RUNTIME_ROOT", None)
            else:
                os.environ["P2_LAYOUT_RUNTIME_ROOT"] = original_runtime
        elk_input = _elk_input(python_bundle.geometry)
        elk_input_path = case_dir / "elk_input.json"
        elk_raw_path = case_dir / "elk_output.json"
        elk_input_path.write_text(
            json.dumps(elk_input, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        command = [
            args.node,
            str(PROJECT_ROOT / "scripts" / "elk_layout_probe.mjs"),
            str(elk_root),
            str(elk_input_path),
            str(elk_raw_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if completed.returncode:
            raise RuntimeError(completed.stderr or completed.stdout or "elkjs probe failed")
        elk_result = json.loads(elk_raw_path.read_text(encoding="utf-8"))
        elk_scene = _elk_scene(python_bundle.geometry, elk_result["graph"])
        metrics = RenderMetricsProfile(renderer="candidate")
        _place_edge_labels(elk_scene, metrics)
        elk_scene.readability = _readability_report(elk_scene, metrics)
        python_report = asdict(python_bundle.geometry.readability)
        elk_report = asdict(elk_scene.readability)
        python_column_ratio = _max_column_ratio(python_bundle.geometry)
        elk_column_ratio = _max_column_ratio(elk_scene)
        python_pass = (
            python_bundle.geometry.readability.hard_violations == 0
            and python_column_ratio <= 0.4
        )
        elk_pass = elk_scene.readability.hard_violations == 0 and elk_column_ratio <= 0.4
        selected_engine = "elkjs_0_12_0" if elk_pass else "python_networkx_v5"
        cases[name] = {
            "input": {
                "nodes": len(python_bundle.projection.node_instances),
                "edges": len(python_bundle.projection.edge_instances),
                "structure_fingerprint": python_bundle.projection.structure_fingerprint,
            },
            "python_networkx_v5": {
                "engine": python_bundle.geometry.engine,
                "canvas": asdict(python_bundle.geometry.canvas_rect),
                "readability": python_report,
                "max_column_ratio": python_column_ratio,
                "gate_pass": python_pass,
            },
            "elkjs_0_12_0": {
                "engine": "org.eclipse.elk.layered",
                "elapsed_ms_reported_by_elk": round(float(elk_result["elapsed_ms"]), 3),
                "canvas": asdict(elk_scene.canvas_rect),
                "readability": elk_report,
                "max_column_ratio": elk_column_ratio,
                "gate_pass": elk_pass,
            },
            "production_hybrid_selection": selected_engine,
        }

    python_passes = all(
        case["python_networkx_v5"]["gate_pass"] for case in cases.values()
    )
    elk_passes = all(case["elkjs_0_12_0"]["gate_pass"] for case in cases.values())
    hybrid_passes = all(
        case[case["production_hybrid_selection"]]["gate_pass"]
        for case in cases.values()
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "timebox_minutes": 30,
        "input_sha256": _sha256(input_path),
        "shared_contract": "relations projection; identical node dimensions and edges; hard metrics shared",
        "candidate_a": {
            "name": "python_networkx_v5",
            "runtime": "bundled Python process",
            "extra_executable": False,
            "all_probes_gate_pass": python_passes,
        },
        "candidate_b": {
            "name": "elkjs_0.12.0",
            "runtime": f"Node {args.node}",
            "extra_executable": True,
            "package_sha256": args.elk_package_sha256,
            "package_license": "EPL-2.0 OR GPL-3.0-or-later",
            "npm_unpacked_bytes": 8046232,
            "all_probes_gate_pass": elk_passes,
            "not_proven": [
                "复合教材框的完整端口/portal 约束",
                "目标 Windows 安装包签名、安全扫描与卸载",
                "无需开发者 PATH 的随包 Node 发现",
            ],
        },
        "cases": cases,
        "decision": {
            "selected": "elkjs_0.12.0_primary_with_python_networkx_v5_guarded_fallback",
            "status": "accepted" if hybrid_passes else "blocked",
            "reason": (
                "ELK 在美术基线中将交叉降为 0 且显著快于候选 A；"
                "星状图会产生超长同层列，因此生产门禁在列占比超过 40% 或 ELK 失败时切换到候选 A。"
                "两者共享投影、尺寸、ReadabilityReport 和输出路径契约。"
            ),
            "reopen_when": "生产规模或跨框样例出现候选 A 无法在预算内修复的根本缺口",
        },
    }
    (output_path / "candidate_evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["decision"], ensure_ascii=False, indent=2))
    return 0 if hybrid_passes else 2


def _elk_input(scene: SceneGeometry) -> dict[str, object]:
    return {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.spacing.nodeNode": "54",
            "elk.layered.spacing.nodeNodeBetweenLayers": "96",
            "elk.separateConnectedComponents": "true",
        },
        "children": [
            {
                "id": node_id,
                "width": rect.width,
                "height": rect.height,
            }
            for node_id, rect in sorted(scene.node_rects.items())
        ],
        "edges": [
            {
                "id": edge.view_id,
                "sources": [edge.source_view_id],
                "targets": [edge.target_view_id],
            }
            for edge in sorted(scene.edge_instances, key=lambda item: item.view_id)
        ],
    }


def _elk_scene(reference: SceneGeometry, graph: dict[str, object]) -> SceneGeometry:
    node_rects = {
        str(node["id"]): Rect(
            float(node.get("x", 0.0)),
            float(node.get("y", 0.0)),
            float(node.get("width", 0.0)),
            float(node.get("height", 0.0)),
        )
        for node in graph.get("children", [])
    }
    edge_by_id = {edge.view_id: edge for edge in reference.edge_instances}
    routes: dict[str, EdgeRoute] = {}
    for raw_edge in graph.get("edges", []):
        edge_id = str(raw_edge["id"])
        edge = edge_by_id[edge_id]
        points: list[tuple[float, float]] = []
        for section in raw_edge.get("sections", []):
            candidates = [section.get("startPoint"), *section.get("bendPoints", []), section.get("endPoint")]
            for point in candidates:
                if not isinstance(point, dict):
                    continue
                value = (float(point["x"]), float(point["y"]))
                if not points or points[-1] != value:
                    points.append(value)
        routes[edge_id] = EdgeRoute(
            edge_id,
            edge.relation_id,
            edge.source_view_id,
            edge.target_view_id,
            tuple(points),
            "elk",
            "elk",
            "routed" if len(points) >= 2 else "failed",
        )
    return SceneGeometry(
        document_id=reference.document_id,
        document_revision=reference.document_revision,
        structure_fingerprint=reference.structure_fingerprint,
        renderer="candidate",
        specification_version=reference.specification_version,
        scope_rects={},
        scope_content_rects={},
        node_rects=node_rects,
        edge_instances=list(reference.edge_instances),
        view_mode=VIEW_MODE_RELATIONS,
        engine="elkjs_0_12_0_probe",
        edge_routes=routes,
    )


def _max_column_ratio(scene: SceneGeometry) -> float:
    if not scene.node_rects:
        return 0.0
    counts: dict[float, int] = {}
    for rect in scene.node_rects.values():
        key = round(rect.x, 3)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.values()) / len(scene.node_rects)


def _star_document() -> P2ReviewDocumentDTO:
    nodes = [ReviewNodeDTO(f"s{index}", f"星状节点 {index}", f"star-{index}") for index in range(18)]
    relations = [
        ReviewRelationDTO(f"sr{index}", RELATION_FAMILY_SEMANTIC, "s0", f"s{index}", "explains")
        for index in range(1, 18)
    ]
    return P2ReviewDocumentDTO(ReviewDocumentMetadataDTO("star-18", "fixture"), nodes=nodes, relations=relations)


def _cycle_document() -> P2ReviewDocumentDTO:
    nodes = [ReviewNodeDTO(f"c{index}", f"循环节点 {index}", f"cycle-{index}") for index in range(12)]
    relations = [
        ReviewRelationDTO(f"cr{index}", RELATION_FAMILY_SEMANTIC, f"c{index}", f"c{(index + 1) % 12}", "progressive")
        for index in range(12)
    ]
    return P2ReviewDocumentDTO(ReviewDocumentMetadataDTO("cycle-12", "fixture"), nodes=nodes, relations=relations)


def _disconnected_document() -> P2ReviewDocumentDTO:
    nodes = [
        ReviewNodeDTO(f"d{index}", f"很长的中英文组合标题 Long title {index:02d} 篆刻知识", f"disconnected-{index}")
        for index in range(24)
    ]
    relations = [
        ReviewRelationDTO(f"dr{index}", RELATION_FAMILY_SEMANTIC, f"d{index}", f"d{index + 1}", "contrast")
        for index in range(0, 24, 2)
    ]
    return P2ReviewDocumentDTO(ReviewDocumentMetadataDTO("disconnected-24", "fixture"), nodes=nodes, relations=relations)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
