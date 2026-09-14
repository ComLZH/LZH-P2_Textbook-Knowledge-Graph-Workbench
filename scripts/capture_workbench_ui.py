from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from textbook_builder.desktop_app import TextbookWorkbenchWindow
from textbook_builder.readers import DraftWorkbookReader
from textbook_builder.review_document import ReviewDocumentMigrator


def _settle(milliseconds: int = 250) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _save_window(window: TextbookWorkbenchWindow, path: Path) -> None:
    _settle()
    if not window.grab().save(str(path), "PNG"):
        raise RuntimeError(f"无法保存工作台截图：{path}")


def _evaluate_javascript(view: object, script: str) -> object:
    loop = QEventLoop()
    result: dict[str, object] = {"value": None}

    def finish(value: object) -> None:
        result["value"] = value
        loop.quit()

    view.page().runJavaScript(script, finish)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    value = result["value"]
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染 P2 工作台视觉验收截图。")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "storage" / "ui_validation_20260823",
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--draft", type=Path)
    parser.add_argument("--canonical-nodes", type=Path)
    parser.add_argument("--width", type=int, default=1480)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = TextbookWorkbenchWindow()
    window.resize(args.width, args.height)
    window.show()
    _save_window(window, args.output_dir / f"workbench_empty_{args.width}x{args.height}.png")

    if args.source is not None:
        source_path = args.source.resolve()
        if not window._load_source_path(source_path):
            raise RuntimeError(f"无法载入视觉验收教材：{source_path}")
        window.model_details_button.setChecked(True)
        _save_window(window, args.output_dir / f"workbench_ready_{args.width}x{args.height}.png")
        if source_path.suffix.lower() == ".pdf":
            window.pdf_view.zoom_in()
            _save_window(
                window,
                args.output_dir / f"workbench_pdf_zoomed_{args.width}x{args.height}.png",
            )
            window.pdf_view.reset_to_fit_width()
            window._apply_layout_preset("focus_preview")
            _save_window(
                window,
                args.output_dir
                / f"workbench_pdf_focus_preview_{args.width}x{args.height}.png",
            )
            window.pdf_view.zoom_in()
            _save_window(
                window,
                args.output_dir
                / f"workbench_pdf_focus_preview_zoomed_{args.width}x{args.height}.png",
            )
            window.pdf_view.reset_to_fit_width()
            window._apply_layout_preset("show_all")

    if args.draft is not None:
        draft_path = args.draft.resolve()
        drafts = DraftWorkbookReader().read(draft_path)
        source_id = drafts[0].source_id if drafts else "LOCAL"
        window.review_session.reset(drafts)
        occurrence_hints = None
        if args.canonical_nodes is not None:
            canonical_rows = json.loads(
                args.canonical_nodes.resolve().read_text(encoding="utf-8")
            )
            occurrence_hints = {
                item["node_id"]: {
                    "chapter": item.get("chapter", ""),
                    "section": item.get("section", ""),
                    "temporary_ids": item.get("source_temporary_ids", []),
                }
                for item in canonical_rows
            }
        window.review_document = ReviewDocumentMigrator().migrate(
            drafts,
            source_identity=source_id,
            occurrence_hints=occurrence_hints,
        )
        window.workbook = window.review_service.build_review_workbook(
            drafts,
            source_id=source_id,
        )
        window._render_tables()
        window._render_graph()
        window._set_workspace_state("待审查", "warning")
        window._apply_layout_preset("focus_graph")
        if window.g6_graph_view is not None:
            window.graph_tabs.setCurrentWidget(window.g6_graph_view)
            for _attempt in range(20):
                if window.g6_graph_loaded:
                    break
                _settle(250)
            if window.g6_graph_loaded:
                javascript_metrics = _evaluate_javascript(
                    window.g6_graph_view,
                    """JSON.stringify({
                      readyState: document.readyState,
                      graphApiReady: typeof window.focusGraphReviewItem === 'function',
                      payloadNodeCount: (window.GRAPH_REVIEW_PAYLOAD.nodes || []).length,
                      payloadEdgeCount: (window.GRAPH_REVIEW_PAYLOAD.edges || []).length,
                      payloadLayoutEdgeCount: (window.GRAPH_REVIEW_PAYLOAD.layout_edges || []).length,
                      payloadComboCount: (window.GRAPH_REVIEW_PAYLOAD.combos || []).length,
                      canvasCount: document.querySelectorAll('canvas').length,
                      geometryReport: window.getGraphReviewGeometryReport
                        ? window.getGraphReviewGeometryReport()
                        : null,
                      pageText: document.body.innerText.slice(0, 500)
                    })""",
                )
                metrics = {
                    "desktopLoadFinished": True,
                    "javascript": javascript_metrics,
                }
            else:
                metrics = {
                    "desktopLoadFinished": False,
                    "note": "当前截图环境未捕获 WebEngine 表面，请使用真实浏览器验收 G6 页面。",
                }
            (args.output_dir / "g6_runtime_metrics.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            g6_screenshot_path = (
                args.output_dir / f"workbench_g6_{args.width}x{args.height}.png"
            )
            if window.g6_graph_loaded:
                _save_window(window, g6_screenshot_path)
            else:
                g6_screenshot_path.unlink(missing_ok=True)
        window.graph_tabs.setCurrentWidget(window.graph_view)
        _settle(300)
        _save_window(
            window,
            args.output_dir / f"workbench_compatibility_{args.width}x{args.height}.png",
        )

    window.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
