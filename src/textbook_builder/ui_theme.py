from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QWidget


WORKBENCH_STYLESHEET = """
QMainWindow, QWidget#workspaceRoot {
    background: #f4f7fb;
    color: #172033;
}
QWidget {
    font-size: 13px;
}
QMenuBar {
    background: #ffffff;
    border-bottom: 1px solid #dce3ed;
    padding: 3px 8px;
}
QMenuBar::item {
    padding: 6px 10px;
    border-radius: 5px;
}
QMenuBar::item:selected, QMenu::item:selected {
    background: #eaf2ff;
    color: #1d4ed8;
}
QMenu {
    background: #ffffff;
    border: 1px solid #dce3ed;
    padding: 5px;
}
QMenu::item {
    padding: 7px 28px 7px 10px;
    border-radius: 5px;
}
QFrame#commandBar {
    background: #ffffff;
    border: 1px solid #dce3ed;
    border-radius: 12px;
}
QFrame#controlPanel, QFrame[role="panelCard"] {
    background: #ffffff;
    border: 1px solid #dce3ed;
    border-radius: 10px;
}
QLabel#appTitle {
    color: #172033;
    font-size: 20px;
    font-weight: 700;
}
QLabel[role="sectionTitle"] {
    color: #172033;
    font-size: 15px;
    font-weight: 700;
}
QLabel[role="muted"] {
    color: #64748b;
    font-size: 12px;
}
QLabel[role="fileName"] {
    color: #334155;
    font-weight: 600;
    padding: 2px 0;
}
QLabel[role="badge"] {
    border: 1px solid #cbd5e1;
    border-radius: 10px;
    padding: 3px 9px;
    color: #475569;
    background: #f8fafc;
    font-size: 12px;
    font-weight: 600;
}
QLabel[role="badge"][tone="primary"] {
    color: #1d4ed8;
    border-color: #bfdbfe;
    background: #eff6ff;
}
QLabel[role="badge"][tone="success"] {
    color: #166534;
    border-color: #bbf7d0;
    background: #f0fdf4;
}
QLabel[role="badge"][tone="warning"] {
    color: #92400e;
    border-color: #fed7aa;
    background: #fff7ed;
}
QLabel[role="badge"][tone="danger"] {
    color: #b91c1c;
    border-color: #fecaca;
    background: #fef2f2;
}
QGroupBox[role="stepCard"] {
    background: #ffffff;
    border: 1px solid #dce3ed;
    border-radius: 9px;
    margin-top: 18px;
    padding: 12px 10px 10px 10px;
    font-weight: 700;
    color: #26344d;
}
QGroupBox[role="stepCard"]::title {
    subcontrol-origin: margin;
    left: 11px;
    padding: 0 6px;
    background: #ffffff;
}
QScrollArea#controlScroll, QScrollArea#controlScroll > QWidget > QWidget {
    background: transparent;
    border: 0;
}
QLineEdit, QComboBox, QSpinBox, QTextEdit {
    background: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 5px 7px;
    min-height: 22px;
    selection-background-color: #bfdbfe;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {
    border: 1px solid #3b82f6;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: #94a3b8;
    background: #f8fafc;
}
QTextEdit#runLog {
    background: #f8fafc;
    color: #475569;
    font-size: 12px;
}
QPushButton, QToolButton {
    min-height: 30px;
    padding: 4px 11px;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    background: #ffffff;
    color: #26344d;
    font-weight: 600;
}
QPushButton:hover, QToolButton:hover {
    background: #f1f5f9;
    border-color: #94a3b8;
}
QPushButton:pressed, QToolButton:pressed {
    background: #e2e8f0;
}
QPushButton:disabled, QToolButton:disabled {
    color: #94a3b8;
    background: #f1f5f9;
    border-color: #e2e8f0;
}
QPushButton[role="primary"] {
    color: #ffffff;
    background: #2563eb;
    border-color: #2563eb;
}
QPushButton[role="primary"]:hover {
    background: #1d4ed8;
    border-color: #1d4ed8;
}
QPushButton[role="success"] {
    color: #166534;
    background: #f0fdf4;
    border-color: #bbf7d0;
}
QPushButton[role="danger"] {
    color: #b91c1c;
    background: #fef2f2;
    border-color: #fecaca;
}
QPushButton[role="primary"]:disabled,
QPushButton[role="success"]:disabled,
QPushButton[role="danger"]:disabled,
QPushButton[role="subtle"]:disabled {
    color: #94a3b8;
    background: #f1f5f9;
    border-color: #e2e8f0;
}
QPushButton[role="subtle"], QToolButton[role="subtle"] {
    color: #475569;
    background: #f8fafc;
    border-color: #e2e8f0;
}
QFrame[role="segmented"] {
    background: #f1f5f9;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
}
QToolButton[role="segment"] {
    min-height: 25px;
    padding: 3px 10px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: #64748b;
}
QToolButton[role="segment"]:checked {
    color: #1d4ed8;
    background: #ffffff;
    border: 1px solid #bfdbfe;
}
QToolButton#advancedToggle {
    border: 0;
    background: transparent;
    padding: 4px 2px;
    text-align: left;
    color: #26344d;
    font-weight: 700;
}
QToolButton[role="inline"] {
    min-width: 22px;
    max-width: 24px;
    min-height: 20px;
    max-height: 22px;
    padding: 0;
    border-radius: 5px;
    font-size: 12px;
    font-weight: 700;
}
QTabWidget::pane {
    border: 1px solid #dce3ed;
    background: #ffffff;
    border-radius: 7px;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: #64748b;
    padding: 8px 13px;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}
QTabBar::tab:selected {
    color: #1d4ed8;
    border-bottom-color: #2563eb;
    background: #eff6ff;
}
QTableWidget {
    background: #ffffff;
    alternate-background-color: #f8fafc;
    border: 1px solid #dce3ed;
    border-radius: 6px;
    gridline-color: #e8edf4;
    selection-background-color: #dbeafe;
    selection-color: #172033;
}
QHeaderView::section {
    background: #f8fafc;
    color: #475569;
    border: 0;
    border-bottom: 1px solid #dce3ed;
    padding: 7px 6px;
    font-weight: 700;
}
QSplitter::handle {
    background: #e8edf4;
}
QSplitter::handle:hover {
    background: #bfdbfe;
}
QStatusBar {
    background: #ffffff;
    color: #64748b;
    border-top: 1px solid #dce3ed;
}
QProgressBar {
    border: 1px solid #dce3ed;
    border-radius: 6px;
    background: #f1f5f9;
    text-align: center;
}
QProgressBar::chunk {
    background: #2563eb;
    border-radius: 5px;
}
"""


def apply_workbench_theme(widget: QWidget) -> None:
    font = QFont(widget.font())
    font.setPointSize(10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    widget.setFont(font)
    widget.setStyleSheet(WORKBENCH_STYLESHEET)


def refresh_dynamic_style(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
