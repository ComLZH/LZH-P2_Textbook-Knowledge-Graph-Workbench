from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from textbook_builder.review_document_io import ReviewDocumentReader  # noqa: E402
from textbook_builder.review_views import (  # noqa: E402
    G6ReviewHtmlRenderer,
    GraphReviewPayloadBuilder,
    VIEW_MODE_RELATIONS,
    VIEW_MODE_TEXTBOOK,
)
from textbook_builder.services import WorkbenchReviewService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="只读验证知识图谱二维布局、路由和标签。")
    parser.add_argument("--input", required=True, help="review_document_v2.xlsx 路径")
    parser.add_argument("--output", required=True, help="新的独立验收目录；默认拒绝覆盖")
    parser.add_argument(
        "--mode",
        choices=(VIEW_MODE_RELATIONS, VIEW_MODE_TEXTBOOK, "both"),
        default="both",
        help="验收阅读模式",
    )
    args = parser.parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.is_file():
        parser.error(f"输入文件不存在：{input_path}")
    if output_path == input_path or input_path in output_path.parents:
        parser.error("输出目录不能是输入文件或位于输入文件内部。")
    if output_path.exists():
        parser.error(f"输出目录已存在，拒绝覆盖：{output_path}")
    output_path.mkdir(parents=True)

    document = ReviewDocumentReader().read_document(input_path)
    service = WorkbenchReviewService()
    modes = (
        (VIEW_MODE_RELATIONS, VIEW_MODE_TEXTBOOK)
        if args.mode == "both"
        else (args.mode,)
    )
    summary: dict[str, object] = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input": {
            "path": str(input_path),
            "sha256": _sha256(input_path),
            "document_id": document.metadata.document_id,
            "document_revision": document.metadata.revision,
            "business_fingerprint": document.business_fingerprint(),
        },
        "modes": {},
    }
    exit_code = 0
    for mode in modes:
        mode_dir = output_path / mode
        mode_dir.mkdir()
        bundle = service.build_review_render_bundle(
            document,
            renderer="g6",
            view_mode=mode,
        )
        payload = GraphReviewPayloadBuilder().build_from_projection(
            projection=bundle.projection,
            geometry=bundle.geometry,
            document=document,
        )
        payload_path = mode_dir / "graph_payload.json"
        scene_path = mode_dir / "scene_layout.json"
        report_path = mode_dir / "readability_report.json"
        html_path = mode_dir / "graph_review.html"
        vendor_source = PROJECT_ROOT / "storage" / "graph_review_static" / "vendor" / "g6" / "g6.min.js"
        vendor_target = mode_dir / "vendor" / "g6" / "g6.min.js"
        if not vendor_source.is_file():
            raise FileNotFoundError(f"缺少离线 G6 运行库：{vendor_source}")
        vendor_target.parent.mkdir(parents=True)
        shutil.copy2(vendor_source, vendor_target)
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        scene_path.write_text(
            json.dumps(
                {
                    "document_id": bundle.geometry.document_id,
                    "document_revision": bundle.geometry.document_revision,
                    "view_mode": bundle.geometry.view_mode,
                    "layout_version": bundle.geometry.layout_version,
                    "engine": bundle.geometry.engine,
                    "canvas_rect": asdict(bundle.geometry.canvas_rect),
                    "node_rects": {key: asdict(value) for key, value in bundle.geometry.node_rects.items()},
                    "scope_rects": {key: asdict(value) for key, value in bundle.geometry.scope_rects.items()},
                    "visual_groups": {key: asdict(value) for key, value in bundle.geometry.visual_groups.items()},
                    "edge_routes": {key: asdict(value) for key, value in bundle.geometry.edge_routes.items()},
                    "edge_labels": {key: asdict(value) for key, value in bundle.geometry.edge_labels.items()},
                    "issues": [asdict(value) for value in bundle.geometry.issues],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        report = asdict(bundle.geometry.readability)
        report.update(
            {
                "node_count": len(bundle.projection.node_instances),
                "edge_count": len(bundle.projection.edge_instances),
                "scope_count": len(bundle.projection.scopes),
                "visual_group_count": len(bundle.geometry.visual_groups),
                "reading_sequences": [asdict(value) for value in bundle.projection.reading_sequences],
                "visible_label_count": sum(label.visible for label in bundle.geometry.edge_labels.values()),
                "coverage": bundle.projection.relation_coverage(),
                "projection_issues": [asdict(value) for value in bundle.projection.issues],
            }
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        G6ReviewHtmlRenderer().render_to_file(payload=payload, output_path=html_path)
        hard_pass = (
            report["node_overlap"] == 0
            and report["edge_node_intrusion"] == 0
            and report["ambiguous_overlap"] == 0
            and report["label_overlap"] == 0
            and report["failed_edges"] == 0
            and report["terminal_direction_violation"] == 0
            and report["terminal_stub_short"] == 0
            and report["arrow_occlusion"] == 0
            and report["port_spacing_violation"] == 0
            and report["group_overlap"] == 0
            and report["group_overflow"] == 0
        )
        if not hard_pass:
            exit_code = 2
        summary["modes"][mode] = {
            "hard_pass": hard_pass,
            "node_count": report["node_count"],
            "edge_count": report["edge_count"],
            "canvas": asdict(bundle.geometry.canvas_rect),
            "readability": report,
            "files": {
                "payload": str(payload_path.relative_to(output_path)),
                "scene": str(scene_path.relative_to(output_path)),
                "report": str(report_path.relative_to(output_path)),
                "html": str(html_path.relative_to(output_path)),
            },
        }
    (output_path / "validation_report.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
