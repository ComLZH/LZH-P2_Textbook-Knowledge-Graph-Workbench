from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QApplication

from textbook_builder.desktop_app import TextbookWorkbenchWindow


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qt_app: QApplication) -> TextbookWorkbenchWindow:
    workbench = TextbookWorkbenchWindow()
    workbench.resize(1180, 720)
    workbench.show()
    qt_app.processEvents()
    yield workbench
    workbench.close()
    qt_app.processEvents()


def _send_wheel(
    window: TextbookWorkbenchWindow,
    qt_app: QApplication,
    *,
    delta: int,
    modifiers: Qt.KeyboardModifier,
) -> QWheelEvent:
    position = QPointF(240, 180)
    event = QWheelEvent(
        position,
        position,
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(window.pdf_view.viewport(), event)
    for _ in range(5):
        qt_app.processEvents()
    return event


def _settle(qt_app: QApplication, cycles: int = 8) -> None:
    for _ in range(cycles):
        qt_app.processEvents()


def test_pdf_zoom_controls_start_disabled_without_document(
    window: TextbookWorkbenchWindow,
) -> None:
    assert window.pdf_zoom_label.text() == "适合宽度"
    assert window.pdf_zoom_out_button.isEnabled() is False
    assert window.pdf_zoom_in_button.isEnabled() is False
    assert window.pdf_fit_width_button.isEnabled() is False


def test_ctrl_wheel_zooms_but_plain_wheel_only_scrolls(
    window: TextbookWorkbenchWindow,
    qt_app: QApplication,
) -> None:
    source_path = PROJECT_ROOT / "textbook" / "目录页构建测试.pdf"
    assert window._load_source_path(source_path) is True
    assert window.pdf_view.zoomMode() == QPdfView.ZoomMode.FitToWidth
    assert window.pdf_zoom_label.text() == "适合宽度"

    initial_factor = window.pdf_view.zoomFactor()
    ctrl_event = _send_wheel(
        window,
        qt_app,
        delta=120,
        modifiers=Qt.KeyboardModifier.ControlModifier,
    )

    assert ctrl_event.isAccepted() is True
    assert window.pdf_view.zoomMode() == QPdfView.ZoomMode.Custom
    assert window.pdf_view.zoomFactor() > initial_factor
    assert window.pdf_zoom_label.text().endswith("%")

    enlarged_factor = window.pdf_view.zoomFactor()
    ctrl_down_event = _send_wheel(
        window,
        qt_app,
        delta=-120,
        modifiers=Qt.KeyboardModifier.ControlModifier,
    )
    assert ctrl_down_event.isAccepted() is True
    assert window.pdf_view.zoomFactor() < enlarged_factor

    custom_factor = window.pdf_view.zoomFactor()
    _send_wheel(
        window,
        qt_app,
        delta=-120,
        modifiers=Qt.KeyboardModifier.NoModifier,
    )
    assert window.pdf_view.zoomMode() == QPdfView.ZoomMode.Custom
    assert window.pdf_view.zoomFactor() == pytest.approx(custom_factor)


def test_zoom_buttons_shortcuts_bounds_and_fit_width_reset(
    window: TextbookWorkbenchWindow,
    qt_app: QApplication,
) -> None:
    source_path = PROJECT_ROOT / "textbook" / "目录页构建测试.pdf"
    assert window._load_source_path(source_path) is True
    assert {shortcut.toString() for shortcut in window.pdf_zoom_in_action.shortcuts()} == {
        "Ctrl++",
        "Ctrl+=",
    }
    assert window.pdf_zoom_out_action.shortcut().toString() == "Ctrl+-"
    assert window.pdf_fit_width_action.shortcut().toString() == "Ctrl+0"

    window.pdf_zoom_in_action.trigger()
    qt_app.processEvents()
    assert window.pdf_view.zoomMode() == QPdfView.ZoomMode.Custom

    window.pdf_view.set_custom_zoom_factor(99.0)
    qt_app.processEvents()
    assert window.pdf_view.zoomFactor() == pytest.approx(4.0)
    assert window.pdf_zoom_in_button.isEnabled() is False

    window.pdf_view.set_custom_zoom_factor(0.01)
    qt_app.processEvents()
    assert window.pdf_view.zoomFactor() == pytest.approx(0.25)
    assert window.pdf_zoom_out_button.isEnabled() is False

    window.pdf_fit_width_action.trigger()
    qt_app.processEvents()
    assert window.pdf_view.zoomMode() == QPdfView.ZoomMode.FitToWidth
    assert window.pdf_zoom_label.text() == "适合宽度"
    assert window.pdf_fit_width_button.isEnabled() is False


def test_page_jump_preserves_custom_zoom(
    window: TextbookWorkbenchWindow,
    qt_app: QApplication,
) -> None:
    source_path = PROJECT_ROOT / "textbook" / "目录页构建测试.pdf"
    assert window._load_source_path(source_path) is True
    assert window.pdf_view.set_custom_zoom_factor(1.5) is True
    qt_app.processEvents()

    window.pdf_jump_input.setValue(2)
    window._jump_to_pdf_page()
    qt_app.processEvents()

    assert window.pdf_view.pageNavigator().currentPage() == 1
    assert window.pdf_view.zoomMode() == QPdfView.ZoomMode.Custom
    assert window.pdf_view.zoomFactor() == pytest.approx(1.5)


def test_pdf_control_bar_switches_between_wide_and_narrow_layouts(
    window: TextbookWorkbenchWindow,
    qt_app: QApplication,
) -> None:
    source_path = PROJECT_ROOT / "textbook" / "目录页构建测试.pdf"
    assert window._load_source_path(source_path) is True

    for width, height in [(1180, 720), (1480, 900), (1850, 1164)]:
        window.resize(width, height)
        window._apply_layout_preset("show_all")
        _settle(qt_app)
        narrow_height = window.pdf_control_bar.height()
        assert window.pdf_control_bar.is_single_row is False
        assert window.pdf_page_controls.y() < window.pdf_zoom_controls.y()
        assert (
            window.pdf_zoom_controls.geometry().right()
            <= window.pdf_control_bar.contentsRect().right()
        )
        assert (
            window.pdf_control_bar.contentsRect().right()
            - window.pdf_zoom_controls.geometry().right()
            <= 1
        )

        window._apply_layout_preset("focus_preview")
        _settle(qt_app)
        assert window.pdf_control_bar.is_single_row is True
        assert window.pdf_page_controls.y() == window.pdf_zoom_controls.y()
        assert (
            window.pdf_page_controls.geometry().right()
            < window.pdf_zoom_controls.geometry().left()
        )
        assert (
            window.pdf_zoom_controls.geometry().right()
            <= window.pdf_control_bar.contentsRect().right()
        )
        assert window.pdf_control_bar.height() <= narrow_height - 40

        window._apply_layout_preset("show_all")
        _settle(qt_app)
        assert window.pdf_control_bar.is_single_row is False

    window.resize(1180, 720)
    window._apply_layout_preset("show_all")
    _settle(qt_app)
    for control in [
        window.pdf_prev_button,
        window.pdf_next_button,
        window.pdf_jump_label,
        window.pdf_jump_input,
        window.pdf_jump_button,
    ]:
        assert control.geometry().right() <= window.pdf_page_controls.contentsRect().right()
