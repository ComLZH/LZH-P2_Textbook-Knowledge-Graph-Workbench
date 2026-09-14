from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
)

from textbook_builder.desktop_app import TextbookWorkbenchWindow  # noqa: E402
from textbook_builder.review_document_io import ReviewDocumentReader  # noqa: E402
from textbook_builder.review_views import VIEW_MODE_RELATIONS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="离屏验证 Qt 兼容图谱渲染器。")
    parser.add_argument("--input", required=True, help="review_document_v2.xlsx 路径")
    parser.add_argument("--output", required=True, help="新的 PNG 输出路径；拒绝覆盖")
    parser.add_argument("--width", type=int, default=1480, help="逻辑视口宽度")
    parser.add_argument("--height", type=int, default=900, help="逻辑视口高度")
    parser.add_argument("--device-pixel-ratio", type=float, default=1.0)
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.is_file():
        parser.error(f"输入文件不存在：{input_path}")
    if output_path.exists():
        parser.error(f"输出文件已存在，拒绝覆盖：{output_path}")
    if args.width < 320 or args.height < 240 or not 0.5 <= args.device_pixel_ratio <= 4.0:
        parser.error("视口至少为 320×240，设备像素比必须位于 0.5–4.0。")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication([])
    window = TextbookWorkbenchWindow()
    try:
        window.review_document = ReviewDocumentReader().read_document(input_path)
        window.graph_view_mode = VIEW_MODE_RELATIONS
        window.graph_scene.clear()
        window._render_projection_graph()
        app.processEvents()

        bounds = window.graph_scene.itemsBoundingRect()
        width = args.width
        height = args.height
        image = QImage(
            round(width * args.device_pixel_ratio),
            round(height * args.device_pixel_ratio),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.setDevicePixelRatio(args.device_pixel_ratio)
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        try:
            window.graph_scene.render(
                painter,
                QRectF(0.0, 0.0, float(width), float(height)),
                bounds,
            )
        finally:
            painter.end()
        if not image.save(str(output_path)):
            raise OSError(f"无法保存 Qt 验证截图：{output_path}")

        items = window.graph_scene.items()
        report = {
            "input": str(input_path),
            "output": str(output_path),
            "scene_bounds": {
                "x": bounds.x(),
                "y": bounds.y(),
                "width": bounds.width(),
                "height": bounds.height(),
            },
            "logical_viewport": {"width": width, "height": height},
            "device_pixel_ratio": image.devicePixelRatio(),
            "physical_image": {"width": image.width(), "height": image.height()},
            "node_items": sum(1 for item in items if item.data(0) == "node"),
            "edge_path_items": sum(isinstance(item, QGraphicsPathItem) for item in items),
            "arrow_polygon_items": sum(isinstance(item, QGraphicsPolygonItem) for item in items),
            "total_items": len(items),
            "image_bytes": output_path.stat().st_size,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        window.close()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
