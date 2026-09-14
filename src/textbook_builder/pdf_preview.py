from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QSize, QTimer, Qt, Signal
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QGridLayout, QWidget


class ResponsivePdfControlBar(QWidget):
    """Places page controls and zoom controls on one row when space allows."""

    GROUP_GAP = 24
    ROW_SPACING = 6

    def __init__(
        self,
        page_controls: QWidget,
        zoom_controls: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.page_controls = page_controls
        self.zoom_controls = zoom_controls
        self._single_row = False
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 8, 0, 0)
        self._layout.setHorizontalSpacing(self.GROUP_GAP)
        self._layout.setVerticalSpacing(self.ROW_SPACING)
        self._layout.setColumnStretch(1, 1)
        self._apply_layout(single_row=False)
        QTimer.singleShot(0, self.refresh_layout)

    @property
    def is_single_row(self) -> bool:
        return self._single_row

    def required_single_row_width(self) -> int:
        return (
            self.page_controls.sizeHint().width()
            + self.zoom_controls.sizeHint().width()
            + self.GROUP_GAP
        )

    def refresh_layout(self) -> None:
        available_width = max(0, self.contentsRect().width())
        self._apply_layout(
            single_row=available_width >= self.required_single_row_width()
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_layout()

    def minimumSizeHint(self) -> QSize:
        top_margin = self._layout.contentsMargins().top()
        page_size = self.page_controls.minimumSizeHint()
        zoom_size = self.zoom_controls.minimumSizeHint()
        if self._single_row:
            height = max(page_size.height(), zoom_size.height()) + top_margin
        else:
            height = page_size.height() + zoom_size.height() + self.ROW_SPACING + top_margin
        return QSize(0, height)

    def _apply_layout(self, *, single_row: bool) -> None:
        if self._layout.count() and self._single_row == single_row:
            return
        self._layout.removeWidget(self.page_controls)
        self._layout.removeWidget(self.zoom_controls)
        self._single_row = single_row
        if single_row:
            self._layout.addWidget(
                self.page_controls,
                0,
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
            self._layout.addWidget(
                self.zoom_controls,
                0,
                2,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        else:
            self._layout.addWidget(
                self.page_controls,
                0,
                0,
                1,
                3,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
            self._layout.addWidget(
                self.zoom_controls,
                1,
                0,
                1,
                3,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        self._layout.invalidate()
        self.updateGeometry()


class ZoomablePdfView(QPdfView):
    """QPdfView with bounded, cursor-aware Ctrl+wheel zooming."""

    MIN_ZOOM_FACTOR = 0.25
    MAX_ZOOM_FACTOR = 4.0
    ZOOM_STEP = 1.10

    zoom_state_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.viewport().installEventFilter(self)
        self.zoomFactorChanged.connect(self._notify_zoom_state)
        self.zoomModeChanged.connect(self._notify_zoom_state)

    def has_ready_document(self) -> bool:
        document = self.document()
        return document is not None and document.pageCount() > 0

    def zoom_in(self) -> bool:
        return self.zoom_by_steps(1.0)

    def zoom_out(self) -> bool:
        return self.zoom_by_steps(-1.0)

    def zoom_by_steps(self, steps: float, anchor: QPointF | None = None) -> bool:
        if not self.has_ready_document() or steps == 0:
            return False
        current_factor = max(self.MIN_ZOOM_FACTOR, float(self.zoomFactor()))
        target_factor = current_factor * (self.ZOOM_STEP**steps)
        return self.set_custom_zoom_factor(target_factor, anchor=anchor)

    def set_custom_zoom_factor(
        self,
        factor: float,
        *,
        anchor: QPointF | None = None,
    ) -> bool:
        if not self.has_ready_document():
            return False

        old_factor = max(0.01, float(self.zoomFactor()))
        bounded_factor = max(self.MIN_ZOOM_FACTOR, min(self.MAX_ZOOM_FACTOR, float(factor)))
        anchor_point = anchor or QPointF(
            self.viewport().width() / 2.0,
            self.viewport().height() / 2.0,
        )
        horizontal_bar = self.horizontalScrollBar()
        vertical_bar = self.verticalScrollBar()
        old_horizontal = horizontal_bar.value()
        old_vertical = vertical_bar.value()

        self.setZoomMode(QPdfView.ZoomMode.Custom)
        self.setZoomFactor(bounded_factor)

        scale_ratio = bounded_factor / old_factor

        def restore_anchor() -> None:
            horizontal_bar.setValue(
                round((old_horizontal + anchor_point.x()) * scale_ratio - anchor_point.x())
            )
            vertical_bar.setValue(
                round((old_vertical + anchor_point.y()) * scale_ratio - anchor_point.y())
            )

        QTimer.singleShot(0, restore_anchor)
        self.zoom_state_changed.emit()
        return True

    def reset_to_fit_width(self) -> bool:
        if not self.has_ready_document():
            return False
        self.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.zoom_state_changed.emit()
        return True

    def eventFilter(self, watched, event) -> bool:
        if watched is self.viewport() and event.type() == QEvent.Type.Wheel:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                angle_delta = event.angleDelta().y()
                pixel_delta = event.pixelDelta().y()
                delta = angle_delta if angle_delta else pixel_delta
                if delta and self.has_ready_document():
                    self.zoom_by_steps(delta / 120.0, anchor=event.position())
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _notify_zoom_state(self, *_args: object) -> None:
        self.zoom_state_changed.emit()
