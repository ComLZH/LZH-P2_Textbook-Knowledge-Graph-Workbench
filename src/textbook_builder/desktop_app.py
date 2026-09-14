from __future__ import annotations

import json
import math
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import QLineF, QObject, QPointF, QTimer, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover - optional desktop dependency
    QWebEngineView = None
try:
    from PySide6.QtWebChannel import QWebChannel
except Exception:  # pragma: no cover - optional desktop dependency
    QWebChannel = None
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsTextItem,
    QGraphicsView,
    QGraphicsItem,
    QGraphicsItemGroup,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from .contracts import (
    DraftKnowledgeItemDTO,
    EvidenceAnchorDTO,
    FormalEdgeDTO,
    FormalNodeDTO,
    SourceRecordDTO,
)
from .quality import GraphQualityIssue, GraphQualityReport
from .progress_recording import (
    PROGRESS_MODE_FULL,
    PROGRESS_MODE_OFF,
    PROGRESS_MODE_SUMMARY,
    normalize_progress_mode,
)
from .pdf_preview import ResponsivePdfControlBar, ZoomablePdfView
from .readers import DocumentReadOptions
from .review_views import (
    G6ReviewHtmlRenderer,
    GraphReviewPayloadBuilder,
    VIEW_MODE_RELATIONS,
    VIEW_MODE_TEXTBOOK,
    relation_display_spec,
)
from .review_document import (
    ExportProfileDTO,
    P2ReviewDocumentDTO,
    RELATION_FAMILY_MEMBERSHIP,
    ReviewDecisionDTO,
    document_to_legacy_drafts,
)
from .review_document_io import ReviewDocumentFormatError, ReviewDocumentReader
from .review_document_merge import ExcelImportPlanner
from .services import (
    ReviewDocumentCommand,
    ReviewDocumentSession,
    WorkbenchAnalysisCanceled,
    WorkbenchAnalysisRequest,
    WorkbenchAnalysisResult,
    WorkbenchAnalysisService,
    WorkbenchReviewSession,
    WorkbenchReviewService,
)
from .ui_theme import apply_workbench_theme, refresh_dynamic_style
from .utils.app_info import load_app_info
from .utils.env_config import env_value, load_project_env
from .utils.chapter_layout import (
    ChapterLayoutPlan,
    build_chapter_layout_plans,
    build_child_map,
    structural_order_key,
)
from .utils.graph_semantics import (
    is_container_node_type,
    normalize_node_type,
    normalize_relation_type,
)
from .utils.relation_layout import (
    RELATION_LAYOUT_REVISION,
    ChapterRelationLayout,
    build_relation_layouts,
    relation_directionality,
    relation_edge_id,
)
from .utils.relation_endpoints import normalize_draft_relation_endpoints
from .utils.review_status import normalize_review_status
from .utils.subject_metadata import derive_subject_metadata, missing_required_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKBENCH_STORAGE = PROJECT_ROOT / "storage" / "desktop_workbench"
GRAPH_REVIEW_STATIC = PROJECT_ROOT / "storage" / "graph_review_static"
GRAPH_REVIEW_VIEW_STORAGE = WORKBENCH_STORAGE / "graph_review_views"
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}
STATUS_OPTIONS = [
    "pending",
    "accepted",
    "needs_revision",
    "needs_expert_review",
    "rejected",
]
GRAPH_LAYOUT_VERSION = 6
STATUS_LABELS = {
    "pending": "待审查",
    "accepted": "已通过",
    "rejected": "已拒绝",
    "needs_revision": "需修改",
    "needs_expert_review": "需专家复核",
}
STATUS_COLORS = {
    "pending": {"bg": "#fff7ed", "fg": "#9a3412", "border": "#fdba74"},
    "accepted": {"bg": "#ecfdf3", "fg": "#166534", "border": "#86efac"},
    "needs_revision": {"bg": "#fff1f2", "fg": "#be123c", "border": "#fda4af"},
    "needs_expert_review": {"bg": "#f5f3ff", "fg": "#6d28d9", "border": "#c4b5fd"},
    "rejected": {"bg": "#f8fafc", "fg": "#475569", "border": "#cbd5e1"},
}
KNOWLEDGE_TYPE_LABELS = {
    "chapter": "章节",
    "section": "小节",
    "subsection": "子小节",
    "concept": "概念",
    "property": "性质",
    "rule": "规则",
    "theorem": "定理",
    "formula": "公式",
    "method": "方法",
    "representation": "表征",
    "problem_type": "题型",
    "application": "应用",
}


class DraggableNodeGroup(QGraphicsItemGroup):
    def __init__(self, node_id: str, release_callback: Any) -> None:
        super().__init__()
        self.node_id = node_id
        self._release_callback = release_callback
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event: object) -> None:
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: object) -> None:
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        position = self.pos()
        self._release_callback(self.node_id, (float(position.x()), float(position.y())))


class ReviewTableWidget(QTableWidget):
    def mousePressEvent(self, event: object) -> None:
        position_getter = getattr(event, "position", None)
        point = position_getter().toPoint() if callable(position_getter) else event.pos()
        if not self.indexAt(point).isValid():
            self.clearSelection()
            self.setCurrentCell(-1, -1)
            event.accept()
            return
        super().mousePressEvent(event)


class InlineActionCell(QWidget):
    def __init__(self, row: int, select_callback: Any) -> None:
        super().__init__()
        self._row = row
        self._select_callback = select_callback

    def mousePressEvent(self, event: object) -> None:
        self._select_callback(self._row)
        super().mousePressEvent(event)


class InlineLineEdit(QLineEdit):
    def __init__(self, row: int, select_callback: Any, text: str = "") -> None:
        super().__init__(text)
        self._row = row
        self._select_callback = select_callback

    def mousePressEvent(self, event: object) -> None:
        self._select_callback(self._row)
        super().mousePressEvent(event)

    def focusInEvent(self, event: object) -> None:
        self._select_callback(self._row)
        super().focusInEvent(event)


class GraphReviewBridge(QObject):
    def __init__(
        self,
        save_callback: Any,
        selection_callback: Any | None = None,
        relayout_callback: Any | None = None,
    ) -> None:
        super().__init__()
        self._save_callback = save_callback
        self._selection_callback = selection_callback
        self._relayout_callback = relayout_callback

    @Slot(str)
    def saveViewConfig(self, config_json: str) -> None:
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError:
            return
        if isinstance(config, dict):
            self._save_callback(config)

    @Slot(str, str, str)
    def selectGraphItem(self, item_type: str, business_id: str, view_id: str) -> None:
        if self._selection_callback is not None:
            self._selection_callback(item_type, business_id, view_id)

    @Slot()
    def requestRelayout(self) -> None:
        if self._relayout_callback is not None:
            self._relayout_callback()


class AnalysisProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正在分析教材")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        self.message_label = QLabel("正在准备分析任务...")
        self.message_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.tip_label = QLabel("分析期间主窗口仍可保持响应，若需要可以取消本次任务。")
        self.tip_label.setWordWrap(True)
        self.cancel_button = QPushButton("取消本次分析")
        self.cancel_button.clicked.connect(self._emit_cancel)

        layout.addWidget(self.message_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.tip_label)
        layout.addWidget(self.cancel_button)

    def update_progress(self, value: int, message: str) -> None:
        self.progress_bar.setValue(max(0, min(100, value)))
        self.message_label.setText(message)

    def set_cancel_pending(self) -> None:
        self.tip_label.setText("已请求取消。如果当前正在等待模型返回，将在本轮调用结束后停止更新结果。")
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("已请求取消")

    def _emit_cancel(self) -> None:
        self.set_cancel_pending()
        self.cancel_requested.emit()


class AnalysisWorker(QObject):
    progress = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)
    canceled = Signal()

    def __init__(
        self,
        *,
        analysis_service: WorkbenchAnalysisService,
        source_path: Path,
        source_format: str,
        options: DocumentReadOptions,
        start_page: int,
        end_page: int,
        scope_label: str,
        model_mode: str,
        api_key: str,
        model_name: str,
        base_url: str,
        dotenv_values: dict[str, str],
        ocr_enabled: bool,
        page_order_check_enabled: bool,
        staged_pipeline_enabled: bool,
        progress_recording_mode: str,
    ) -> None:
        super().__init__()
        self.analysis_service = analysis_service
        self.source_path = source_path
        self.source_format = source_format
        self.options = options
        self.start_page = start_page
        self.end_page = end_page
        self.scope_label = scope_label
        self.model_mode = model_mode
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url
        self.dotenv_values = dict(dotenv_values)
        self.ocr_enabled = ocr_enabled
        self.page_order_check_enabled = page_order_check_enabled
        self.staged_pipeline_enabled = staged_pipeline_enabled
        self.progress_recording_mode = progress_recording_mode
        self.cancel_requested = False

    def cancel(self) -> None:
        self.cancel_requested = True

    def run(self) -> None:
        try:
            result = self.analysis_service.analyze(
                WorkbenchAnalysisRequest(
                    source_path=self.source_path,
                    source_format=self.source_format,
                    options=self.options,
                    start_page=self.start_page,
                    end_page=self.end_page,
                    scope_label=self.scope_label,
                    model_mode=self.model_mode,
                    api_key=self.api_key,
                    model_name=self.model_name,
                    base_url=self.base_url,
                    dotenv_values=self.dotenv_values,
                    ocr_enabled=self.ocr_enabled,
                    page_order_check_enabled=self.page_order_check_enabled,
                    staged_pipeline_enabled=self.staged_pipeline_enabled,
                    progress_recording_mode=self.progress_recording_mode,
                ),
                on_progress=self.progress.emit,
                is_canceled=lambda: self.cancel_requested,
            )
            self.finished.emit(result)
        except WorkbenchAnalysisCanceled:
            self.canceled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class TextbookWorkbenchWindow(QMainWindow):
    @property
    def drafts(self) -> list[DraftKnowledgeItemDTO]:
        return self.review_session.drafts

    @drafts.setter
    def drafts(self, value: list[DraftKnowledgeItemDTO]) -> None:
        self.review_session.drafts = value

    @property
    def relation_status_overrides(self) -> dict[str, str]:
        return self.review_session.relation_status_overrides

    @relation_status_overrides.setter
    def relation_status_overrides(self, value: dict[str, str]) -> None:
        self.review_session.relation_status_overrides = value

    @property
    def graph_node_positions(self) -> dict[str, tuple[float, float]]:
        return self.review_session.graph_node_positions

    @graph_node_positions.setter
    def graph_node_positions(self, value: dict[str, tuple[float, float]]) -> None:
        self.review_session.graph_node_positions = value

    @property
    def g6_view_config(self) -> dict[str, Any]:
        return self.review_session.g6_view_config

    @g6_view_config.setter
    def g6_view_config(self, value: dict[str, Any]) -> None:
        self.review_session.g6_view_config = value

    @property
    def undo_stack(self) -> list[Any]:
        return self.review_session.undo_stack

    @undo_stack.setter
    def undo_stack(self, value: list[Any]) -> None:
        self.review_session.undo_stack = value

    def __init__(
        self,
        *,
        analysis_service: WorkbenchAnalysisService | None = None,
        review_service: WorkbenchReviewService | None = None,
        review_session: WorkbenchReviewSession | None = None,
    ) -> None:
        super().__init__()
        self.review_session = review_session or WorkbenchReviewSession()
        self.app_info = load_app_info(PROJECT_ROOT / "config" / "app_info.json")
        self.setWindowTitle(self.app_info.application.display_name)
        self.resize(1480, 900)
        self.setMinimumSize(1180, 720)
        self.source_path: Path | None = None
        self.source_format = ""
        self.current_record: SourceRecordDTO | None = None
        self.review_document: P2ReviewDocumentDTO | None = None
        self.current_staged_outcome: Any = None
        self.drafts: list[DraftKnowledgeItemDTO] = []
        self.workbook = None
        self.layout_plans: dict[str, ChapterLayoutPlan] = {}
        self.layout_recommendations: dict[str, dict[str, object]] = {}
        self.pdf_document: QPdfDocument | None = None
        self.dotenv_values = load_project_env(PROJECT_ROOT)
        self.analysis_service = analysis_service or WorkbenchAnalysisService(
            render_dir=WORKBENCH_STORAGE / "rendered_pages",
            ocr_settings=self.dotenv_values,
            progress_root=PROJECT_ROOT / "progress_recordings",
        )
        self.review_service = review_service or WorkbenchReviewService()
        self.analysis_thread: QThread | None = None
        self.analysis_worker: AnalysisWorker | None = None
        self.analysis_dialog: AnalysisProgressDialog | None = None
        self._pdf_navigator_connected = False
        self.graph_node_positions: dict[str, tuple[float, float]] = {}
        self.relation_status_overrides: dict[str, str] = {}
        self.g6_view_config: dict[str, Any] = {}
        self.g6_render_revision = 0
        self.g6_graph_loaded = False
        self.graph_view_mode = VIEW_MODE_RELATIONS
        self._selected_graph_view_id = ""
        self._review_document_undo_stack: list[tuple[str, P2ReviewDocumentDTO]] = []
        self.g6_graph_bridge: GraphReviewBridge | None = None
        self.g6_web_channel: Any = None
        self.last_export_directory: Path | None = None
        self.current_analysis_run_id = ""
        self.current_progress_recording_dir = ""
        self.current_progress_recording_mode = PROGRESS_MODE_OFF
        self.undo_stack: list[dict[str, Any]] = []
        self.review_toolbar_buttons: dict[str, dict[str, QPushButton]] = {}
        self.node_inline_action_widgets: list[QWidget] = []
        self.edge_inline_action_widgets: list[QWidget] = []
        self.current_quality_report: GraphQualityReport | None = None
        WORKBENCH_STORAGE.mkdir(parents=True, exist_ok=True)
        GRAPH_REVIEW_VIEW_STORAGE.mkdir(parents=True, exist_ok=True)
        self._build_ui()
        self._build_menu()
        apply_workbench_theme(self)
        self._sync_model_controls()
        self._set_workspace_state("等待导入", "idle")
        self._update_command_availability()
        self._update_summary_badges()

    def _build_menu(self) -> None:
        self.open_action = QAction("导入文件", self)
        self.open_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.choose_file)
        self.open_review_action = QAction("打开审查包", self)
        self.open_review_action.setToolTip("打开 review_document_v2.xlsx 或迁移旧 draft 工作簿")
        self.open_review_action.triggered.connect(self.choose_review_document)
        self.merge_review_action = QAction("合并 Excel 审查修订", self)
        self.merge_review_action.setToolTip("按审查包内基线与当前会话执行字段级三方合并")
        self.merge_review_action.triggered.connect(self.merge_review_document)
        self.export_action = QAction("导出审查结果", self)
        self.export_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.export_action.triggered.connect(self.export_reviewed)
        self.output_action = QAction("打开输出目录", self)
        self.output_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.output_action.triggered.connect(self._open_output_directory)

        self.file_menu = self.menuBar().addMenu("文件")
        self.file_menu.addAction(self.open_action)
        self.file_menu.addAction(self.open_review_action)
        self.file_menu.addAction(self.merge_review_action)
        self.file_menu.addAction(self.export_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.output_action)

        self.help_menu = self.menuBar().addMenu("帮助")
        self.help_menu.setObjectName("helpMenu")
        self.developer_info_action = QAction(
            f"关于 {self.app_info.application.project_code} / 开发者信息",
            self,
        )
        self.developer_info_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        )
        self.developer_info_action.setShortcut(QKeySequence("F1"))
        self.developer_info_action.setObjectName("developerInfoAction")
        self.developer_info_action.triggered.connect(self._show_developer_info)
        self.help_menu.addAction(self.developer_info_action)

    def _show_developer_info(self) -> None:
        dialog = QMessageBox(self)
        dialog.setObjectName("developerInfoDialog")
        dialog.setWindowTitle(f"{self.app_info.application.project_code} · 开发者信息")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText(self.app_info.application.display_name)
        details = []
        if self.app_info.application.description:
            details.extend([self.app_info.application.description, ""])
        details.extend(
            [
                f"项目标识：{self.app_info.application.project_code}",
                f"开发者：{self.app_info.developer.name}",
                f"开发者联系方式：{self.app_info.developer.contact}",
            ]
        )
        dialog.setInformativeText("\n".join(details))
        dialog.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.exec()

    def _badge(self, text: str, tone: str = "idle") -> QLabel:
        badge = QLabel(text)
        badge.setProperty("role", "badge")
        badge.setProperty("tone", tone)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return badge

    @staticmethod
    def _set_badge(badge: QLabel, text: str, tone: str) -> None:
        badge.setText(text)
        badge.setProperty("tone", tone)
        refresh_dynamic_style(badge)

    def _build_empty_state(
        self,
        standard_pixmap: QStyle.StandardPixmap,
        title: str,
        description: str,
    ) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.addStretch(1)
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(self.style().standardIcon(standard_pixmap).pixmap(44, 44))
        title_label = QLabel(title)
        title_label.setProperty("role", "sectionTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description_label = QLabel(description)
        description_label.setProperty("role", "muted")
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description_label.setWordWrap(True)
        layout.addWidget(icon_label)
        layout.addSpacing(6)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addStretch(1)
        return widget

    def _open_output_directory(self) -> None:
        output_directory = self.last_export_directory or WORKBENCH_STORAGE
        output_directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_directory)))

    def _set_model_details_visible(self, visible: bool) -> None:
        self.model_details_container.setVisible(visible)
        self.model_details_button.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self.api_key_visibility_button.setText("隐藏" if visible else "显示")

    def _sync_model_controls(self, *_args: object) -> None:
        if not hasattr(self, "model_mode_input"):
            return
        remote_mode = self.model_mode_input.currentText() == "真实大模型"
        for widget in [
            self.api_key_input,
            self.api_key_visibility_button,
            self.model_input,
            self.base_url_input,
        ]:
            widget.setEnabled(remote_mode)
        if not remote_mode and self.api_key_visibility_button.isChecked():
            self.api_key_visibility_button.setChecked(False)
        mode_text = "真实大模型" if remote_mode else "本地模拟"
        ocr_text = "OCR 开启" if self.ocr_enabled_input.isChecked() else "OCR 关闭"
        staged_text = "分阶段抽取开启" if self.staged_pipeline_input.isChecked() else "分阶段抽取关闭"
        order_text = "页序检查开启" if self.page_order_check_input.isChecked() else "页序检查关闭"
        recording_labels = {
            PROGRESS_MODE_FULL: "完整过程记录",
            PROGRESS_MODE_SUMMARY: "摘要过程记录",
            PROGRESS_MODE_OFF: "过程记录关闭",
        }
        recording_text = recording_labels.get(
            str(self.progress_recording_input.currentData() or PROGRESS_MODE_OFF),
            "过程记录关闭",
        )
        self.model_summary_label.setText(
            f"{mode_text} · {ocr_text} · {staged_text} · {order_text} · {recording_text}"
        )
        self.model_details_button.setText(f"高级设置（{mode_text}）")

    def _set_workspace_state(self, text: str, tone: str) -> None:
        if hasattr(self, "workspace_status_badge"):
            self._set_badge(self.workspace_status_badge, text, tone)

    def _update_command_availability(self) -> None:
        has_source = self.source_path is not None
        has_drafts = bool(self.drafts)
        analysis_running = bool(self.analysis_thread and self.analysis_thread.isRunning())
        import_enabled = not analysis_running
        analyze_enabled = has_source and not analysis_running
        export_enabled = has_drafts and not analysis_running

        for button_name in ["import_button", "source_choose_button"]:
            button = getattr(self, button_name, None)
            if button is not None:
                button.setEnabled(import_enabled)
                button.setToolTip(
                    "分析进行中，暂不能更换教材。"
                    if analysis_running
                    else "选择 PDF、PNG 或 JPG 教材文件（Ctrl+O）"
                )
        self.analyze_button.setEnabled(analyze_enabled)
        self.analyze_button.setToolTip(
            "正在分析教材，请等待当前任务结束。"
            if analysis_running
            else (
                "开始分析当前教材范围（Ctrl+Enter）"
                if has_source
                else "请先导入教材文件。"
            )
        )
        self.export_button.setEnabled(export_enabled)
        self.export_button.setToolTip(
            "分析进行中，完成后可导出。"
            if analysis_running
            else (
                "导出工作底稿、正式图谱和质量报告（Ctrl+Shift+S）"
                if has_drafts
                else "请先生成候选图谱。"
            )
        )
        if hasattr(self, "open_action"):
            self.open_action.setEnabled(import_enabled)
            self.open_review_action.setEnabled(import_enabled)
            self.merge_review_action.setEnabled(import_enabled and self.review_document is not None)
            self.export_action.setEnabled(export_enabled)

    def _update_summary_badges(self) -> None:
        node_count = len(self.workbook.nodes) if self.workbook else len(self.drafts)
        edge_count = len(self.workbook.edges) if self.workbook else 0
        pending_nodes = self._pending_node_count() if self.drafts else 0
        pending_edges = self._pending_relation_count() if self.workbook else 0
        pending_count = pending_nodes + pending_edges
        revision_nodes = self.review_session.revision_node_count() if self.drafts else 0
        revision_edges = self.review_session.revision_relation_count(self.workbook)
        revision_count = revision_nodes + revision_edges
        if hasattr(self, "graph_node_badge"):
            self._set_badge(self.graph_node_badge, f"节点 {node_count}", "primary")
            self._set_badge(self.graph_edge_badge, f"关系 {edge_count}", "primary")
            self._set_badge(
                self.graph_pending_badge,
                f"待审 {pending_count}",
                "warning" if pending_count else "success",
            )
            self._set_badge(
                self.graph_revision_badge,
                f"待修 {revision_count}",
                "danger" if revision_count else "success",
            )
        if hasattr(self, "tabs"):
            evidence_count = sum(len(draft.evidence_anchors) for draft in self.drafts)
            quality_count = (
                len(self.current_quality_report.issues)
                if self.current_quality_report is not None
                else 0
            )
            self.tabs.setTabText(0, f"节点审查 · {len(self.drafts)}")
            self.tabs.setTabText(1, f"关系审查 · {edge_count}")
            self.tabs.setTabText(2, f"质量检查 · {quality_count}")
            self.tabs.setTabText(3, f"证据锚点 · {evidence_count}")
        if hasattr(self, "review_empty_hint"):
            self.review_empty_hint.setVisible(not bool(self.drafts))

    def _build_ui(self) -> None:
        workspace = QWidget()
        workspace.setObjectName("workspaceRoot")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(12, 10, 12, 10)
        workspace_layout.setSpacing(10)
        workspace_layout.addWidget(self._build_command_bar())

        self.root_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.control_panel = self._build_control_panel()
        self.root_splitter.addWidget(self.control_panel)
        self.content_splitter = QSplitter(Qt.Orientation.Vertical)
        self.top_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.preview_panel = self._build_preview_panel()
        self.graph_panel = self._build_graph_panel()
        self.preview_panel.setMinimumWidth(360)
        self.graph_panel.setMinimumWidth(360)
        self.top_splitter.addWidget(self.preview_panel)
        self.top_splitter.addWidget(self.graph_panel)
        self.top_splitter.setSizes([620, 640])
        self.content_splitter.addWidget(self.top_splitter)
        self.review_panel = self._build_review_tabs()
        self.content_splitter.addWidget(self.review_panel)
        self.content_splitter.setSizes([500, 360])
        self.root_splitter.addWidget(self.content_splitter)
        self.root_splitter.setSizes([360, 1090])
        for splitter in [self.root_splitter, self.content_splitter, self.top_splitter]:
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(7)
            for index in range(splitter.count()):
                splitter.setCollapsible(index, False)
        workspace_layout.addWidget(self.root_splitter, 1)
        self.setCentralWidget(workspace)
        self.statusBar().showMessage("请先导入 PDF、PNG 或 JPG 教材材料。")

    def _build_command_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("commandBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 11, 14, 11)
        layout.setSpacing(10)

        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        self.app_title_label = QLabel(self.app_info.application.display_name)
        self.app_title_label.setObjectName("appTitle")
        self.workspace_status_badge = self._badge("等待导入", "idle")
        title_row.addWidget(self.app_title_label)
        title_row.addWidget(self.workspace_status_badge)
        title_row.addStretch(1)
        subtitle = QLabel(
            self.app_info.application.description or "教材知识点关系抽取、审查与导出工具"
        )
        subtitle.setProperty("role", "muted")
        brand_layout.addLayout(title_row)
        brand_layout.addWidget(subtitle)
        layout.addWidget(brand, 1)

        layout.addWidget(self._create_layout_controls())
        self.layout_menu_button = QToolButton()
        self.layout_menu_button.setText("布局")
        self.layout_menu_button.setToolTip("选择工作区布局，或重置图谱节点位置")
        self.layout_menu_button.setProperty("role", "subtle")
        self.layout_menu_button.setMenu(self._build_layout_menu())
        self.layout_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(self.layout_menu_button)

        self.import_button = QPushButton("导入教材")
        self.import_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.import_button.setProperty("role", "subtle")
        self.import_button.clicked.connect(self.choose_file)
        self.analyze_button = QPushButton("开始分析")
        self.analyze_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.analyze_button.setProperty("role", "primary")
        self.analyze_button.setShortcut(QKeySequence("Ctrl+Return"))
        self.analyze_button.clicked.connect(self.generate_candidates)
        self.export_button = QPushButton("导出结果")
        self.export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_button.setProperty("role", "success")
        self.export_button.clicked.connect(self.export_reviewed)
        self.open_export_button = QPushButton("输出目录")
        self.open_export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_export_button.setProperty("role", "subtle")
        self.open_export_button.setToolTip("打开工作台默认输出目录")
        self.open_export_button.clicked.connect(self._open_output_directory)
        for button in [
            self.import_button,
            self.analyze_button,
            self.export_button,
            self.open_export_button,
        ]:
            layout.addWidget(button)
        return bar

    def _build_control_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("controlPanel")
        panel.setMinimumWidth(330)
        panel.setMaximumWidth(440)
        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setObjectName("controlScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(10)

        source_group = QGroupBox("01  教材来源")
        source_group.setProperty("role", "stepCard")
        source_layout = QVBoxLayout(source_group)
        source_layout.setSpacing(7)
        self.file_label = QLabel("尚未导入教材")
        self.file_label.setProperty("role", "fileName")
        self.file_label.setWordWrap(True)
        self.file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        source_hint = QLabel("支持 PDF、PNG、JPG；PDF 可选择分析页码范围。")
        source_hint.setProperty("role", "muted")
        source_hint.setWordWrap(True)
        self.source_choose_button = QPushButton("选择 PDF 或图片")
        self.source_choose_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        )
        self.source_choose_button.setProperty("role", "subtle")
        self.source_choose_button.clicked.connect(self.choose_file)
        source_layout.addWidget(self.file_label)
        source_layout.addWidget(source_hint)
        source_layout.addWidget(self.source_choose_button)

        meta_group = QGroupBox("02  教材信息")
        meta_group.setProperty("role", "stepCard")
        meta_layout = QFormLayout(meta_group)
        meta_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        meta_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        meta_layout.setHorizontalSpacing(8)
        meta_layout.setVerticalSpacing(7)
        self.subject_input = QLineEdit()
        self.grade_input = QLineEdit()
        self.term_input = QLineEdit()
        self.source_id_input = QLineEdit("LOCAL_TEXTBOOK")
        self.subject_input.setPlaceholderText("必填，例如：art / math")
        self.grade_input.setPlaceholderText("必填，例如：g7")
        self.term_input.setPlaceholderText("必填，例如：term1")
        self.source_id_input.setToolTip("用于来源追踪，建议同一教材保持稳定且唯一。")
        meta_layout.addRow("学科", self.subject_input)
        meta_layout.addRow("年级", self.grade_input)
        meta_layout.addRow("册次", self.term_input)
        meta_layout.addRow("来源 ID", self.source_id_input)

        scope_group = QGroupBox("03  分析范围")
        scope_group.setProperty("role", "stepCard")
        scope_layout = QFormLayout(scope_group)
        scope_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scope_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        scope_layout.setHorizontalSpacing(8)
        scope_layout.setVerticalSpacing(7)
        self.start_page_input = QSpinBox()
        self.start_page_input.setRange(1, 9999)
        self.start_page_input.setValue(1)
        self.end_page_input = QSpinBox()
        self.end_page_input.setRange(1, 9999)
        self.end_page_input.setValue(8)
        self.scope_label_input = QLineEdit("chapter_scope")
        self.start_page_input.valueChanged.connect(self._sync_analysis_range_hint)
        self.end_page_input.valueChanged.connect(self._sync_analysis_range_hint)
        scope_layout.addRow("起始 PDF 页", self.start_page_input)
        scope_layout.addRow("结束 PDF 页", self.end_page_input)
        scope_layout.addRow("范围名称", self.scope_label_input)

        model_group = QGroupBox("04  模型与识别")
        model_group.setProperty("role", "stepCard")
        model_group_layout = QVBoxLayout(model_group)
        model_group_layout.setContentsMargins(8, 6, 8, 8)
        model_group_layout.setSpacing(6)
        self.model_details_button = QToolButton()
        self.model_details_button.setObjectName("advancedToggle")
        self.model_details_button.setCheckable(True)
        self.model_details_button.setChecked(False)
        self.model_details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.model_details_button.setArrowType(Qt.ArrowType.RightArrow)
        self.model_details_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.model_details_button.toggled.connect(self._set_model_details_visible)
        self.model_summary_label = QLabel("本地模拟 · OCR 开启 · 分阶段抽取开启")
        self.model_summary_label.setProperty("role", "muted")
        self.model_summary_label.setWordWrap(True)

        self.model_details_container = QWidget()
        model_layout = QFormLayout(self.model_details_container)
        model_layout.setContentsMargins(0, 4, 0, 0)
        model_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        model_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        model_layout.setHorizontalSpacing(8)
        model_layout.setVerticalSpacing(7)
        self.model_mode_input = QComboBox()
        self.model_mode_input.addItems(["本地模拟", "真实大模型"])
        self.model_mode_input.setToolTip("本地模拟用于离线流程验证；真实大模型会读取界面或 .env 配置。")
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("优先使用界面输入，留空读取 .env")
        self.api_key_visibility_button = QToolButton()
        self.api_key_visibility_button.setText("显示")
        self.api_key_visibility_button.setCheckable(True)
        self.api_key_visibility_button.setProperty("role", "subtle")
        self.api_key_visibility_button.setToolTip("临时显示或隐藏 API Key")
        self.api_key_visibility_button.toggled.connect(self._toggle_api_key_visibility)
        api_key_widget = QWidget()
        api_key_layout = QHBoxLayout(api_key_widget)
        api_key_layout.setContentsMargins(0, 0, 0, 0)
        api_key_layout.setSpacing(5)
        api_key_layout.addWidget(self.api_key_input, 1)
        api_key_layout.addWidget(self.api_key_visibility_button)
        self.model_input = QLineEdit(
            self._model_config_value("TEXTBOOK_BUILDER_LLM_MODEL", "qwen3.6-plus")
        )
        self.base_url_input = QLineEdit(
            self._model_config_value(
                "TEXTBOOK_BUILDER_LLM_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
        )
        self.base_url_input.setToolTip(self.base_url_input.text())
        self.base_url_input.textChanged.connect(self.base_url_input.setToolTip)
        model_layout.addRow("模式", self.model_mode_input)
        model_layout.addRow("API Key", api_key_widget)
        model_layout.addRow("模型", self.model_input)
        model_layout.addRow("Base URL", self.base_url_input)
        self.ocr_enabled_input = QCheckBox("扫描页启用本地 OCR（原生文本优先）")
        self.ocr_enabled_input.setChecked(True)
        self.staged_pipeline_input = QCheckBox("启用节点/关系分阶段抽取")
        self.staged_pipeline_input.setChecked(True)
        self.page_order_check_input = QCheckBox("检查当前页面顺序（不自动重排）")
        self.page_order_check_input.setChecked(False)
        self.page_order_check_input.setToolTip(
            "正常按书本顺序扫描时可保持关闭；开启后仅标记可疑相邻页，必要时再由模型复核。"
        )
        self.progress_recording_input = QComboBox()
        self.progress_recording_input.addItem("完整记录（开发期）", PROGRESS_MODE_FULL)
        self.progress_recording_input.addItem("摘要记录", PROGRESS_MODE_SUMMARY)
        self.progress_recording_input.addItem("关闭记录", PROGRESS_MODE_OFF)
        configured_recording_mode = normalize_progress_mode(
            self._model_config_value(
                "TEXTBOOK_BUILDER_PROGRESS_RECORDING_MODE",
                PROGRESS_MODE_FULL,
            )
        )
        self.progress_recording_input.setCurrentIndex(
            max(self.progress_recording_input.findData(configured_recording_mode), 0)
        )
        self.progress_recording_input.setToolTip(
            "完整记录会保存脱敏后的模型请求/响应与各阶段中间产物；"
            "关闭后不创建本轮记录目录。"
        )
        model_layout.addRow("页面文本", self.ocr_enabled_input)
        model_layout.addRow("抽取流程", self.staged_pipeline_input)
        model_layout.addRow("页序检查", self.page_order_check_input)
        model_layout.addRow("过程记录", self.progress_recording_input)
        self.model_mode_input.currentTextChanged.connect(self._sync_model_controls)
        self.ocr_enabled_input.toggled.connect(self._sync_model_controls)
        self.staged_pipeline_input.toggled.connect(self._sync_model_controls)
        self.page_order_check_input.toggled.connect(self._sync_model_controls)
        self.progress_recording_input.currentIndexChanged.connect(self._sync_model_controls)
        self.model_details_container.setVisible(False)
        model_group_layout.addWidget(self.model_details_button)
        model_group_layout.addWidget(self.model_summary_label)
        model_group_layout.addWidget(self.model_details_container)

        self.info_box = QTextEdit()
        self.info_box.setObjectName("runLog")
        self.info_box.setReadOnly(True)
        self.info_box.setMinimumHeight(150)
        self.info_box.setMaximumHeight(230)
        env_hint = "已检测到 .env 文件。" if self.dotenv_values else "未检测到 .env 文件。"
        self.info_box.setText(
            "导入教材文件后，可以选择 PDF 页码范围并生成候选图谱。\n"
            f"{env_hint} 真实大模型模式会优先使用界面输入，其次使用 .env 或系统环境变量。"
        )
        run_group = QGroupBox("运行记录")
        run_group.setProperty("role", "stepCard")
        run_layout = QVBoxLayout(run_group)
        run_layout.addWidget(self.info_box)

        layout.addWidget(source_group)
        layout.addWidget(meta_group)
        layout.addWidget(scope_group)
        layout.addWidget(model_group)
        layout.addWidget(run_group)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "panelCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("教材原文预览")
        title.setProperty("role", "sectionTitle")
        header_layout.addWidget(title)
        self.preview_status_badge = self._badge("未导入", "idle")
        header_layout.addWidget(self.preview_status_badge)
        header_layout.addStretch(1)
        self.preview_stack = QTabWidget()

        self.pdf_view = ZoomablePdfView()
        self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
        self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.pdf_view.setPageSpacing(14)

        pdf_panel = QWidget()
        pdf_layout = QGridLayout(pdf_panel)
        pdf_layout.setContentsMargins(0, 0, 0, 0)
        pdf_layout.addWidget(self.pdf_view, 0, 0)

        self.pdf_page_badge = QLabel("PDF 页码：- / -")
        self.pdf_page_badge.setProperty("role", "badge")
        self.pdf_page_badge.setProperty("tone", "primary")
        pdf_layout.addWidget(
            self.pdf_page_badge,
            0,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        self.pdf_prev_button = QPushButton("上一页")
        self.pdf_prev_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.pdf_prev_button.setProperty("role", "subtle")
        self.pdf_prev_button.setEnabled(False)
        self.pdf_prev_button.clicked.connect(lambda: self._step_pdf_page(-1))
        self.pdf_next_button = QPushButton("下一页")
        self.pdf_next_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.pdf_next_button.setProperty("role", "subtle")
        self.pdf_next_button.setEnabled(False)
        self.pdf_next_button.clicked.connect(lambda: self._step_pdf_page(1))
        self.pdf_jump_input = QSpinBox()
        self.pdf_jump_input.setRange(1, 1)
        self.pdf_jump_input.setValue(1)
        self.pdf_jump_input.setEnabled(False)
        self.pdf_jump_button = QPushButton("跳转")
        self.pdf_jump_button.setProperty("role", "subtle")
        self.pdf_jump_button.setEnabled(False)
        self.pdf_jump_button.clicked.connect(self._jump_to_pdf_page)
        self.pdf_jump_label = QLabel("定位到页码")
        self.pdf_page_controls = QWidget()
        pdf_page_controls_layout = QHBoxLayout(self.pdf_page_controls)
        pdf_page_controls_layout.setContentsMargins(0, 0, 0, 0)
        pdf_page_controls_layout.setSpacing(5)
        pdf_page_controls_layout.addWidget(self.pdf_prev_button)
        pdf_page_controls_layout.addWidget(self.pdf_next_button)
        pdf_page_controls_layout.addWidget(self.pdf_jump_label)
        pdf_page_controls_layout.addWidget(self.pdf_jump_input)
        pdf_page_controls_layout.addWidget(self.pdf_jump_button)

        self.pdf_zoom_out_button = QToolButton()
        self.pdf_zoom_out_button.setText("−")
        self.pdf_zoom_out_button.setProperty("role", "subtle")
        self.pdf_zoom_out_button.setToolTip("缩小 PDF（Ctrl+-）")
        self.pdf_zoom_out_button.setAutoRepeat(True)
        self.pdf_zoom_out_button.setFixedSize(38, 40)
        self.pdf_zoom_out_button.clicked.connect(self.pdf_view.zoom_out)
        self.pdf_zoom_label = QLabel("适合宽度")
        self.pdf_zoom_label.setProperty("role", "badge")
        self.pdf_zoom_label.setProperty("tone", "primary")
        self.pdf_zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pdf_zoom_label.setMinimumWidth(76)
        self.pdf_zoom_label.setFixedHeight(40)
        self.pdf_zoom_label.setToolTip("按住 Ctrl 并滚动鼠标滚轮可缩放 PDF")
        self.pdf_zoom_in_button = QToolButton()
        self.pdf_zoom_in_button.setText("+")
        self.pdf_zoom_in_button.setProperty("role", "subtle")
        self.pdf_zoom_in_button.setToolTip("放大 PDF（Ctrl++）")
        self.pdf_zoom_in_button.setAutoRepeat(True)
        self.pdf_zoom_in_button.setFixedSize(38, 40)
        self.pdf_zoom_in_button.clicked.connect(self.pdf_view.zoom_in)
        self.pdf_fit_width_button = QPushButton("适合宽度")
        self.pdf_fit_width_button.setProperty("role", "subtle")
        self.pdf_fit_width_button.setToolTip("恢复适合宽度（Ctrl+0）")
        self.pdf_fit_width_button.clicked.connect(self.pdf_view.reset_to_fit_width)
        self.pdf_zoom_controls = QWidget()
        pdf_zoom_controls_layout = QHBoxLayout(self.pdf_zoom_controls)
        pdf_zoom_controls_layout.setContentsMargins(0, 0, 0, 0)
        pdf_zoom_controls_layout.setSpacing(6)
        pdf_zoom_controls_layout.addWidget(QLabel("缩放"))
        pdf_zoom_controls_layout.addWidget(self.pdf_zoom_out_button)
        pdf_zoom_controls_layout.addWidget(self.pdf_zoom_label)
        pdf_zoom_controls_layout.addWidget(self.pdf_zoom_in_button)
        pdf_zoom_controls_layout.addWidget(self.pdf_fit_width_button)
        self.pdf_control_bar = ResponsivePdfControlBar(
            self.pdf_page_controls,
            self.pdf_zoom_controls,
        )

        self.analysis_range_badge = QLabel("分析范围：-")
        self.analysis_range_badge.setProperty("role", "badge")
        self.analysis_range_badge.setProperty("tone", "warning")
        self.range_set_start_button = QPushButton("设为起始")
        self.range_set_start_button.setToolTip("把当前 PDF 页设置为分析起始页")
        self.range_set_start_button.clicked.connect(self._set_current_page_as_start)
        self.range_set_end_button = QPushButton("设为结束")
        self.range_set_end_button.setToolTip("把当前 PDF 页设置为分析结束页")
        self.range_set_end_button.clicked.connect(self._set_current_page_as_end)
        self.range_jump_start_button = QPushButton("查看起始")
        self.range_jump_start_button.setToolTip("跳转到当前配置的分析起始页")
        self.range_jump_start_button.clicked.connect(lambda: self._jump_to_configured_page("start"))
        self.range_jump_end_button = QPushButton("查看结束")
        self.range_jump_end_button.setToolTip("跳转到当前配置的分析结束页")
        self.range_jump_end_button.clicked.connect(lambda: self._jump_to_configured_page("end"))
        for button in [
            self.range_set_start_button,
            self.range_set_end_button,
            self.range_jump_start_button,
            self.range_jump_end_button,
        ]:
            button.setProperty("role", "subtle")
            button.setEnabled(False)

        range_widget = QWidget()
        range_layout = QGridLayout(range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setHorizontalSpacing(6)
        range_layout.setVerticalSpacing(6)
        range_layout.addWidget(self.analysis_range_badge, 0, 0, 1, 4)
        range_layout.addWidget(self.range_set_start_button, 1, 0)
        range_layout.addWidget(self.range_set_end_button, 1, 1)
        range_layout.addWidget(self.range_jump_start_button, 1, 2)
        range_layout.addWidget(self.range_jump_end_button, 1, 3)

        pdf_panel_wrapper = QWidget()
        pdf_panel_wrapper_layout = QVBoxLayout(pdf_panel_wrapper)
        pdf_panel_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        pdf_panel_wrapper_layout.addWidget(pdf_panel, 1)
        pdf_panel_wrapper_layout.addWidget(self.pdf_control_bar)
        pdf_panel_wrapper_layout.addWidget(range_widget)

        self.pdf_zoom_in_action = QAction("放大 PDF", pdf_panel_wrapper)
        self.pdf_zoom_in_action.setShortcuts(
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")]
        )
        self.pdf_zoom_in_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.pdf_zoom_in_action.triggered.connect(self.pdf_view.zoom_in)
        self.pdf_zoom_out_action = QAction("缩小 PDF", pdf_panel_wrapper)
        self.pdf_zoom_out_action.setShortcut(QKeySequence("Ctrl+-"))
        self.pdf_zoom_out_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.pdf_zoom_out_action.triggered.connect(self.pdf_view.zoom_out)
        self.pdf_fit_width_action = QAction("PDF 适合宽度", pdf_panel_wrapper)
        self.pdf_fit_width_action.setShortcut(QKeySequence("Ctrl+0"))
        self.pdf_fit_width_action.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.pdf_fit_width_action.triggered.connect(self.pdf_view.reset_to_fit_width)
        pdf_panel_wrapper.addActions(
            [
                self.pdf_zoom_in_action,
                self.pdf_zoom_out_action,
                self.pdf_fit_width_action,
            ]
        )
        self.pdf_view.zoom_state_changed.connect(self._sync_pdf_zoom_controls)
        self._sync_pdf_zoom_controls()

        self.image_label = QLabel("导入图片后在这里预览")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setProperty("role", "muted")
        self.image_label.setScaledContents(False)
        self.source_text_box = QTextEdit()
        self.source_text_box.setReadOnly(True)
        self.preview_stack.addTab(pdf_panel_wrapper, "PDF")
        self.preview_stack.addTab(self.image_label, "图片")
        self.preview_stack.addTab(self.source_text_box, "来源记录")
        self.preview_content_stack = QStackedWidget()
        self.preview_empty_state = self._build_empty_state(
            QStyle.StandardPixmap.SP_FileDialogContentsView,
            "尚未导入教材",
            "从顶部或左侧选择 PDF、PNG、JPG 文件后，可在这里预览原文并设置分析范围。",
        )
        self.preview_content_stack.addWidget(self.preview_empty_state)
        self.preview_content_stack.addWidget(self.preview_stack)
        self.preview_content_stack.setCurrentIndex(0)
        layout.addWidget(header)
        layout.addWidget(self.preview_content_stack, 1)
        return panel

    def _build_graph_panel(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "panelCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("知识图谱候选")
        title.setProperty("role", "sectionTitle")
        header_layout.addWidget(title)
        self.graph_node_badge = self._badge("节点 0", "primary")
        self.graph_edge_badge = self._badge("关系 0", "primary")
        self.graph_pending_badge = self._badge("待审 0", "warning")
        self.graph_revision_badge = self._badge("待修 0", "success")
        header_layout.addWidget(self.graph_node_badge)
        header_layout.addWidget(self.graph_edge_badge)
        header_layout.addWidget(self.graph_pending_badge)
        header_layout.addWidget(self.graph_revision_badge)
        header_layout.addStretch(1)
        self.graph_scene = QGraphicsScene()
        self.graph_scene.selectionChanged.connect(self._on_qt_graph_selection_changed)
        self.graph_view = QGraphicsView(self.graph_scene)
        self.graph_view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        layout.addWidget(header)
        mode_bar = QWidget()
        mode_layout = QHBoxLayout(mode_bar)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        mode_label = QLabel("阅读方式")
        mode_label.setProperty("role", "muted")
        mode_layout.addWidget(mode_label)
        self.graph_view_mode_group = QButtonGroup(self)
        self.graph_view_mode_group.setExclusive(True)
        self.graph_view_mode_buttons: dict[str, QToolButton] = {}
        for mode, text, tooltip in [
            (VIEW_MODE_RELATIONS, "关系探索", "一个知识点一张卡片，从关联关系开始追踪"),
            (VIEW_MODE_TEXTBOOK, "教材定位", "按真实教材归属和章节结构查看引用位置"),
        ]:
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tooltip)
            button.setCheckable(True)
            button.setProperty("role", "segment")
            button.clicked.connect(
                lambda _checked=False, target=mode: self._set_graph_view_mode(target)
            )
            self.graph_view_mode_group.addButton(button)
            self.graph_view_mode_buttons[mode] = button
            mode_layout.addWidget(button)
        self.graph_view_mode_buttons[VIEW_MODE_RELATIONS].setChecked(True)
        hide_button = QToolButton()
        hide_button.setText("隐藏此卡片")
        hide_button.setToolTip("仅隐藏当前视图实例，不归档知识点、不删除教材归属")
        hide_button.clicked.connect(self._hide_selected_graph_card)
        mode_layout.addWidget(hide_button)
        restore_hidden_button = QToolButton()
        restore_hidden_button.setText("显示已隐藏")
        restore_hidden_button.setToolTip("恢复当前阅读模式下由教师隐藏的卡片")
        restore_hidden_button.clicked.connect(self._restore_hidden_graph_cards)
        mode_layout.addWidget(restore_hidden_button)
        membership_button = QToolButton()
        membership_button.setText("管理教材归属")
        membership_button.setToolTip("列出选中知识点的明确归属，选择后再执行变更")
        membership_button.clicked.connect(self._manage_selected_node_membership)
        mode_layout.addWidget(membership_button)
        mode_layout.addStretch(1)
        self.graph_view_mode_hint = QLabel("默认：从知识点追踪关联；教材归属可在详情中核对")
        self.graph_view_mode_hint.setProperty("role", "muted")
        mode_layout.addWidget(self.graph_view_mode_hint)
        layout.addWidget(mode_bar)
        self.graph_tabs = QTabWidget()
        self.g6_graph_view = QWebEngineView() if QWebEngineView is not None else None
        if self.g6_graph_view is not None:
            self.g6_graph_view.loadFinished.connect(self._on_g6_graph_load_finished)
            if QWebChannel is not None:
                self.g6_web_channel = QWebChannel(self.g6_graph_view.page())
                self.g6_graph_bridge = GraphReviewBridge(
                    self._save_g6_view_config,
                    self._on_g6_graph_selection,
                    self._request_graph_relayout,
                )
                self.g6_web_channel.registerObject("graphReviewBridge", self.g6_graph_bridge)
                self.g6_graph_view.page().setWebChannel(self.g6_web_channel)
            self.graph_tabs.addTab(self.g6_graph_view, "G6 图谱")
        self.graph_tabs.addTab(self.graph_view, "兼容视图")
        self.graph_content_stack = QStackedWidget()
        self.graph_empty_state = self._build_empty_state(
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
            "尚未生成候选图谱",
            "导入教材并完成分析后，节点、关系和章节层级将在这里显示。",
        )
        self.graph_content_stack.addWidget(self.graph_empty_state)
        self.graph_content_stack.addWidget(self.graph_tabs)
        self.graph_content_stack.setCurrentIndex(0)
        layout.addWidget(self.graph_content_stack, 1)
        return panel

    def _on_qt_graph_selection_changed(self) -> None:
        selected = self.graph_scene.selectedItems()
        if not selected:
            return
        item = selected[0]
        kind = str(item.data(0) or "")
        object_id = str(item.data(1) or "")
        self._selected_graph_view_id = str(item.data(2) or "")
        if not object_id:
            return
        if kind == "node":
            row = next(
                (index for index, draft in enumerate(self.drafts) if draft.candidate_node_id == object_id),
                -1,
            )
            if row >= 0:
                self.tabs.setCurrentIndex(0)
                self.node_table.selectRow(row)
                self.statusBar().showMessage("已在节点审查表中定位所选知识点。")
        elif kind == "edge":
            row = next(
                (
                    index
                    for index in range(self.edge_table.rowCount())
                    if self._review_relation_id_from_edge_id(
                        str(self.edge_table.item(index, 1).data(Qt.ItemDataRole.UserRole) or "")
                        if self.edge_table.item(index, 1) is not None
                        else ""
                    )
                    == object_id
                ),
                -1,
            )
            if row >= 0:
                self.tabs.setCurrentIndex(1)
                self.edge_table.selectRow(row)
                self.statusBar().showMessage("已在关系审查表中定位所选关系及其证据。")

    def _set_graph_view_mode(self, mode: str) -> None:
        if mode not in {VIEW_MODE_RELATIONS, VIEW_MODE_TEXTBOOK} or mode == self.graph_view_mode:
            return
        self.graph_view_mode = mode
        self._selected_graph_view_id = ""
        self.graph_node_positions.clear()
        self.g6_view_config = self._load_g6_view_config(self._current_graph_id())
        self.g6_graph_loaded = False
        self.graph_view_mode_hint.setText(
            "一个知识点一张卡片；教材归属可在详情中核对"
            if mode == VIEW_MODE_RELATIONS
            else "按教材作用域显示引用；同一知识点可能出现多张卡片"
        )
        self._render_graph()
        self.statusBar().showMessage(
            "已切换为关系探索视图。" if mode == VIEW_MODE_RELATIONS else "已切换为教材定位视图。"
        )

    def _on_g6_graph_selection(self, item_type: str, business_id: str, view_id: str) -> None:
        self._selected_graph_view_id = str(view_id or "")
        if item_type == "node":
            row = next(
                (
                    index
                    for index, draft in enumerate(self.drafts)
                    if draft.candidate_node_id == business_id
                ),
                -1,
            )
            if row >= 0:
                self.node_table.selectRow(row)
        elif item_type == "edge":
            row = next(
                (
                    index
                    for index in range(self.edge_table.rowCount())
                    if self._review_relation_id_from_edge_id(
                        self._edge_id_for_table_row(index)
                    ) == business_id
                ),
                -1,
            )
            if row >= 0:
                self.edge_table.selectRow(row)

    def _request_graph_relayout(self) -> None:
        """Recompute only the current reading mode and preserve business data."""

        if self.review_document is None:
            self.statusBar().showMessage("当前没有可重新排版的审查文档。")
            return
        self.graph_node_positions.clear()
        self.g6_view_config = {
            **self.g6_view_config,
            "manual_positions": {},
            "layout_revision": GRAPH_LAYOUT_VERSION,
            "view_mode": self.graph_view_mode,
        }
        self._render_g6_graph()
        self.graph_scene.clear()
        self._render_projection_graph()
        self.statusBar().showMessage("当前阅读模式已重新排版；业务内容未变更。")

    def _hide_selected_graph_card(self) -> None:
        if self.review_document is None:
            QMessageBox.information(self, "暂无审查图谱", "请先生成或打开审查文档。")
            return
        node_id = self._selected_node_id()
        if not node_id:
            QMessageBox.information(self, "未选择知识点", "请先在图谱或节点表中选择知识点。")
            return
        projection = self.review_service.build_review_render_bundle(
            self.review_document,
            renderer="g6",
            view_mode=self.graph_view_mode,
        ).projection
        candidates = [item for item in projection.node_instances if item.node_id == node_id]
        selected = next(
            (item for item in candidates if item.view_id == self._selected_graph_view_id),
            None,
        )
        if selected is None and len(candidates) == 1:
            selected = candidates[0]
        if selected is None and candidates:
            scope_labels = {
                scope.scope_id: scope.label for scope in projection.scopes
            }
            options = [
                (
                    f"{scope_labels.get(item.scope_id, '待确认归属')}｜"
                    f"{item.membership_id or item.view_id}",
                    item,
                )
                for item in candidates
            ]
            label, accepted = QInputDialog.getItem(
                self,
                "选择要隐藏的教材引用卡片",
                "教材模式下同一知识点可能有多张卡片，请明确选择本处引用：",
                [label for label, _item in options],
                0,
                False,
            )
            if not accepted:
                return
            selected = next(item for option, item in options if option == label)
        if selected is None:
            QMessageBox.information(self, "卡片不可见", "当前模式中没有可隐藏的对应卡片。")
            return
        view_config = self._load_g6_view_config(self._current_graph_id())
        hidden = {str(item) for item in view_config.get("hidden_view_ids", []) if str(item)}
        hidden.add(selected.view_id)
        view_config["hidden_view_ids"] = sorted(hidden)
        view_config.setdefault("manual_positions", {})
        if not self._save_g6_view_config(view_config):
            return
        self._selected_graph_view_id = ""
        self._render_graph()
        self.statusBar().showMessage("已仅在当前阅读模式隐藏该卡片；业务数据未改变。")

    def _restore_hidden_graph_cards(self) -> None:
        view_config = self._load_g6_view_config(self._current_graph_id())
        hidden = list(view_config.get("hidden_view_ids", []))
        if not hidden:
            QMessageBox.information(self, "没有隐藏卡片", "当前阅读模式没有被隐藏的卡片。")
            return
        view_config["hidden_view_ids"] = []
        if not self._save_g6_view_config(view_config):
            return
        self._render_graph()
        self.statusBar().showMessage(f"已恢复当前阅读模式下的 {len(hidden)} 张隐藏卡片。")

    def _manage_selected_node_membership(self) -> None:
        node_id = self._selected_node_id()
        if not node_id:
            QMessageBox.information(self, "未选择知识点", "请先在图谱或节点表中选择知识点。")
            return
        if self.review_document is None:
            QMessageBox.information(self, "暂无审查文档", "当前没有可管理的教材归属记录。")
            return
        memberships = [
            relation
            for relation in self.review_document.relations
            if relation.relation_family == RELATION_FAMILY_MEMBERSHIP
            and relation.target_node_id == node_id
            and relation.lifecycle_state == "active"
        ]
        if not memberships:
            QMessageBox.information(
                self,
                "教材归属待确认",
                "该知识点当前没有有效教材归属。关系视图不会暗选小节；请通过教材结构审查流程补充归属。",
            )
            return
        scope_names = {
            node.node_id: node.display_name for node in self.review_document.nodes
        }
        options = [
            (
                f"{scope_names.get(item.source_node_id, item.source_node_id)}｜"
                f"{item.relation_id}｜{self._review_status_label(item.review_status)}",
                item,
            )
            for item in sorted(memberships, key=lambda value: (value.source_node_id, value.relation_id))
        ]
        label, accepted = QInputDialog.getItem(
            self,
            "管理教材归属",
            "请选择要移除的明确归属记录（取消不会产生任何变更）：",
            [label for label, _item in options],
            0,
            False,
        )
        if not accepted:
            return
        membership = next(item for option, item in options if option == label)
        selected_by_profiles = [
            profile.profile_id
            for profile in self.review_document.export_profiles
            if profile.primary_membership_by_node.get(node_id) == membership.relation_id
        ]
        consequences = [
            f"知识点：{scope_names.get(node_id, node_id)} ({node_id})",
            f"将移除归属：{scope_names.get(membership.source_node_id, membership.source_node_id)}",
            f"归属记录：{membership.relation_id}",
        ]
        if len(memberships) == 1:
            consequences.append("移除后该知识点将处于“归属待确认”状态。")
        if selected_by_profiles:
            consequences.append("该归属正被发布配置使用：" + "、".join(selected_by_profiles))
        consequences.append("记录将逻辑归档并保留历史；知识点实体和证据不会删除。")
        if QMessageBox.question(
            self,
            "确认移除教材归属",
            "\n".join(consequences),
        ) != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_snapshot(f"移除教材归属：{membership.relation_id}")
        session = ReviewDocumentSession(self.review_document)
        session.archive_relations(
            [membership.relation_id],
            expected_revision=self.review_document.metadata.revision,
            reason="desktop_archive_selected_membership",
        )
        self.review_document = session.snapshot()
        self.drafts = session.draft_rows()
        self._refresh_review_after_status_change(synchronize_document=False)
        self.statusBar().showMessage("已移除所选教材归属；知识点和证据仍保留。")

    def _create_layout_controls(self) -> QWidget:
        widget = QFrame()
        widget.setProperty("role", "segmented")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self.layout_mode_group = QButtonGroup(self)
        self.layout_mode_group.setExclusive(True)
        self.layout_mode_buttons: dict[str, QToolButton] = {}
        for preset, text, tooltip in [
            ("focus_preview", "教材", "专注教材原文预览"),
            ("focus_graph", "图谱", "专注知识图谱审查"),
            ("show_all", "全部", "显示全部工作台面板"),
        ]:
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tooltip)
            button.setCheckable(True)
            button.setProperty("role", "segment")
            button.clicked.connect(
                lambda _checked=False, target=preset: self._apply_layout_preset(target)
            )
            self.layout_mode_group.addButton(button)
            self.layout_mode_buttons[preset] = button
            layout.addWidget(button)
        self.layout_mode_buttons["show_all"].setChecked(True)
        return widget

    def _build_layout_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("恢复默认布局", lambda: self._apply_layout_preset("default"))
        menu.addAction("均衡布局", lambda: self._apply_layout_preset("balanced"))
        menu.addAction("专注教材预览", lambda: self._apply_layout_preset("focus_preview"))
        menu.addAction("专注图谱审查", lambda: self._apply_layout_preset("focus_graph"))
        menu.addAction("显示全部面板", lambda: self._apply_layout_preset("show_all"))
        menu.addSeparator()
        menu.addAction("重置图谱节点位置", self._reset_graph_node_positions)
        return menu

    def _reset_graph_node_positions(self) -> None:
        if self.graph_node_positions or self.g6_view_config.get("manual_positions"):
            self._push_undo_snapshot("重置图谱节点位置")
        self.graph_node_positions.clear()
        self.g6_view_config = {}
        graph_id = self._current_graph_id()
        if graph_id:
            self._g6_view_config_path(graph_id).unlink(missing_ok=True)
        self.g6_graph_loaded = False
        self._render_graph()

    def _apply_layout_preset(self, preset: str) -> None:
        if not hasattr(self, "root_splitter"):
            return
        if preset == "focus_preview":
            self._set_top_panels_visible(preview_visible=True, graph_visible=False)
            self.root_splitter.setSizes([320, 1180])
            self.content_splitter.setSizes([620, 220])
            self.top_splitter.setSizes([1, 0])
        elif preset == "focus_graph":
            self._set_top_panels_visible(preview_visible=False, graph_visible=True)
            self.root_splitter.setSizes([280, 1220])
            self.content_splitter.setSizes([620, 240])
            self.top_splitter.setSizes([0, 1])
        elif preset == "balanced":
            self._set_top_panels_visible(preview_visible=True, graph_visible=True)
            self.root_splitter.setSizes([340, 1100])
            self.content_splitter.setSizes([470, 390])
            self.top_splitter.setSizes([560, 560])
        elif preset == "show_all":
            self._set_top_panels_visible(preview_visible=True, graph_visible=True)
            self.root_splitter.setSizes([360, 1100])
            self.content_splitter.setSizes([520, 340])
            self.top_splitter.setSizes([600, 600])
        else:
            self._set_top_panels_visible(preview_visible=True, graph_visible=True)
            self.root_splitter.setSizes([360, 1090])
            self.content_splitter.setSizes([500, 360])
            self.top_splitter.setSizes([620, 640])
        selected_preset = preset if preset in {"focus_preview", "focus_graph"} else "show_all"
        if hasattr(self, "layout_mode_buttons") and selected_preset in self.layout_mode_buttons:
            self.layout_mode_buttons[selected_preset].setChecked(True)
        self.statusBar().showMessage("工作台布局已更新。")

    def _set_top_panels_visible(self, *, preview_visible: bool, graph_visible: bool) -> None:
        if hasattr(self, "preview_panel"):
            self.preview_panel.setVisible(preview_visible)
        if hasattr(self, "graph_panel"):
            self.graph_panel.setVisible(graph_visible)

    def _build_review_tabs(self) -> QWidget:
        panel = QFrame()
        panel.setProperty("role", "panelCard")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 9, 12, 12)
        panel_layout.setSpacing(7)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("审查与质量")
        title.setProperty("role", "sectionTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        hint = QLabel("选择表格行后显示对应操作")
        hint.setProperty("role", "muted")
        header_layout.addWidget(hint)
        panel_layout.addWidget(header)

        self.review_empty_hint = QLabel(
            "尚无候选内容。完成教材分析后，可在此逐项审查节点、关系、证据与质量问题。"
        )
        self.review_empty_hint.setProperty("role", "muted")
        self.review_empty_hint.setWordWrap(True)
        panel_layout.addWidget(self.review_empty_hint)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.review_toolbar_buttons = {"node": {}, "edge": {}}
        self.node_table = ReviewTableWidget()
        self.edge_table = ReviewTableWidget()
        self.anchor_table = ReviewTableWidget()
        self.quality_table = QTableWidget()
        for table in [self.node_table, self.edge_table, self.anchor_table, self.quality_table]:
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setAlternatingRowColors(True)
            table.setShowGrid(False)
            table.verticalHeader().setDefaultSectionSize(34)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.node_table.cellChanged.connect(self._node_cell_changed)
        self.edge_table.cellChanged.connect(self._edge_cell_changed)
        self.quality_table.cellDoubleClicked.connect(lambda _row, _column: self._locate_selected_quality_issue())
        self.tabs.addTab(
            self._review_table_tab(
                self.node_table,
                [
                    ("新增知识点", self._add_manual_node),
                    ("编辑节点", self._edit_selected_node),
                    ("复制节点", self._duplicate_selected_node),
                    ("定位到图谱", self._locate_selected_node_in_graph),
                    ("归档选中知识点", self._delete_selected_node),
                    ("清理已拒绝节点", self._delete_rejected_nodes),
                    ("通过当前章节结构", self._accept_current_chapter_structure),
                    ("通过全部目录结构", self._accept_all_catalog_structure),
                    ("通过待审查节点", self._accept_pending_nodes),
                    ("通过全部待审查", self._accept_all_pending),
                    ("撤销", self._undo_last_review_operation),
                ],
                "node",
            ),
            "节点审查",
        )
        self.tabs.addTab(
            self._review_table_tab(
                self.edge_table,
                [
                    ("新增关系", self._add_manual_relation),
                    ("编辑关系", self._edit_selected_relation),
                    ("定位到图谱", self._locate_selected_relation_in_graph),
                    ("删除选中关系", self._delete_selected_relation),
                    ("清理已拒绝关系", self._delete_rejected_relations),
                    ("通过待审查关系", self._accept_pending_relations),
                    ("通过全部待审查", self._accept_all_pending),
                    ("撤销", self._undo_last_review_operation),
                ],
                "edge",
            ),
            "关系审查",
        )
        self.tabs.addTab(self._quality_check_tab(), "质量检查")
        self.tabs.addTab(self.anchor_table, "证据锚点")
        self.node_table.itemSelectionChanged.connect(self._update_review_toolbar_visibility)
        self.edge_table.itemSelectionChanged.connect(self._update_review_toolbar_visibility)
        self.tabs.currentChanged.connect(lambda _index: self._update_review_toolbar_visibility())
        self._update_review_toolbar_visibility()
        panel_layout.addWidget(self.tabs, 1)
        return panel

    def _review_table_tab(self, table: QTableWidget, actions: list[tuple[str, Any]], context: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 6)
        toolbar_layout.setSpacing(6)
        toolbar_layout.addStretch(1)
        for text, callback in actions:
            button = QPushButton(text)
            if text.startswith("清理"):
                button.setToolTip("当前选中项为已拒绝时出现，用于批量清理同类已拒绝项目。")
                button.setProperty("role", "danger")
            elif text.startswith("通过"):
                button.setToolTip("仅处理仍为待审查的项目，不覆盖已通过、需修改或已拒绝。")
                button.setProperty("role", "success")
            elif text.startswith("删除"):
                button.setToolTip("删除当前表格中选中的项目，并同步更新图谱。")
                button.setProperty("role", "danger")
            elif text == "撤销":
                button.setToolTip("撤销上一步本地审查操作。")
                button.setProperty("role", "subtle")
            elif text.startswith("定位"):
                button.setToolTip("将 G6 图谱平移并高亮到当前选中对象。")
                button.setProperty("role", "subtle")
            else:
                button.setToolTip("基于当前审查底稿手动补充内容。")
                button.setProperty("role", "primary" if text.startswith("新增") else "subtle")
            button.clicked.connect(lambda _checked=False, action=callback: action())
            toolbar_layout.addWidget(button)
            self.review_toolbar_buttons.setdefault(context, {})[text] = button
        layout.addWidget(toolbar)
        layout.addWidget(table, 1)
        return widget

    def _quality_check_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 6)
        toolbar_layout.setSpacing(6)
        self.quality_summary_label = QLabel("尚未运行质量检查。")
        self.quality_summary_label.setProperty("role", "muted")
        toolbar_layout.addWidget(self.quality_summary_label, 1)
        run_button = QPushButton("重新检查")
        run_button.setToolTip("基于当前节点与关系审查结果刷新质量问题列表。")
        run_button.clicked.connect(self._run_quality_check)
        locate_button = QPushButton("定位问题")
        locate_button.setToolTip("定位当前选中的质量问题关联节点或关系。")
        locate_button.clicked.connect(self._locate_selected_quality_issue)
        for button in [run_button, locate_button]:
            button.setProperty("role", "subtle")
            toolbar_layout.addWidget(button)
        layout.addWidget(toolbar)
        layout.addWidget(self.quality_table, 1)
        return widget

    def _update_review_toolbar_visibility(self) -> None:
        if not getattr(self, "review_toolbar_buttons", None):
            return
        node_selected = (
            self.node_table.selectionModel().hasSelection()
            and 0 <= self.node_table.currentRow() < len(self.drafts)
        )
        node_status = self._selected_node_status() if node_selected else ""
        node_pending_exists = self._pending_node_count() > 0
        any_pending_exists = node_pending_exists or self._pending_relation_count() > 0
        structure_pending_exists = self.review_session.pending_structure_relation_count(
            self.workbook
        ) > 0
        node_rules = {
            "新增知识点": True,
            "编辑节点": False,
            "复制节点": False,
            "定位到图谱": False,
            "删除选中节点": False,
            "清理已拒绝节点": node_selected and node_status == "rejected",
            "通过当前章节结构": node_selected and structure_pending_exists,
            "通过全部目录结构": structure_pending_exists,
            "通过待审查节点": node_pending_exists,
            "通过全部待审查": any_pending_exists,
            "撤销": bool(self.undo_stack),
        }
        for text, button in self.review_toolbar_buttons.get("node", {}).items():
            button.setVisible(node_rules.get(text, True))
        for row, action_widget in enumerate(self.node_inline_action_widgets):
            action_widget.setVisible(node_selected and row == self.node_table.currentRow())

        edge_selected = (
            self.edge_table.selectionModel().hasSelection()
            and self.edge_table.currentRow() >= 0
            and bool(self._edge_id_for_table_row(self.edge_table.currentRow()))
        )
        edge_status = self._selected_relation_status() if edge_selected else ""
        edge_pending_exists = self._pending_relation_count() > 0
        edge_rules = {
            "新增关系": bool(self.drafts),
            "编辑关系": False,
            "定位到图谱": False,
            "删除选中关系": False,
            "清理已拒绝关系": edge_selected and edge_status == "rejected",
            "通过待审查关系": edge_pending_exists,
            "通过全部待审查": any_pending_exists,
            "撤销": bool(self.undo_stack),
        }
        for text, button in self.review_toolbar_buttons.get("edge", {}).items():
            button.setVisible(edge_rules.get(text, True))
        for row, action_widget in enumerate(self.edge_inline_action_widgets):
            action_widget.setVisible(edge_selected and row == self.edge_table.currentRow())

    def choose_file(self) -> None:
        if self.analysis_thread and self.analysis_thread.isRunning():
            QMessageBox.information(self, "请稍候", "当前正在执行分析任务，请先等待完成或取消。")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择教材文件",
            str(PROJECT_ROOT / "textbook"),
            "教材文件 (*.pdf *.png *.jpg *.jpeg)",
        )
        if not file_path:
            return
        self._load_source_path(Path(file_path))

    def choose_review_document(self) -> None:
        if self.analysis_thread and self.analysis_thread.isRunning():
            QMessageBox.information(self, "请稍候", "当前正在执行分析任务，请先等待完成或取消。")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开审查包",
            str(WORKBENCH_STORAGE),
            "审查工作簿 (*.xlsx)",
        )
        if not file_path:
            return
        try:
            document = ReviewDocumentReader().read_document(Path(file_path), migrate_v1=True)
        except (ReviewDocumentFormatError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "审查包读取失败", str(exc))
            return
        active_nodes = document.active_nodes()
        representative = active_nodes[0] if active_nodes else (document.nodes[0] if document.nodes else None)
        self.source_path = None
        self.source_format = "review_document_v2"
        self.review_document = document
        self.current_staged_outcome = None
        self.drafts = document_to_legacy_drafts(document)
        self.relation_status_overrides.clear()
        self.graph_node_positions.clear()
        self.g6_view_config = {}
        self.undo_stack.clear()
        self._review_document_undo_stack.clear()
        self.layout_plans = {}
        self.layout_recommendations = {}
        self.current_analysis_run_id = document.metadata.analysis_run_id
        self.current_progress_recording_dir = ""
        self.current_progress_recording_mode = PROGRESS_MODE_OFF
        self.current_quality_report = None
        self.current_record = SourceRecordDTO(
            source_id=document.metadata.source_identity or document.metadata.document_id,
            source_type="review_document",
            source_path=representative.source_path if representative else str(file_path),
            subject=representative.subject if representative else "",
            grade=representative.grade if representative else "",
            term=representative.term if representative else "",
            raw_structure={
                "document_id": document.metadata.document_id,
                "schema_version": document.metadata.schema_version,
                "revision": document.metadata.revision,
                "package_path": str(file_path),
            },
            source_format=representative.source_format if representative else "xlsx",
            source_document_type=(
                representative.source_document_type if representative else "review_document"
            ),
            education_stage=representative.education_stage if representative else "",
            grade_band=representative.grade_band if representative else "",
            subject_tags=list(representative.subject_tags) if representative else [],
        )
        self.file_label.setText(Path(file_path).name)
        self.file_label.setToolTip(str(file_path))
        self.pdf_document = None
        self.preview_content_stack.setCurrentIndex(0)
        self._set_badge(self.preview_status_badge, "审查包", "primary")
        self.source_text_box.setText(
            json.dumps(self.current_record.raw_structure, ensure_ascii=False, indent=2)
        )
        self.workbook = self.review_service.build_review_workbook(
            self.drafts,
            source_id=self.current_record.source_id,
        )
        self._render_tables()
        self._render_graph()
        self._set_workspace_state("审查包已打开", "primary")
        self._update_command_availability()
        self._update_review_toolbar_visibility()
        self.statusBar().showMessage(
            f"已打开审查包 revision {document.metadata.revision}；可继续审查或发布。"
        )

    def merge_review_document(self) -> None:
        if self.review_document is None:
            QMessageBox.information(self, "尚无当前会话", "请先分析教材或打开一个审查包。")
            return
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "合并 Excel 审查修订",
            str(WORKBENCH_STORAGE),
            "审查工作簿 (*.xlsx)",
        )
        if not file_path:
            return
        planner = ExcelImportPlanner()
        try:
            plan = planner.plan(Path(file_path), self.review_document)
        except (ReviewDocumentFormatError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "修订读取失败", str(exc))
            return
        if plan.status == "blocked":
            QMessageBox.warning(
                self,
                "修订导入已阻止",
                "\n".join(plan.warnings) or "审查包缺少可信导入基线。",
            )
            return
        if plan.conflicts:
            preview = "\n".join(
                f"- {item.record_type}/{item.object_id}/{item.field_name}"
                for item in plan.conflicts[:12]
            )
            if len(plan.conflicts) > 12:
                preview += f"\n- 另有 {len(plan.conflicts) - 12} 项"
            dialog = QMessageBox(self)
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setWindowTitle("发现三方合并冲突")
            dialog.setText(f"发现 {len(plan.conflicts)} 个同字段冲突：\n{preview}")
            dialog.setInformativeText(
                "请选择统一裁决；取消不会改变当前会话，也不会改写 Excel 文件。"
            )
            keep_local = dialog.addButton(
                "保留当前值并导入其他修改",
                QMessageBox.ButtonRole.AcceptRole,
            )
            use_excel = dialog.addButton(
                "采用 Excel 值",
                QMessageBox.ButtonRole.DestructiveRole,
            )
            dialog.addButton(QMessageBox.StandardButton.Cancel)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked is not keep_local and clicked is not use_excel:
                return
            plan = planner.resolve_conflicts(
                plan,
                use_imported_values=clicked is use_excel,
            )
        if not plan.changes:
            QMessageBox.information(self, "没有可导入修改", "Excel 与当前会话没有待合并差异。")
            return
        try:
            merged_document = planner.apply(
                plan,
                self.review_document,
                expected_revision=self.review_document.metadata.revision,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "修订导入失败", str(exc))
            return
        self._push_undo_snapshot("合并 Excel 审查修订")
        self.review_document = merged_document
        self.drafts = document_to_legacy_drafts(self.review_document)
        self._refresh_review_after_status_change(synchronize_document=False)
        self.statusBar().showMessage(
            f"已原子导入 {len(plan.changes)} 项 Excel 修订；文档 revision "
            f"{self.review_document.metadata.revision}。"
        )

    def _load_source_path(self, path: Path) -> bool:
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            QMessageBox.warning(self, "文件格式不支持", "请选择 PDF、PNG、JPG 或 JPEG 文件。")
            return False
        self.source_path = path
        self.source_format = path.suffix.lower().lstrip(".")
        self.current_record = None
        self.review_document = None
        self.current_staged_outcome = None
        self.drafts = []
        self.workbook = None
        self.current_quality_report = None
        self.layout_plans = {}
        self.layout_recommendations = {}
        self.current_analysis_run_id = ""
        self.current_progress_recording_dir = ""
        self.current_progress_recording_mode = PROGRESS_MODE_OFF
        self.graph_node_positions.clear()
        self.relation_status_overrides.clear()
        self.g6_view_config = {}
        self.undo_stack.clear()
        self._review_document_undo_stack.clear()
        self._update_review_toolbar_visibility()
        self.g6_graph_loaded = False
        self.source_id_input.setText(self.source_id_input.text() or path.stem)
        self.file_label.setText(path.name)
        self.file_label.setToolTip(str(path))
        self._render_graph()
        self._render_tables()
        if not self._preview_source(path):
            self.source_path = None
            self.source_format = ""
            self._set_workspace_state("打开失败", "danger")
            self._update_command_availability()
            return False
        try:
            self._load_base_record(path)
        except Exception as exc:
            self.source_path = None
            self.source_format = ""
            self._set_workspace_state("读取失败", "danger")
            self._update_command_availability()
            QMessageBox.warning(self, "教材读取失败", str(exc))
            return False
        self._set_workspace_state("已就绪", "primary")
        self._update_command_availability()
        self.statusBar().showMessage("文件已导入。")
        return True

    def _preview_source(self, path: Path) -> bool:
        self.preview_content_stack.setCurrentIndex(1)
        if path.suffix.lower() == ".pdf":
            for button in [
                self.pdf_prev_button,
                self.pdf_next_button,
                self.pdf_jump_button,
                self.range_set_start_button,
                self.range_set_end_button,
                self.range_jump_start_button,
                self.range_jump_end_button,
            ]:
                button.setEnabled(True)
            self.pdf_jump_input.setEnabled(True)
            self.pdf_document = QPdfDocument(self)
            load_status = self.pdf_document.load(str(path))
            if load_status != QPdfDocument.Error.None_:
                QMessageBox.warning(self, "PDF 打开失败", f"无法读取 PDF 文件：{load_status}")
                self.pdf_document = None
                self._set_badge(self.preview_status_badge, "打开失败", "danger")
                self.preview_content_stack.setCurrentIndex(0)
                return False
            self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
            self.pdf_view.setDocument(self.pdf_document)
            self.pdf_view.reset_to_fit_width()
            total_pages = max(1, self.pdf_document.pageCount())
            self.pdf_jump_input.setMaximum(total_pages)
            self.start_page_input.setMaximum(total_pages)
            self.end_page_input.setMaximum(total_pages)
            if self.end_page_input.value() < self.start_page_input.value():
                self.end_page_input.setValue(self.start_page_input.value())
            self._connect_pdf_navigator()
            self._update_pdf_page_badge()
            self._sync_analysis_range_hint()
            self._set_badge(self.preview_status_badge, f"PDF · {total_pages} 页", "primary")
            self.preview_stack.setCurrentIndex(0)
            self._sync_pdf_zoom_controls()
        else:
            if self.pdf_document is not None:
                self.pdf_view.setDocument(None)
            self.pdf_document = None
            self._sync_pdf_zoom_controls()
            for button in [
                self.pdf_prev_button,
                self.pdf_next_button,
                self.pdf_jump_button,
                self.range_set_start_button,
                self.range_set_end_button,
                self.range_jump_start_button,
                self.range_jump_end_button,
            ]:
                button.setEnabled(False)
            self.pdf_jump_input.setEnabled(False)
            pixmap = QPixmap(str(path))
            self.image_label.setPixmap(
                pixmap.scaled(
                    self.image_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self._sync_analysis_range_hint()
            self._set_badge(self.preview_status_badge, "图片", "primary")
            self.preview_stack.setCurrentIndex(1)
        return True

    def _sync_pdf_zoom_controls(self, *_args: object) -> None:
        has_document = self.pdf_view.has_ready_document()
        custom_mode = self.pdf_view.zoomMode() == QPdfView.ZoomMode.Custom
        zoom_factor = float(self.pdf_view.zoomFactor())
        at_minimum = custom_mode and zoom_factor <= self.pdf_view.MIN_ZOOM_FACTOR + 1e-6
        at_maximum = custom_mode and zoom_factor >= self.pdf_view.MAX_ZOOM_FACTOR - 1e-6

        self.pdf_zoom_label.setText(
            f"{zoom_factor * 100:.0f}%" if custom_mode else "适合宽度"
        )
        self.pdf_zoom_out_button.setEnabled(has_document and not at_minimum)
        self.pdf_zoom_in_button.setEnabled(has_document and not at_maximum)
        self.pdf_fit_width_button.setEnabled(has_document and custom_mode)
        self.pdf_zoom_out_action.setEnabled(has_document and not at_minimum)
        self.pdf_zoom_in_action.setEnabled(has_document and not at_maximum)
        self.pdf_fit_width_action.setEnabled(has_document and custom_mode)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        if self.source_path and self.source_format in {"png", "jpg", "jpeg"}:
            self._preview_source(self.source_path)

    def _connect_pdf_navigator(self) -> None:
        if self._pdf_navigator_connected or self.pdf_document is None:
            return
        navigator = self.pdf_view.pageNavigator()
        navigator.currentPageChanged.connect(self._update_pdf_page_badge)
        self._pdf_navigator_connected = True

    def _update_pdf_page_badge(self, *_args: object) -> None:
        if self.pdf_document is None:
            self.pdf_page_badge.setText("PDF 页码：- / -")
            self._update_pdf_navigation_state()
            self._sync_analysis_range_hint()
            return
        current_page = self.pdf_view.pageNavigator().currentPage() + 1
        total_pages = max(1, self.pdf_document.pageCount())
        page_label = self.pdf_document.pageLabel(current_page - 1)
        label_suffix = f" | 文档页标 {page_label}" if page_label and page_label != str(current_page) else ""
        self.pdf_page_badge.setText(f"PDF 页码：{current_page} / {total_pages}{label_suffix}")
        self.pdf_jump_input.blockSignals(True)
        self.pdf_jump_input.setMaximum(total_pages)
        self.pdf_jump_input.setValue(current_page)
        self.pdf_jump_input.blockSignals(False)
        self._update_pdf_navigation_state()
        self._sync_analysis_range_hint()

    def _update_pdf_navigation_state(self) -> None:
        if self.pdf_document is None:
            self.pdf_prev_button.setEnabled(False)
            self.pdf_next_button.setEnabled(False)
            return
        current_page = self.pdf_view.pageNavigator().currentPage()
        last_page = max(0, self.pdf_document.pageCount() - 1)
        self.pdf_prev_button.setEnabled(current_page > 0)
        self.pdf_next_button.setEnabled(current_page < last_page)

    def _step_pdf_page(self, delta: int) -> None:
        if self.pdf_document is None:
            return
        navigator = self.pdf_view.pageNavigator()
        current_page = navigator.currentPage()
        target_page = max(0, min(self.pdf_document.pageCount() - 1, current_page + delta))
        navigator.jump(target_page, QPointF(), navigator.currentZoom())

    def _jump_to_pdf_page(self) -> None:
        if self.pdf_document is None:
            return
        target_page = self.pdf_jump_input.value() - 1
        self.pdf_view.pageNavigator().jump(target_page, QPointF(), self.pdf_view.zoomFactor())

    def _current_pdf_page_number(self) -> int | None:
        if self.pdf_document is None:
            return None
        return self.pdf_view.pageNavigator().currentPage() + 1

    def _set_current_page_as_start(self) -> None:
        current_page = self._current_pdf_page_number()
        if current_page is None:
            return
        self.start_page_input.setValue(current_page)
        if self.end_page_input.value() < current_page:
            self.end_page_input.setValue(current_page)
        self._sync_analysis_range_hint()

    def _set_current_page_as_end(self) -> None:
        current_page = self._current_pdf_page_number()
        if current_page is None:
            return
        self.end_page_input.setValue(current_page)
        if self.start_page_input.value() > current_page:
            self.start_page_input.setValue(current_page)
        self._sync_analysis_range_hint()

    def _jump_to_configured_page(self, which: str) -> None:
        if self.pdf_document is None:
            return
        target_page = self.start_page_input.value() if which == "start" else self.end_page_input.value()
        self.pdf_view.pageNavigator().jump(target_page - 1, QPointF(), self.pdf_view.zoomFactor())

    def _sync_analysis_range_hint(self, *_args: object) -> None:
        start_page = self.start_page_input.value()
        end_page = self.end_page_input.value()
        if end_page < start_page:
            self._set_badge(
                self.analysis_range_badge,
                f"分析范围：{start_page}-{end_page} 页 | 范围无效",
                "danger",
            )
            return

        current_page = self._current_pdf_page_number()
        if current_page is None:
            if self.source_format in {"png", "jpg", "jpeg"}:
                text = "当前材料为图片，页码范围选择不适用"
            else:
                text = f"分析范围：{start_page}-{end_page} 页"
            self._set_badge(self.analysis_range_badge, text, "warning")
            return

        in_range = start_page <= current_page <= end_page
        state_text = "当前页在分析范围内" if in_range else "当前页不在分析范围内"
        self._set_badge(
            self.analysis_range_badge,
            f"分析范围：{start_page}-{end_page} 页 | 当前页 {current_page} | {state_text}",
            "success" if in_range else "warning",
        )

    def _load_base_record(self, path: Path) -> None:
        options = self._document_options()
        record = self.analysis_service.read_source(path, options)
        self.current_record = record
        self.source_text_box.setText(json.dumps(asdict(record), ensure_ascii=False, indent=2))
        self.info_box.setText(
            "已导入文件。\n"
            f"格式：{record.source_format}\n"
            f"元数据：{json.dumps(record.source_metadata, ensure_ascii=False)}"
        )

    def generate_candidates(self) -> None:
        if self.source_path is None:
            return
        if self.analysis_thread and self.analysis_thread.isRunning():
            QMessageBox.information(self, "正在分析", "当前已有分析任务在运行。")
            return
        if self.end_page_input.value() < self.start_page_input.value():
            QMessageBox.warning(self, "页码范围无效", "结束页不能小于起始页。")
            return
        missing_metadata = missing_required_metadata(
            self.subject_input.text(),
            self.grade_input.text(),
            self.term_input.text(),
        )
        if missing_metadata:
            QMessageBox.warning(
                self,
                "请确认教材信息",
                "开始分析前请明确填写：" + "、".join(missing_metadata) + "。\n"
                "系统不会再静默回退为 math/g8/term1。",
            )
            return

        self.analysis_dialog = AnalysisProgressDialog(self)
        self.analysis_thread = QThread(self)
        self.analysis_worker = AnalysisWorker(
            analysis_service=self.analysis_service,
            source_path=self.source_path,
            source_format=self.source_format,
            options=self._document_options(),
            start_page=self.start_page_input.value(),
            end_page=self.end_page_input.value(),
            scope_label=self.scope_label_input.text().strip() or "manual_scope",
            model_mode=self.model_mode_input.currentText(),
            api_key=self._resolved_ui_or_env_api_key(),
            model_name=self.model_input.text().strip()
            or self._model_config_value("TEXTBOOK_BUILDER_LLM_MODEL", "qwen3.6-plus"),
            base_url=self.base_url_input.text().strip()
            or self._model_config_value(
                "TEXTBOOK_BUILDER_LLM_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            dotenv_values=self.dotenv_values,
            ocr_enabled=self.ocr_enabled_input.isChecked(),
            page_order_check_enabled=self.page_order_check_input.isChecked(),
            staged_pipeline_enabled=self.staged_pipeline_input.isChecked(),
            progress_recording_mode=str(
                self.progress_recording_input.currentData() or PROGRESS_MODE_OFF
            ),
        )
        self.analysis_worker.moveToThread(self.analysis_thread)

        self.analysis_thread.started.connect(self.analysis_worker.run)
        self.analysis_worker.progress.connect(self._on_analysis_progress)
        self.analysis_worker.finished.connect(self._on_analysis_finished)
        self.analysis_worker.failed.connect(self._on_analysis_failed)
        self.analysis_worker.canceled.connect(self._on_analysis_canceled)
        self.analysis_worker.finished.connect(self.analysis_thread.quit)
        self.analysis_worker.failed.connect(self.analysis_thread.quit)
        self.analysis_worker.canceled.connect(self.analysis_thread.quit)
        self.analysis_thread.finished.connect(self._cleanup_analysis_worker)
        self.analysis_dialog.cancel_requested.connect(self._cancel_analysis)

        self.analyze_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.source_choose_button.setEnabled(False)
        self._set_workspace_state("分析中", "primary")
        self.statusBar().showMessage("正在分析教材，请稍候...")
        self.analysis_thread.start()
        self._update_command_availability()
        self.analysis_dialog.show()

    def _cancel_analysis(self) -> None:
        if self.analysis_worker is not None:
            self.analysis_worker.cancel()

    def _on_analysis_progress(self, value: int, message: str) -> None:
        if self.analysis_dialog is not None:
            self.analysis_dialog.update_progress(value, message)
        self.statusBar().showMessage(message)
        self.info_box.setText(message)

    def _on_analysis_finished(self, payload: object) -> None:
        if not isinstance(payload, WorkbenchAnalysisResult):
            self._on_analysis_failed("分析结果格式无效。")
            return
        self.current_record = payload.record
        self.drafts = payload.drafts
        normalize_draft_relation_endpoints(self.drafts)
        self.current_staged_outcome = payload.staged_outcome
        try:
            self.review_document = self.review_service.create_review_document(
                self.drafts,
                source_identity=payload.record.source_id,
                analysis_run_id=payload.analysis_run_id,
                staged_outcome=payload.staged_outcome,
            )
        except (AttributeError, TypeError, ValueError):
            # Test doubles and legacy integrations keep the compatible v1 path.
            self.review_document = None
        self.workbook = payload.workbook
        self.layout_plans = payload.layout_plans
        self.layout_recommendations = payload.layout_recommendations
        self.current_analysis_run_id = payload.analysis_run_id
        self.current_progress_recording_dir = payload.progress_recording_dir
        self.current_progress_recording_mode = payload.progress_recording_mode
        self.graph_node_positions.clear()
        self.relation_status_overrides.clear()
        self.g6_view_config = {}
        self.undo_stack.clear()
        self._review_document_undo_stack.clear()
        self._update_review_toolbar_visibility()
        self.g6_graph_loaded = False
        self.source_text_box.setText(json.dumps(asdict(self.current_record), ensure_ascii=False, indent=2))
        self._render_graph()
        self._render_tables()
        self.export_button.setEnabled(True)
        self.analyze_button.setEnabled(True)
        if self.analysis_dialog is not None:
            self.analysis_dialog.accept()
        pending_count = self._pending_node_count() + self._pending_relation_count()
        self._set_workspace_state(
            "待审查" if pending_count else "分析完成",
            "warning" if pending_count else "success",
        )
        self.statusBar().showMessage("候选图谱已生成。")
        model_advice_count = sum(
            1 for plan in self.layout_plans.values() if getattr(plan, "source", "local") != "local"
        )
        evidence_count = len(payload.page_evidence)
        cache_hits = sum(page.cache_hit for page in payload.page_evidence)
        suspicious_pairs = sum(item.needs_model_review for item in payload.continuity_results)
        run_summary = ""
        if payload.run_manifest is not None:
            run_summary = (
                f"\n分阶段调用：文本 {payload.run_manifest.text_model_calls} 次，"
                f"视觉 {payload.run_manifest.image_model_calls} 次，"
                f"输入约 {payload.run_manifest.model_input_characters} 字符"
            )
        recording_summary = ""
        if payload.progress_recording_dir:
            recording_summary = (
                f"\n过程记录：{payload.progress_recording_mode}"
                f"\n记录编号：{payload.analysis_run_id}"
                f"\n记录目录：{payload.progress_recording_dir}"
            )
        else:
            recording_summary = "\n过程记录：已关闭"
        self.info_box.setText(
            "候选图谱已生成。\n"
            f"节点数：{len(self.workbook.nodes)}\n"
            f"关系数：{len(self.workbook.edges)}\n"
            f"模型模式：{self.model_mode_input.currentText()}\n"
            f"章节结构建议：{'已补充 ' + str(model_advice_count) + ' 个章节' if model_advice_count else '采用本地骨架判断'}\n"
            f"页面证据：{evidence_count} 页（缓存命中 {cache_hits} 页）\n"
            f"页序检查：{'发现 ' + str(suspicious_pairs) + ' 组需确认' if suspicious_pairs else ('已通过' if payload.continuity_results else '未启用')}"
            f"{run_summary}"
            f"{recording_summary}"
        )

    def _on_analysis_failed(self, message: str) -> None:
        self.analyze_button.setEnabled(True)
        self.export_button.setEnabled(bool(self.drafts))
        if self.analysis_dialog is not None:
            self.analysis_dialog.reject()
        self._set_workspace_state("分析失败", "danger")
        self.statusBar().showMessage("分析失败。")
        self.info_box.setText(f"分析失败：{message}")
        QMessageBox.critical(self, "生成失败", message)

    def _on_analysis_canceled(self) -> None:
        self.analyze_button.setEnabled(True)
        self.export_button.setEnabled(bool(self.drafts))
        if self.analysis_dialog is not None:
            self.analysis_dialog.reject()
        self._set_workspace_state("已取消", "warning")
        self.statusBar().showMessage("本次分析已取消。")
        self.info_box.setText("本次分析已取消。若正在等待模型响应，当前轮次结果不会回填到工作台。")

    def _cleanup_analysis_worker(self) -> None:
        if self.analysis_worker is not None:
            self.analysis_worker.deleteLater()
        if self.analysis_thread is not None:
            self.analysis_thread.deleteLater()
        self.analysis_worker = None
        self.analysis_thread = None
        self.analysis_dialog = None
        self._update_command_availability()

    def _resolved_ui_or_env_api_key(self) -> str:
        ui_value = self.api_key_input.text().strip()
        if ui_value:
            return ui_value
        return self._model_config_value("TEXTBOOK_BUILDER_LLM_API_KEY", "")

    def _model_config_value(self, key: str, default: str) -> str:
        return env_value(key, dotenv_values=self.dotenv_values, default=default)

    def _render_graph(self) -> None:
        self.graph_scene.clear()
        if not self.workbook:
            if hasattr(self, "graph_content_stack"):
                self.graph_content_stack.setCurrentIndex(0)
            return
        if hasattr(self, "graph_content_stack"):
            self.graph_content_stack.setCurrentIndex(1)
        self._render_g6_graph()

        if self.review_document is not None:
            self._render_projection_graph()
            return

        node_by_id = {node.node_id: node for node in self.workbook.nodes}
        child_map = build_child_map(self.workbook.nodes, self.workbook.edges)
        layout_plans = self.layout_plans or build_chapter_layout_plans(
            self.workbook.nodes,
            self.workbook.edges,
        )
        relation_layouts = build_relation_layouts(
            self.workbook.nodes,
            self.workbook.edges,
        )
        node_roles = self._node_role_map(self.workbook.nodes, child_map, layout_plans)
        geometry = self._graph_geometry(
            self.workbook.nodes,
            child_map,
            layout_plans,
            node_roles,
            relation_layouts,
        )
        geometry = self._apply_graph_node_positions(geometry)
        formal_relation_pairs = self._formal_relation_pairs(self.workbook.edges)
        cycle_edge_ids = {
            edge_id
            for layout in relation_layouts.values()
            for edge_id in layout.cycle_edge_ids
        }

        for node in self.workbook.nodes:
            node_type = normalize_node_type(node.node_type or node.knowledge_type)
            if not child_map.get(node.node_id):
                continue
            rect = geometry.get(node.node_id)
            if not rect:
                continue
            self._draw_container(node=node, rect=rect, plan=layout_plans.get(node.node_id))

        for source_id, target_id, relation_type in self._layout_guide_edges(
            layout_plans,
            suppressed_pairs=formal_relation_pairs,
        ):
            source_rect = geometry.get(source_id)
            target_rect = geometry.get(target_id)
            if not source_rect or not target_rect:
                continue
            self._draw_relation_between(
                relation_type=relation_type,
                source_rect=source_rect,
                target_rect=target_rect,
            )

        for edge in self.workbook.edges:
            relation_type = normalize_relation_type(edge.relation_type)
            if relation_type == "contains":
                continue
            source_rect = geometry.get(edge.source_node_id)
            target_rect = geometry.get(edge.target_node_id)
            if not source_rect or not target_rect:
                continue
            self._draw_relation_edge(
                edge=edge,
                relation_type=relation_type,
                source_rect=source_rect,
                target_rect=target_rect,
                is_cycle_edge=relation_edge_id(
                    edge.source_node_id,
                    relation_type,
                    edge.target_node_id,
                ) in cycle_edge_ids,
            )

        for node_id, (x, y, width, height) in geometry.items():
            node = node_by_id.get(node_id)
            if node is None:
                continue
            node_type = normalize_node_type(node.node_type or node.knowledge_type)
            has_children = bool(child_map.get(node_id))
            if has_children:
                continue

            role = node_roles.get(node_id, "fallback")
            visual = self._node_visual_spec(node_type, role)
            group = DraggableNodeGroup(node_id, self._remember_graph_node_position)
            group.setPos(x, y)
            group.setToolTip("拖拽调整审查位置")
            group.setZValue(3.0)

            card = QGraphicsRectItem(0, 0, width, height, group)
            card.setBrush(QBrush(QColor(visual["fill"])))
            card.setPen(QPen(QColor(visual["border"]), float(visual["border_width"])))
            card.setZValue(0.0)

            title = QGraphicsSimpleTextItem(self._clip(node.display_name, int(visual["title_clip"])), group)
            title.setBrush(QBrush(QColor(visual["text"])))
            title.setFont(QFont("Microsoft YaHei", int(visual["title_size"]), QFont.Weight.Bold))
            title.setPos(12, 8)
            title.setZValue(1.0)

            subtitle = QGraphicsSimpleTextItem(self._node_subtitle(node, role), group)
            subtitle.setBrush(QBrush(QColor(visual["subtext"])))
            subtitle.setPos(12, height - 24)
            subtitle.setZValue(1.0)
            self.graph_scene.addItem(group)

        self.graph_scene.setSceneRect(self.graph_scene.itemsBoundingRect().adjusted(-40, -40, 80, 80))

    def _render_g6_graph(self) -> None:
        if self.g6_graph_view is None or not self.workbook:
            return
        self._apply_relation_status_overrides()
        GRAPH_REVIEW_STATIC.mkdir(parents=True, exist_ok=True)
        graph_id = self._current_graph_id()
        view_config = self._load_g6_view_config(graph_id)
        view_config["storage_mode"] = "desktop_file"
        self.g6_view_config = view_config
        if self.review_document is not None:
            bundle = self.review_service.build_review_render_bundle(
                self.review_document,
                renderer="g6",
                focused_scope_id=str(view_config.get("focused_scope_id", "")),
                hidden_view_ids=set(view_config.get("hidden_view_ids", [])),
                view_mode=self.graph_view_mode,
            )
            payload = GraphReviewPayloadBuilder().build_from_projection(
                projection=bundle.projection,
                geometry=bundle.geometry,
                view_config=view_config,
                document=self.review_document,
            )
            payload["view_config"].update(view_config)
            payload["view_config"]["layout_revision"] = GRAPH_LAYOUT_VERSION
            payload["view_config"]["layout_mode"] = (
                "knowledge_relations_v6"
                if self.graph_view_mode == VIEW_MODE_RELATIONS
                else "textbook_recursive_v6"
            )
            payload["view_config"]["view_mode"] = self.graph_view_mode
            payload["view_config"]["structure_fingerprint"] = bundle.projection.structure_fingerprint
            payload["view_config"]["storage_mode"] = "desktop_file"
        else:
            payload = GraphReviewPayloadBuilder().build(
                drafts=self.drafts,
                workbook=self.workbook,
                layout_plans=self.layout_plans or None,
                view_config=view_config,
            )
        if self.g6_graph_loaded:
            self._update_g6_graph_payload(payload)
            return
        self.g6_render_revision += 1
        html_path = GRAPH_REVIEW_STATIC / f"desktop_graph_review_{self.g6_render_revision:06d}.html"
        G6ReviewHtmlRenderer().render_to_file(payload=payload, output_path=html_path)
        url = QUrl.fromLocalFile(str(html_path))
        self.g6_graph_loaded = False
        self.g6_graph_view.load(url)

    def _on_g6_graph_load_finished(self, ok: bool) -> None:
        self.g6_graph_loaded = bool(ok)

    def _update_g6_graph_payload(self, payload: dict[str, Any]) -> None:
        if self.g6_graph_view is None:
            return
        payload_json = json.dumps(payload, ensure_ascii=False)
        script = (
            "window.updateGraphReviewPayload"
            f" && window.updateGraphReviewPayload({payload_json});"
        )
        self.g6_graph_view.page().runJavaScript(script)

    def _current_graph_id(self) -> str:
        if self.workbook is not None:
            return str(self.workbook.metadata.graph_id or "default")
        if self.current_record is not None:
            return str(self.current_record.source_id or "default")
        return "default"

    def _safe_graph_view_stem(self, graph_id: str) -> str:
        stem = "".join(char if char.isalnum() or char in "-_." else "_" for char in str(graph_id))
        return stem.strip("._")[:120] or "default"

    def _g6_view_config_path(self, graph_id: str) -> Path:
        return GRAPH_REVIEW_VIEW_STORAGE / (
            f"{self._safe_graph_view_stem(graph_id)}.{self.graph_view_mode}.json"
        )

    def _load_g6_view_config(self, graph_id: str) -> dict[str, Any]:
        path = self._g6_view_config_path(graph_id)
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        try:
            layout_revision = int(value.get("layout_revision", 1))
        except (TypeError, ValueError):
            layout_revision = 1
        stored_mode = str(value.get("view_mode") or "")
        if layout_revision > GRAPH_LAYOUT_VERSION:
            value = dict(value)
            value["manual_positions"] = {}
            value["read_only_future_version"] = True
            return value
        if layout_revision < GRAPH_LAYOUT_VERSION or stored_mode != self.graph_view_mode:
            value = dict(value)
            value["layout_revision"] = GRAPH_LAYOUT_VERSION
            value["layout_mode"] = (
                "knowledge_relations_v6"
                if self.graph_view_mode == VIEW_MODE_RELATIONS
                else "textbook_recursive_v6"
            )
            value["view_mode"] = self.graph_view_mode
            value["manual_positions"] = {}
            value.setdefault("show_auxiliary_edges", False)
        return value

    def _save_g6_view_config(self, config: dict[str, Any]) -> bool:
        if self.g6_view_config.get("read_only_future_version"):
            self.statusBar().showMessage(
                "检测到更高版本的视图状态；为避免有损覆盖，本版本未保存。"
            )
            return False
        graph_id = str(config.get("graph_id") or self._current_graph_id())
        if graph_id != self._current_graph_id():
            return False
        manual_positions = self._clean_g6_manual_positions(config.get("manual_positions", {}))
        view_config = {
            "graph_id": graph_id,
            "layout_revision": GRAPH_LAYOUT_VERSION,
            "layout_mode": config.get("layout_mode") or (
                "knowledge_relations_v6"
                if self.graph_view_mode == VIEW_MODE_RELATIONS
                else "textbook_recursive_v6"
            ),
            "view_mode": self.graph_view_mode,
            "storage_mode": "desktop_file",
            "show_auxiliary_edges": bool(config.get("show_auxiliary_edges", False)),
            "show_relation_groups": bool(config.get("show_relation_groups", True)),
            "structure_fingerprint": str(config.get("structure_fingerprint", "")),
            "manual_positions": manual_positions,
            "hidden_view_ids": sorted(
                {
                    str(item)
                    for item in config.get(
                        "hidden_view_ids",
                        self.g6_view_config.get("hidden_view_ids", []),
                    )
                    if str(item)
                }
            ),
            "collapsed_scope_ids": list(config.get("collapsed_scope_ids", [])),
            "focused_scope_id": str(config.get("focused_scope_id", "")),
            "updated_at": str(config.get("updated_at") or ""),
        }
        try:
            self._persist_graph_view_config(graph_id, view_config)
        except OSError as exc:
            self.statusBar().showMessage(f"G6 视图配置未保存：{exc}")
            return False
        self.g6_view_config = view_config
        self.statusBar().showMessage("G6 图谱审查视图配置已保存。")
        return True

    def _persist_graph_view_config(self, graph_id: str, view_config: dict[str, Any]) -> None:
        GRAPH_REVIEW_VIEW_STORAGE.mkdir(parents=True, exist_ok=True)
        path = self._g6_view_config_path(graph_id)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(view_config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _clean_g6_manual_positions(self, value: object) -> dict[str, dict[str, float]]:
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, dict[str, float]] = {}
        for node_id, position in value.items():
            if len(cleaned) >= 5000:
                break
            if not isinstance(node_id, str) or not isinstance(position, dict):
                continue
            try:
                x = float(position["x"])
                y = float(position["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(x) or not math.isfinite(y) or abs(x) > 10_000_000 or abs(y) > 10_000_000:
                continue
            cleaned[node_id] = {"x": x, "y": y}
        return cleaned

    def _edge_status_key(self, source_node_id: str, relation_type: str, target_node_id: str) -> str:
        return self.review_session.edge_status_key(source_node_id, relation_type, target_node_id)

    def _apply_relation_status_overrides(self) -> None:
        self.review_session.apply_status_overrides(self.workbook)

    def _apply_graph_node_positions(
        self,
        geometry: dict[str, tuple[float, float, float, float]],
    ) -> dict[str, tuple[float, float, float, float]]:
        if not self.graph_node_positions:
            return geometry
        adjusted = dict(geometry)
        for node_id, (x, y) in self.graph_node_positions.items():
            rect = adjusted.get(node_id)
            if rect is None:
                continue
            adjusted[node_id] = (x, y, rect[2], rect[3])
        return adjusted

    def _remember_graph_node_position(self, node_id: str, position: tuple[float, float]) -> None:
        self.graph_node_positions[node_id] = position
        QTimer.singleShot(0, self._render_graph)

    def _graph_geometry(
        self,
        nodes: list[FormalNodeDTO],
        child_map: dict[str, list[str]],
        layout_plans: dict[str, ChapterLayoutPlan],
        node_roles: dict[str, str],
        relation_layouts: dict[str, ChapterRelationLayout] | None = None,
    ) -> dict[str, tuple[float, float, float, float]]:
        if relation_layouts is None:
            relation_layouts = build_relation_layouts(
                nodes,
                self.workbook.edges if self.workbook is not None else [],
            )
        geometry: dict[str, tuple[float, float, float, float]] = {}
        parent_ids = {node.parent_node_id for node in nodes if node.parent_node_id}
        top_level_containers = [
            node
            for node in nodes
            if child_map.get(node.node_id)
            and not node.parent_node_id
        ]
        top_level_containers.sort(key=structural_order_key)
        top_level_orphans = [
            node
            for node in nodes
            if node.node_id not in parent_ids
            and not node.parent_node_id
            and not child_map.get(node.node_id)
        ]

        y_cursor = 40.0
        for container in top_level_containers:
            children = child_map.get(container.node_id, [])
            plan = layout_plans.get(container.node_id)
            relation_layout = relation_layouts.get(container.node_id)
            if relation_layout is not None:
                nodes_by_rank = relation_layout.nodes_by_rank()
                column_gap = 112.0
                row_gap = 24.0
                column_widths = {
                    rank: max(
                        (
                            self._node_box_for_role(node_roles.get(node_id, "fallback"))[0]
                            for node_id in node_ids
                        ),
                        default=158.0,
                    )
                    for rank, node_ids in nodes_by_rank.items()
                }
                column_heights = {
                    rank: sum(
                        self._node_box_for_role(node_roles.get(node_id, "fallback"))[1]
                        for node_id in node_ids
                    ) + row_gap * max(0, len(node_ids) - 1)
                    for rank, node_ids in nodes_by_rank.items()
                }
                content_width = sum(column_widths.values()) + column_gap * max(
                    0,
                    len(column_widths) - 1,
                )
                content_height = max(column_heights.values(), default=58.0)
                width = max(700.0, content_width + 68.0)
                height = max(196.0, 86.0 + content_height + 30.0)
                geometry[container.node_id] = (40.0, y_cursor, width, height)
                x_cursor = 40.0 + (width - content_width) / 2
                content_top = y_cursor + 86.0
                for rank in sorted(nodes_by_rank):
                    column_width = column_widths[rank]
                    node_y = content_top
                    for child_id in nodes_by_rank[rank]:
                        width_box, height_box = self._node_box_for_role(
                            node_roles.get(child_id, "fallback")
                        )
                        geometry[child_id] = (
                            x_cursor + (column_width - width_box) / 2,
                            node_y,
                            width_box,
                            height_box,
                        )
                        node_y += height_box + row_gap
                    x_cursor += column_width + column_gap
                y_cursor += height + 48.0
                continue
            structural_children = list(plan.structural_children if plan else [])
            main_path = list(plan.main_path_nodes if plan else [])
            branch_groups = dict(plan.branch_groups if plan else {})
            auxiliary_attachments = dict(plan.auxiliary_attachments if plan else {})
            assigned_children = set(structural_children)
            assigned_children.update(main_path)
            assigned_children.update(item for values in branch_groups.values() for item in values)
            assigned_children.update(item for values in auxiliary_attachments.values() for item in values)
            fallback_children = [child_id for child_id in children if child_id not in assigned_children]

            structural_gap = 104.0
            structural_width = self._lane_width(
                structural_children,
                node_roles,
                gap=structural_gap,
            )
            main_width = self._lane_width(main_path, node_roles)
            fallback_width = self._lane_width(fallback_children, node_roles)
            aux_width = max(
                (self._lane_width(values, node_roles, gap=18.0) for values in auxiliary_attachments.values()),
                default=0.0,
            )
            width = max(700.0, 140.0 + max(structural_width, main_width, fallback_width, aux_width, 280.0))
            structural_lane = 72.0 if structural_children else 0.0
            main_lane = 96.0 if main_path else 0.0
            max_branch_depth = max((len(values) for values in branch_groups.values()), default=0)
            branch_lane = 82.0 * max(1, max_branch_depth) if branch_groups else 0.0
            auxiliary_lane = 74.0 if auxiliary_attachments else 0.0
            fallback_lane = 72.0 if fallback_children else 0.0
            height = 118.0 + structural_lane + main_lane + branch_lane + auxiliary_lane + fallback_lane + 18.0
            geometry[container.node_id] = (40.0, y_cursor, width, height)
            content_left = 40.0 + 34.0
            content_right = 40.0 + width - 34.0
            lane_y = y_cursor + 86.0
            if structural_children:
                x_cursor = self._centered_lane_start(
                    content_left,
                    content_right,
                    structural_children,
                    node_roles,
                    gap=structural_gap,
                )
                for child_id in structural_children:
                    width_box, height_box = self._node_box_for_role(node_roles.get(child_id, "structural"))
                    geometry[child_id] = (x_cursor, lane_y, width_box, height_box)
                    x_cursor += width_box + structural_gap
                lane_y += 76.0
            main_centers: dict[str, float] = {}
            if main_path:
                x_cursor = self._centered_lane_start(
                    content_left,
                    content_right,
                    main_path,
                    node_roles,
                    gap=24.0,
                )
                for child_id in main_path:
                    width_box, height_box = self._node_box_for_role(node_roles.get(child_id, "main_path"))
                    geometry[child_id] = (x_cursor, lane_y, width_box, height_box)
                    main_centers[child_id] = x_cursor + width_box / 2
                    x_cursor += width_box + 24.0
                lane_y += 98.0
            if branch_groups:
                for parent_id, branch_ids in branch_groups.items():
                    center_x = main_centers.get(parent_id, content_left + 90.0)
                    total_width = self._lane_width(branch_ids, node_roles, gap=18.0)
                    x_cursor = max(content_left, min(content_right - total_width, center_x - total_width / 2))
                    for index, branch_id in enumerate(branch_ids):
                        width_box, height_box = self._node_box_for_role(node_roles.get(branch_id, "branch"))
                        geometry[branch_id] = (x_cursor, lane_y + index * 82.0, width_box, height_box)
                        x_cursor += width_box + 18.0
                lane_y += max(1, max_branch_depth) * 84.0
            if auxiliary_attachments:
                for parent_id, attached_ids in auxiliary_attachments.items():
                    if not attached_ids:
                        continue
                    total_width = self._lane_width(attached_ids, node_roles, gap=16.0)
                    parent_center = main_centers.get(parent_id, (content_left + content_right) / 2)
                    x_cursor = max(content_left, min(content_right - total_width, parent_center - total_width / 2))
                    for attached_id in attached_ids:
                        width_box, height_box = self._node_box_for_role(node_roles.get(attached_id, "auxiliary"))
                        geometry[attached_id] = (x_cursor, lane_y, width_box, height_box)
                        x_cursor += width_box + 16.0
                lane_y += 84.0
            if fallback_children:
                x_cursor = self._centered_lane_start(
                    content_left,
                    content_right,
                    fallback_children,
                    node_roles,
                    gap=16.0,
                )
                for child_id in fallback_children:
                    width_box, height_box = self._node_box_for_role(node_roles.get(child_id, "fallback"))
                    geometry[child_id] = (x_cursor, lane_y, width_box, height_box)
                    x_cursor += width_box + 16.0
            y_cursor += height + 48.0

        if top_level_orphans:
            child_x = 72.0
            for orphan in top_level_orphans:
                width_box, height_box = self._node_box_for_role("orphan")
                geometry[orphan.node_id] = (child_x, y_cursor + 52.0, width_box, height_box)
                child_x += width_box + 24.0
            y_cursor += 170.0

        for node in nodes:
            if node.node_id in geometry:
                continue
            parent_rect = geometry.get(node.parent_node_id)
            if parent_rect:
                siblings = child_map.get(node.parent_node_id, [])
                index = siblings.index(node.node_id) if node.node_id in siblings else len(siblings)
                width_box, height_box = self._node_box_for_role(node_roles.get(node.node_id, "fallback"))
                geometry[node.node_id] = (
                    parent_rect[0] + 32.0 + index * 186.0,
                    parent_rect[1] + 96.0,
                    width_box,
                    height_box,
                )
            else:
                width_box, height_box = self._node_box_for_role(node_roles.get(node.node_id, "orphan"))
                geometry[node.node_id] = (60.0, y_cursor, width_box, height_box)
                y_cursor += 96.0
        return geometry

    def _draw_container(
        self,
        *,
        node: FormalNodeDTO,
        rect: tuple[float, float, float, float],
        plan: ChapterLayoutPlan | None,
    ) -> None:
        x, y, width, height = rect
        frame = QGraphicsRectItem(x, y, width, height)
        frame.setBrush(QBrush(QColor("#fbf7e9")))
        frame.setPen(QPen(QColor("#d8aa42"), 1.8))
        frame.setZValue(0.2)
        self.graph_scene.addItem(frame)

        header = QGraphicsRectItem(x, y, width, 58.0)
        header.setBrush(QBrush(QColor("#f2e7bf")))
        header.setPen(QPen(QColor("#d8aa42"), 0.8))
        header.setZValue(0.4)
        self.graph_scene.addItem(header)

        title = QGraphicsTextItem(node.display_name)
        title.setDefaultTextColor(QColor("#6f4b00"))
        title.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        title.setPos(x + 16, y + 7)
        title.setZValue(0.8)
        self.graph_scene.addItem(title)

        mode_label = self._layout_mode_label(plan.layout_mode if plan else "")
        meta = QGraphicsSimpleTextItem(
            f"{mode_label}  |  主线 {len(plan.main_path_nodes) if plan else 0}  |  置信度 {int((plan.local_confidence if plan else 0) * 100)}%"
        )
        meta.setBrush(QBrush(QColor("#9a6b10")))
        meta.setPos(x + 16, y + 36)
        meta.setZValue(0.8)
        self.graph_scene.addItem(meta)

    def _render_projection_graph(self) -> None:
        if self.review_document is None:
            return
        bundle = self.review_service.build_review_render_bundle(
            self.review_document,
            renderer="qt",
            view_mode=self.graph_view_mode,
            manual_positions={
                node_id: {"x": position[0], "y": position[1]}
                for node_id, position in self.graph_node_positions.items()
            },
        )
        projection = bundle.projection
        geometry = bundle.geometry
        scope_by_view = {item.view_id: item for item in projection.scopes}
        instance_by_view = {item.view_id: item for item in projection.node_instances}
        ordinal_by_node = {
            item.node_id: item.ordinal
            for sequence in projection.reading_sequences
            for item in sequence.items
        }

        for visual_group in geometry.visual_groups.values():
            rect = visual_group.frame_rect
            isolated = visual_group.kind == "unconnected_region"
            frame = QGraphicsRectItem(rect.x, rect.y, rect.width, rect.height)
            frame.setBrush(QBrush(QColor("#f8fafc" if isolated else "#f6f8fb")))
            pen = QPen(QColor("#94a3b8" if isolated else "#9db3cc"), 1.4)
            if isolated:
                pen.setStyle(Qt.PenStyle.DashLine)
            frame.setPen(pen)
            frame.setZValue(0.02)
            self.graph_scene.addItem(frame)
            header = QGraphicsRectItem(rect.x, rect.y, rect.width, 42.0)
            header.setBrush(QBrush(QColor("#f1f5f9")))
            header.setPen(QPen(QColor("#cbd5e1"), 0.8))
            header.setZValue(0.03)
            self.graph_scene.addItem(header)
            title = QGraphicsSimpleTextItem(visual_group.title)
            title.setBrush(QBrush(QColor("#475569")))
            title.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
            title.setPos(rect.x + 14, rect.y + 10)
            title.setZValue(0.04)
            self.graph_scene.addItem(title)

        # Parent frames are drawn first; deeper frames naturally sit above them.
        depth_cache: dict[str, int] = {}
        parent_by_scope = {item.scope_id: item.parent_scope_id for item in projection.scopes}
        def scope_depth(scope_id: str) -> int:
            if scope_id in depth_cache:
                return depth_cache[scope_id]
            depth = 0
            current = scope_id
            seen: set[str] = set()
            while parent_by_scope.get(current) and current not in seen:
                seen.add(current)
                current = parent_by_scope[current]
                depth += 1
            depth_cache[scope_id] = depth
            return depth

        for view_id, rect in sorted(
            geometry.scope_rects.items(),
            key=lambda item: (scope_depth(scope_by_view[item[0]].scope_id), item[0]),
        ):
            scope = scope_by_view[view_id]
            colors = self._status_colors(scope.review_status)
            frame = QGraphicsRectItem(rect.x, rect.y, rect.width, rect.height)
            frame.setBrush(QBrush(QColor("#fbfaf6" if not scope.is_virtual else "#f8fafc")))
            frame.setPen(QPen(QColor(colors["border"]), 1.8))
            frame.setZValue(0.1 + scope_depth(scope.scope_id) * 0.05)
            self.graph_scene.addItem(frame)
            header = QGraphicsRectItem(rect.x, rect.y, rect.width, 54.0)
            header.setBrush(QBrush(QColor(colors["bg"])))
            header.setPen(QPen(QColor(colors["border"]), 0.8))
            header.setZValue(frame.zValue() + 0.02)
            self.graph_scene.addItem(header)
            title = QGraphicsSimpleTextItem(
                f"{self._clip(scope.label, 28)} · {self._review_status_label(scope.review_status)}"
            )
            title.setBrush(QBrush(QColor(colors["fg"])))
            title.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
            title.setPos(rect.x + 14, rect.y + 15)
            title.setZValue(frame.zValue() + 0.04)
            self.graph_scene.addItem(title)

        for edge in geometry.edge_instances:
            source = geometry.all_rects.get(edge.source_view_id)
            target = geometry.all_rects.get(edge.target_view_id)
            if source is None or target is None:
                continue
            self._draw_relation_between(
                relation_type=edge.relation_type,
                relation_family=edge.relation_family,
                source_rect=(source.x, source.y, source.width, source.height),
                target_rect=(target.x, target.y, target.width, target.height),
                review_status=edge.review_status,
                is_cycle_edge=edge.constraint_status == "semantic_cycle",
                route_points=(
                    list(geometry.edge_routes[edge.view_id].points)
                    if edge.view_id in geometry.edge_routes
                    else None
                ),
                edge_id=edge.relation_id,
                label_geometry=geometry.edge_labels.get(edge.view_id),
                route_geometry=geometry.edge_routes.get(edge.view_id),
            )

        for view_id, rect in geometry.node_rects.items():
            instance = instance_by_view[view_id]
            visual = self._node_visual_spec(instance.node_type, "fallback")
            group = DraggableNodeGroup(view_id, self._remember_graph_node_position)
            group.setPos(rect.x, rect.y)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            group.setCursor(Qt.CursorShape.ArrowCursor)
            group.setToolTip(
                f"业务节点：{instance.node_id}\n"
                f"教材归属：{len(instance.membership_ids) if self.graph_view_mode == VIEW_MODE_RELATIONS else (1 if instance.membership_id else 0)} 处\n"
                "自动布局视图已锁定；请使用重新布局恢复整体几何。"
            )
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            group.setData(0, "node")
            group.setData(1, instance.node_id)
            group.setData(2, instance.view_id)
            group.setZValue(3.0)
            card = QGraphicsRectItem(0, 0, rect.width, rect.height, group)
            card.setBrush(QBrush(QColor(visual["fill"])))
            status_colors = self._status_colors(instance.review_status)
            card.setPen(QPen(QColor(status_colors["border"]), 2.0))
            title_font = QFont("Microsoft YaHei", 10, QFont.Weight.Bold)
            title_prefix = f"{ordinal_by_node[instance.node_id]}  " if instance.node_id in ordinal_by_node else ""
            title_text = QFontMetrics(title_font).elidedText(
                title_prefix + instance.label,
                Qt.TextElideMode.ElideRight,
                max(40, int(rect.width - 112)),
            )
            title = QGraphicsSimpleTextItem(title_text, group)
            title.setBrush(QBrush(QColor(visual["text"])))
            title.setFont(title_font)
            title.setPos(12, 9)
            badge = QGraphicsRectItem(rect.width - 76, 8, 64, 22, group)
            badge.setBrush(QBrush(QColor(status_colors["bg"])))
            badge.setPen(QPen(QColor(status_colors["border"]), 1.0))
            badge_text = QGraphicsSimpleTextItem(
                self._review_status_label(instance.review_status), group
            )
            badge_text.setBrush(QBrush(QColor(status_colors["fg"])))
            badge_text.setFont(QFont("Microsoft YaHei", 8, QFont.Weight.DemiBold))
            badge_bounds = badge_text.boundingRect()
            badge_text.setPos(
                rect.width - 44 - badge_bounds.width() / 2,
                19 - badge_bounds.height() / 2,
            )
            subtitle = QGraphicsSimpleTextItem(
                f"{self._knowledge_type_label(instance.node_type)} · "
                f"{self._review_status_label(instance.review_status)}"
                f"{' · 引用' if instance.is_reference else ''}",
                group,
            )
            subtitle.setBrush(QBrush(QColor(visual["subtext"])))
            subtitle.setPos(12, rect.height - 25)
            self.graph_scene.addItem(group)
        self.graph_scene.setSceneRect(
            self.graph_scene.itemsBoundingRect().adjusted(-40, -40, 80, 80)
        )
        serious = [
            issue
            for issue in geometry.issues
            if issue.code.startswith("geometry_")
            or issue.code in {"layout_coordinate_missing", "edge_route_failed"}
        ]
        if serious:
            self.statusBar().showMessage(
                f"二维布局完成，但有 {len(serious)} 个几何/路由问题需要检查。"
            )

    @staticmethod
    def _layout_mode_label(mode: str) -> str:
        labels = {
            "single_path": "单主线",
            "main_path_with_branches": "主线+分支",
            "multi_core_clusters": "多核心",
        }
        return labels.get(mode, "章节结构")

    @staticmethod
    def _node_subtitle(node: FormalNodeDTO, role: str) -> str:
        role_labels = {
            "main_path": "主路径",
            "branch": "分支",
            "auxiliary": "侧挂",
            "structural": "子结构",
            "fallback": "待确认",
            "orphan": "独立",
        }
        node_type = normalize_node_type(node.node_type or node.knowledge_type)
        role_text = role_labels.get(role, "")
        return f"{node_type} | {role_text}" if role_text else node_type

    def _node_role_map(
        self,
        nodes: list[FormalNodeDTO],
        child_map: dict[str, list[str]],
        layout_plans: dict[str, ChapterLayoutPlan],
    ) -> dict[str, str]:
        roles: dict[str, str] = {}
        for container_id, plan in layout_plans.items():
            roles[container_id] = "container"
            for child_id in plan.structural_children:
                roles[child_id] = "structural"
            for child_id in plan.main_path_nodes:
                roles[child_id] = "main_path"
            for branch_ids in plan.branch_groups.values():
                for child_id in branch_ids:
                    roles.setdefault(child_id, "branch")
            for attachment_ids in plan.auxiliary_attachments.values():
                for child_id in attachment_ids:
                    roles.setdefault(child_id, "auxiliary")
            for child_id in plan.unassigned_nodes:
                roles.setdefault(child_id, "fallback")
        for node in nodes:
            if node.node_id in roles:
                continue
            if is_container_node_type(node.node_type or node.knowledge_type):
                roles[node.node_id] = "container"
            elif node.parent_node_id:
                roles[node.node_id] = "fallback"
            else:
                roles[node.node_id] = "orphan"
        return roles

    @staticmethod
    def _layout_guide_edges(
        layout_plans: dict[str, ChapterLayoutPlan],
        suppressed_pairs: set[frozenset[str]] | None = None,
    ) -> list[tuple[str, str, str]]:
        suppressed_pairs = suppressed_pairs or set()
        guide_edges: list[tuple[str, str, str]] = []
        for plan in layout_plans.values():
            main_path = list(plan.main_path_nodes)
            for index in range(len(main_path) - 1):
                source_id = main_path[index]
                target_id = main_path[index + 1]
                if frozenset({source_id, target_id}) not in suppressed_pairs:
                    guide_edges.append((source_id, target_id, "layout_main_path"))
            for parent_id, child_ids in plan.branch_groups.items():
                for child_id in child_ids:
                    if frozenset({parent_id, child_id}) not in suppressed_pairs:
                        guide_edges.append((parent_id, child_id, "layout_branch"))
            for parent_id, child_ids in plan.auxiliary_attachments.items():
                for child_id in child_ids:
                    if frozenset({parent_id, child_id}) not in suppressed_pairs:
                        guide_edges.append((parent_id, child_id, "layout_auxiliary"))
        return guide_edges

    @staticmethod
    def _formal_relation_pairs(edges: list[FormalEdgeDTO]) -> set[frozenset[str]]:
        return {
            frozenset({edge.source_node_id, edge.target_node_id})
            for edge in edges
            if normalize_relation_type(edge.relation_type) != "contains"
        }

    @staticmethod
    def _node_box_for_role(role: str) -> tuple[float, float]:
        if role == "main_path":
            return (198.0, 82.0)
        if role == "branch":
            return (172.0, 68.0)
        if role == "auxiliary":
            return (156.0, 58.0)
        if role == "structural":
            return (150.0, 46.0)
        if role == "orphan":
            return (170.0, 62.0)
        return (158.0, 58.0)

    def _lane_width(self, node_ids: list[str], node_roles: dict[str, str], *, gap: float = 24.0) -> float:
        if not node_ids:
            return 0.0
        total = 0.0
        for index, node_id in enumerate(node_ids):
            total += self._node_box_for_role(node_roles.get(node_id, "fallback"))[0]
            if index < len(node_ids) - 1:
                total += gap
        return total

    def _centered_lane_start(
        self,
        content_left: float,
        content_right: float,
        node_ids: list[str],
        node_roles: dict[str, str],
        *,
        gap: float = 24.0,
    ) -> float:
        total_width = self._lane_width(node_ids, node_roles, gap=gap)
        return max(content_left, content_left + (content_right - content_left - total_width) / 2)

    @staticmethod
    def _node_visual_spec(node_type: str, role: str) -> dict[str, object]:
        styles = {
            "concept": {"fill": "#1f2937", "border": "#0f172a", "text": "#ffffff", "subtext": "#cbd5e1"},
            "property": {"fill": "#334e68", "border": "#243b53", "text": "#ffffff", "subtext": "#d9e2ec"},
            "rule": {"fill": "#5b21b6", "border": "#4c1d95", "text": "#ffffff", "subtext": "#ede9fe"},
            "method": {"fill": "#166534", "border": "#14532d", "text": "#ffffff", "subtext": "#dcfce7"},
            "representation": {"fill": "#d7f5f1", "border": "#0f766e", "text": "#0f4f49", "subtext": "#0f766e"},
            "problem_type": {"fill": "#ffedd5", "border": "#c2410c", "text": "#9a3412", "subtext": "#c2410c"},
            "application": {"fill": "#fef3c7", "border": "#d97706", "text": "#92400e", "subtext": "#b45309"},
        }
        visual = dict(styles.get(node_type, styles["concept"]))
        if role == "main_path":
            visual.update({"border_width": 2.2, "title_size": 11, "title_clip": 18})
        elif role == "branch":
            visual.update({"border_width": 1.8, "title_size": 10, "title_clip": 16})
        elif role == "structural":
            visual.update({"fill": "#fffaf0", "border": "#c6901a", "text": "#7c4c00", "subtext": "#9a6b10", "border_width": 1.4, "title_size": 9, "title_clip": 14})
        elif role == "auxiliary":
            visual.update({"border_width": 1.4, "title_size": 9, "title_clip": 14})
        else:
            visual.update({"border_width": 1.3, "title_size": 9, "title_clip": 14})
        return visual

    @staticmethod
    def _relation_visual_spec(relation_type: str) -> dict[str, object]:
        specs = {
            "layout_main_path": {"color": "#2563eb", "style": Qt.PenStyle.SolidLine, "width": 4.2, "start_arrow": False, "end_arrow": True, "opacity": 0.82, "label": True, "z": 1.35},
            "layout_branch": {"color": "#64748b", "style": Qt.PenStyle.DashLine, "width": 2.4, "start_arrow": False, "end_arrow": True, "opacity": 0.66, "label": True, "z": 1.25},
            "layout_auxiliary": {"color": "#b45309", "style": Qt.PenStyle.DashDotLine, "width": 1.8, "start_arrow": False, "end_arrow": True, "opacity": 0.58, "label": True, "z": 1.2},
            "prerequisite": {"color": "#1d4ed8", "style": Qt.PenStyle.SolidLine, "width": 3.5, "start_arrow": False, "end_arrow": True, "opacity": 0.98, "label": True},
            "progressive": {"color": "#0f766e", "style": Qt.PenStyle.SolidLine, "width": 3.2, "start_arrow": False, "end_arrow": True, "opacity": 0.95, "label": True},
            "derives_to": {"color": "#7c3aed", "style": Qt.PenStyle.SolidLine, "width": 3.0, "start_arrow": False, "end_arrow": True, "opacity": 0.95, "label": True},
            "explains": {"color": "#64748b", "style": Qt.PenStyle.DashLine, "width": 1.6, "start_arrow": False, "end_arrow": True, "opacity": 0.62, "label": True},
            "equivalent": {"color": "#475467", "style": Qt.PenStyle.SolidLine, "width": 1.9, "start_arrow": True, "end_arrow": True, "opacity": 0.68, "label": True},
            "parallel": {"color": "#7c3aed", "style": Qt.PenStyle.DashLine, "width": 1.6, "start_arrow": False, "end_arrow": False, "opacity": 0.58, "label": True},
            "contrast": {"color": "#b42318", "style": Qt.PenStyle.DashLine, "width": 1.8, "start_arrow": True, "end_arrow": True, "opacity": 0.64, "label": True},
            "applies_to": {"color": "#b45309", "style": Qt.PenStyle.SolidLine, "width": 2.1, "start_arrow": False, "end_arrow": True, "opacity": 0.72, "label": True},
            "represented_by": {"color": "#0f766e", "style": Qt.PenStyle.DashDotLine, "width": 1.5, "start_arrow": False, "end_arrow": True, "opacity": 0.6, "label": True},
        }
        return specs.get(
            relation_type,
            {"color": "#667085", "style": Qt.PenStyle.SolidLine, "width": 1.8, "start_arrow": False, "end_arrow": True, "opacity": 0.72, "label": True},
        )

    def _draw_relation_edge(
        self,
        *,
        edge: FormalEdgeDTO,
        relation_type: str,
        source_rect: tuple[float, float, float, float],
        target_rect: tuple[float, float, float, float],
        is_cycle_edge: bool = False,
    ) -> None:
        self._draw_relation_between(
            relation_type=relation_type,
            source_rect=source_rect,
            target_rect=target_rect,
            review_status=edge.review_status,
            is_cycle_edge=is_cycle_edge,
        )

    def _draw_relation_between(
        self,
        *,
        relation_type: str,
        relation_family: str = "semantic",
        source_rect: tuple[float, float, float, float],
        target_rect: tuple[float, float, float, float],
        review_status: str = "",
        is_cycle_edge: bool = False,
        route_points: list[tuple[float, float]] | None = None,
        edge_id: str = "",
        label_geometry: object | None = None,
        route_geometry: object | None = None,
    ) -> None:
        source_center = QPointF(source_rect[0] + source_rect[2] / 2, source_rect[1] + source_rect[3] / 2)
        target_center = QPointF(target_rect[0] + target_rect[2] / 2, target_rect[1] + target_rect[3] / 2)
        directionality = relation_directionality(relation_type)
        horizontal = (
            abs(target_center.x() - source_center.x()) >= abs(target_center.y() - source_center.y())
        )
        if directionality == "directional" and target_center.x() != source_center.x():
            horizontal = True
        if directionality == "symmetric" and abs(target_center.x() - source_center.x()) < 1.0:
            horizontal = False
        if is_cycle_edge:
            start = QPointF(source_rect[0] + source_rect[2], source_center.y())
            end = QPointF(target_rect[0] + target_rect[2], target_center.y())
        elif horizontal:
            start = QPointF(source_rect[0] + source_rect[2], source_center.y()) if target_center.x() >= source_center.x() else QPointF(source_rect[0], source_center.y())
            end = QPointF(target_rect[0], target_center.y()) if target_center.x() >= source_center.x() else QPointF(target_rect[0] + target_rect[2], target_center.y())
        else:
            start = QPointF(source_center.x(), source_rect[1] + source_rect[3]) if target_center.y() >= source_center.y() else QPointF(source_center.x(), source_rect[1])
            end = QPointF(target_center.x(), target_rect[1]) if target_center.y() >= source_center.y() else QPointF(target_center.x(), target_rect[1] + target_rect[3])
        spec = self._relation_visual_spec(relation_type)
        display_spec = relation_display_spec(relation_family, relation_type)
        if not relation_type.startswith("layout_"):
            spec = dict(spec)
            spec["color"] = display_spec.stroke
            spec["width"] = display_spec.line_width
            spec["start_arrow"] = display_spec.arrow_mode == "both"
            spec["end_arrow"] = display_spec.arrow_mode in {"end", "both"}
            if display_spec.dash:
                spec["style"] = Qt.PenStyle.DashLine
        pen = QPen(QColor(spec["color"]), float(spec["width"]))
        normalized_status = normalize_review_status(review_status, default="")
        if normalized_status == "needs_revision":
            pen.setStyle(Qt.PenStyle.DashLine)
        elif normalized_status == "pending":
            pen.setStyle(Qt.PenStyle.DotLine)
        else:
            pen.setStyle(spec["style"])
        actual_points = [QPointF(float(x), float(y)) for x, y in (route_points or [])]
        if len(actual_points) >= 2:
            start = actual_points[0]
            end = actual_points[-1]
            path = QPainterPath(start)
            for point in actual_points[1:]:
                path.lineTo(point)
        else:
            path = QPainterPath(start)
        if len(actual_points) >= 2:
            pass
        elif is_cycle_edge:
            control_x = max(start.x(), end.x()) + 92.0
            path.cubicTo(
                QPointF(control_x, start.y()),
                QPointF(control_x, end.y()),
                end,
            )
        elif horizontal:
            control_delta = max(42.0, abs(end.x() - start.x()) * 0.35)
            path.cubicTo(
                QPointF(start.x() + control_delta, start.y()),
                QPointF(end.x() - control_delta, end.y()),
                end,
            )
        else:
            control_delta = max(34.0, abs(end.y() - start.y()) * 0.35)
            path.cubicTo(
                QPointF(start.x(), start.y() + control_delta),
                QPointF(end.x(), end.y() - control_delta),
                end,
            )
        line = QGraphicsPathItem(path)
        line.setPen(pen)
        status_opacity = {
            "pending": 0.84,
            "needs_revision": 0.9,
            "rejected": 0.42,
        }.get(normalized_status, 1.0)
        line.setOpacity(float(spec["opacity"]) * status_opacity)
        line.setZValue(float(spec.get("z", 1.6)))
        line.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, bool(edge_id))
        line.setData(0, "edge")
        line.setData(1, edge_id)
        line.setToolTip(
            f"{display_spec.label} · {self._review_status_label(review_status)}\n关系 ID：{edge_id or '无'}"
        )
        self.graph_scene.addItem(line)
        source_arrow = getattr(route_geometry, "source_arrow", ())
        target_arrow = getattr(route_geometry, "target_arrow", ())
        if source_arrow or target_arrow:
            self._add_shared_arrow_polygon(source_arrow, QColor(spec["color"]))
            self._add_shared_arrow_polygon(target_arrow, QColor(spec["color"]))
        else:
            end_tail = actual_points[-2] if len(actual_points) >= 2 else start
            start_tail = actual_points[1] if len(actual_points) >= 2 else end
            self._add_arrowhead(end_tail, end, QColor(spec["color"]), at_end=bool(spec["end_arrow"]))
            self._add_arrowhead(start_tail, start, QColor(spec["color"]), at_end=bool(spec["start_arrow"]))
        label_visible = bool(getattr(label_geometry, "visible", True))
        if bool(spec["label"]) and label_visible:
            status_suffix = {
                "pending": " · 待审",
                "needs_revision": " · 待修",
                "rejected": " · 已拒绝",
            }.get(normalized_status, "")
            label_text = str(
                getattr(label_geometry, "text", "")
                or f"{display_spec.label}{status_suffix}"
            )
            label = QGraphicsSimpleTextItem(label_text)
            label.setBrush(QBrush(QColor("#334155")))
            label.setFont(QFont("Microsoft YaHei", 8, QFont.Weight.DemiBold))
            label_rect = label.boundingRect()
            planned_rect = getattr(label_geometry, "rect", None)
            if planned_rect is not None:
                label_x = float(planned_rect.x) + (float(planned_rect.width) - label_rect.width()) / 2
                label_y = float(planned_rect.y) + (float(planned_rect.height) - label_rect.height()) / 2
            else:
                label_x = (start.x() + end.x()) / 2 - label_rect.width() / 2
                label_y = (
                    (start.y() + end.y()) / 2
                    - label_rect.height()
                    - self._relation_label_offset(relation_type)
                )
            badge = QGraphicsRectItem(
                label_x - 7,
                label_y - 3,
                label_rect.width() + 14,
                label_rect.height() + 6,
            )
            badge.setBrush(QBrush(QColor("#fffdf8")))
            badge.setPen(QPen(QColor(spec["color"]), 0.8))
            badge.setOpacity(0.92)
            badge.setZValue(2.05)
            self.graph_scene.addItem(badge)
            label.setPos(label_x, label_y)
            label.setZValue(2.1)
            self.graph_scene.addItem(label)

    def _add_arrowhead(self, tail: QPointF, tip: QPointF, color: QColor, *, at_end: bool) -> None:
        if not at_end:
            return
        line = QLineF(tail, tip)
        if line.length() <= 0:
            return
        unit = QPointF(line.dx() / line.length(), line.dy() / line.length())
        normal = QPointF(-unit.y(), unit.x())
        arrow_length = 12.0
        arrow_width = 6.0
        base = QPointF(tip.x() - unit.x() * arrow_length, tip.y() - unit.y() * arrow_length)
        polygon = QPolygonF(
            [
                tip,
                QPointF(base.x() + normal.x() * arrow_width, base.y() + normal.y() * arrow_width),
                QPointF(base.x() - normal.x() * arrow_width, base.y() - normal.y() * arrow_width),
            ]
        )
        arrow = QGraphicsPolygonItem(polygon)
        arrow.setBrush(QBrush(color))
        arrow.setPen(QPen(color, 1.0))
        arrow.setZValue(1.8)
        self.graph_scene.addItem(arrow)

    def _add_shared_arrow_polygon(
        self,
        points: object,
        color: QColor,
    ) -> None:
        values = list(points or [])
        if len(values) < 3:
            return
        polygon = QPolygonF([QPointF(float(x), float(y)) for x, y in values])
        arrow = QGraphicsPolygonItem(polygon)
        arrow.setBrush(QBrush(color))
        arrow.setPen(QPen(color, 1.0))
        arrow.setZValue(1.8)
        self.graph_scene.addItem(arrow)

    @staticmethod
    def _relation_label(relation_type: str) -> str:
        labels = {
            "layout_main_path": "主路径",
            "layout_branch": "分支",
            "layout_auxiliary": "侧挂",
            "contains": "包含",
            "prerequisite": "前置",
            "progressive": "递进",
            "derives_to": "推导",
            "explains": "解释",
            "equivalent": "等价",
            "parallel": "并列",
            "contrast": "对比",
            "applies_to": "应用",
            "represented_by": "表征",
        }
        return labels.get(relation_type, relation_type)

    @staticmethod
    def _relation_label_offset(relation_type: str) -> float:
        if relation_type.startswith("layout_"):
            return 28.0
        if relation_type in {"prerequisite", "progressive", "derives_to"}:
            return 8.0
        return 14.0

    def _delete_selected_node(self) -> None:
        row = self.node_table.currentRow()
        if not (0 <= row < len(self.drafts)):
            QMessageBox.information(self, "未选择知识点", "请先在节点审查表中选择要归档的知识点。")
            return
        draft = self.drafts[row]
        node_id = draft.candidate_node_id
        if self.review_document is None:
            source_identity = (
                self.current_record.source_id
                if self.current_record is not None
                else self._current_graph_id()
            )
            self.review_document = self.review_service.create_review_document(
                self.drafts,
                source_identity=source_identity,
                analysis_run_id=self.current_analysis_run_id,
            )
        memberships = [
            relation
            for relation in self.review_document.relations
            if relation.relation_family == RELATION_FAMILY_MEMBERSHIP
            and relation.target_node_id == node_id
            and relation.lifecycle_state == "active"
        ]
        relations = [
            relation
            for relation in self.review_document.relations
            if node_id in {relation.source_node_id, relation.target_node_id}
            and relation.lifecycle_state == "active"
        ]
        occurrences = [
            occurrence
            for occurrence in self.review_document.occurrences
            if occurrence.node_id == node_id and occurrence.lifecycle_state == "active"
        ]
        message = (
            f"确定在当前审查文档中归档知识点“{draft.candidate_display_name}”吗？\n\n"
            f"受影响：教材归属 {len(memberships)} 处、出现记录 {len(occurrences)} 条、"
            f"关联关系 {len(relations)} 条。\n"
            "这是可撤销的逻辑归档；证据和审查历史会保留，不会物理删除文件或其他审查文档中的对象。"
        )
        if QMessageBox.question(self, "确认归档知识点", message) != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_snapshot(f"归档知识点：{draft.candidate_display_name}")
        session = ReviewDocumentSession(self.review_document)
        session.archive_node(
            node_id,
            expected_revision=self.review_document.metadata.revision,
            reason="desktop_archive_selected_node",
        )
        self.review_document = session.snapshot()
        self.drafts = session.draft_rows()
        self._remove_manual_position(node_id)
        self._refresh_review_after_status_change(synchronize_document=False)
        self.statusBar().showMessage(f"已归档节点：{draft.candidate_display_name}")

    def _delete_selected_relation(self) -> None:
        row = self.edge_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "未选择关系", "请先在关系审查表中选择要删除的关系。")
            return
        edge_id_item = self.edge_table.item(row, 1)
        edge_id = str(edge_id_item.data(Qt.ItemDataRole.UserRole) if edge_id_item else "")
        if not edge_id:
            QMessageBox.information(self, "未选择关系", "当前关系缺少可删除标识。")
            return
        if QMessageBox.question(self, "确认删除关系", "确定删除当前选中的关系吗？") != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_snapshot("删除关系")
        relation_id = self._review_relation_id_from_edge_id(edge_id)
        used_document_command = False
        if self.review_document is not None and relation_id:
            session = ReviewDocumentSession(self.review_document)
            result = session.archive_relations(
                [relation_id],
                expected_revision=self.review_document.metadata.revision,
                reason="desktop_delete_selected_relation",
            )
            self.review_document = session.snapshot()
            self.drafts = session.draft_rows()
            removed = result.changed
            used_document_command = True
        else:
            removed = self._remove_relation_by_edge_id(edge_id)
        if not removed:
            QMessageBox.information(self, "未删除关系", "未能在当前底稿中找到对应关系。")
            return
        self._refresh_review_after_status_change(
            synchronize_document=not used_document_command
        )
        self.statusBar().showMessage("已归档选中关系。")

    def _edit_selected_node(self) -> None:
        row = self.node_table.currentRow()
        if not (0 <= row < len(self.drafts)):
            QMessageBox.information(self, "未选择节点", "请先在节点审查表中选择要编辑的节点。")
            return
        draft = self.drafts[row]
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑知识点")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name_input = QLineEdit(draft.candidate_display_name)
        node_name_input = QLineEdit(draft.candidate_node_name)
        type_input = self._knowledge_type_combo()
        self._set_combo_data(type_input, normalize_node_type(draft.knowledge_type))
        parent_input = self._draft_combo(
            include_empty=True,
            empty_label="无父节点",
            exclude_node_id=draft.candidate_node_id,
        )
        self._set_combo_data(parent_input, draft.candidate_parent_node_id)
        status_input = self._plain_status_combo(default=draft.review_status)
        evidence_input = QTextEdit()
        evidence_input.setMinimumHeight(72)
        evidence_input.setPlainText(draft.source_text or self._first_anchor_text(draft))
        reason_input = QTextEdit()
        reason_input.setMinimumHeight(72)
        reason_input.setPlainText(draft.reasoning_summary)
        form.addRow("显示名称", name_input)
        form.addRow("规范名称", node_name_input)
        form.addRow("知识类型", type_input)
        form.addRow("父节点", parent_input)
        form.addRow("审查状态", status_input)
        form.addRow("证据说明", evidence_input)
        form.addRow("候选理由", reason_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        display_name = name_input.text().strip()
        if not display_name:
            QMessageBox.warning(self, "信息不完整", "请填写知识点显示名称。")
            return
        parent_id = str(parent_input.currentData() or "")
        if parent_id == draft.candidate_node_id:
            QMessageBox.warning(self, "父节点无效", "节点不能挂载到自身下面。")
            return
        parent_draft = self._draft_by_node_id(parent_id)
        status = self._combo_status_value(status_input)
        evidence_text = evidence_input.toPlainText().strip()
        self._push_undo_snapshot(f"编辑节点：{draft.candidate_display_name}")
        draft.candidate_display_name = display_name
        draft.candidate_node_name = node_name_input.text().strip() or draft.candidate_node_id
        draft.knowledge_type = normalize_node_type(type_input.currentData() or "concept")
        draft.candidate_parent_node_id = parent_id
        draft.candidate_parent_name = parent_draft.candidate_display_name if parent_draft else ""
        if parent_draft:
            draft.chapter = parent_draft.chapter
        draft.review_status = status
        draft.source_text = evidence_text
        draft.reasoning_summary = reason_input.toPlainText().strip()
        self._sync_parent_display_names(draft.candidate_node_id, display_name)
        self._sync_node_anchor(draft, evidence_text, status)
        self._refresh_review_after_status_change()
        self.statusBar().showMessage(f"已更新知识点：{display_name}")

    def _duplicate_selected_node(self) -> None:
        row = self.node_table.currentRow()
        if not (0 <= row < len(self.drafts)):
            QMessageBox.information(self, "未选择节点", "请先在节点审查表中选择要复制的节点。")
            return
        original = self.drafts[row]
        node_id = self._unique_manual_node_id(f"{original.candidate_display_name}_副本")
        duplicate = deepcopy(original)
        duplicate.candidate_node_id = node_id
        duplicate.draft_id = f"manual_{node_id}"
        duplicate.candidate_display_name = f"{original.candidate_display_name} 副本"
        duplicate.candidate_node_name = node_id
        duplicate.candidate_relations = []
        duplicate.candidate_prerequisites = []
        duplicate.extractor_source = "teacher_manual"
        duplicate.reasoning_summary = f"由“{original.candidate_display_name}”复制后待教师确认"
        duplicate.review_status = "pending"
        duplicate.review_notes = "teacher_manual_duplicate"
        for anchor in duplicate.evidence_anchors:
            anchor.anchor_id = f"manual_anchor_{node_id}_{len(anchor.anchor_text)}"
            anchor.target_ids = [node_id]
            anchor.review_status = "pending"
        self._push_undo_snapshot(f"复制节点：{original.candidate_display_name}")
        self.drafts.append(duplicate)
        self._place_manual_node_and_refresh(node_id)
        self.statusBar().showMessage(f"已复制知识点：{duplicate.candidate_display_name}")

    def _edit_selected_relation(self) -> None:
        edge_id = self._selected_edge_id()
        if not edge_id:
            QMessageBox.information(self, "未选择关系", "请先在关系审查表中选择要编辑的关系。")
            return
        relation = self._relation_details_by_edge_id(edge_id)
        if relation is None:
            QMessageBox.information(self, "未找到关系", "未能在当前底稿中找到对应关系。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑关系")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        source_input = self._draft_combo(include_empty=False)
        target_input = self._draft_combo(include_empty=False)
        relation_input = self._relation_type_combo()
        status_input = self._plain_status_combo(default=relation["status"])
        evidence_input = QTextEdit()
        evidence_input.setMinimumHeight(72)
        evidence_input.setPlainText(relation["evidence"])
        self._set_combo_data(source_input, relation["source"])
        self._set_combo_data(target_input, relation["target"])
        self._set_combo_data(relation_input, relation["relation_type"])
        form.addRow("起点节点", source_input)
        form.addRow("关系类型", relation_input)
        form.addRow("终点节点", target_input)
        form.addRow("审查状态", status_input)
        form.addRow("证据说明", evidence_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        source_id = str(source_input.currentData() or "")
        target_id = str(target_input.currentData() or "")
        if not source_id or not target_id or source_id == target_id:
            QMessageBox.warning(self, "关系无效", "请选择不同的起点和终点节点。")
            return
        source_draft = self._draft_by_node_id(source_id)
        if source_draft is None:
            QMessageBox.warning(self, "关系无效", "未找到起点节点。")
            return
        relation_type = normalize_relation_type(relation_input.currentData() or "explains")
        status = self._combo_status_value(status_input)
        evidence = evidence_input.toPlainText().strip()
        self._push_undo_snapshot("编辑关系")
        self.review_session.replace_relation(
            edge_id=edge_id,
            source_node_id=source_id,
            target_node_id=target_id,
            relation_type=relation_type,
            status=status,
            evidence=evidence,
        )
        self._refresh_review_after_status_change()
        self.statusBar().showMessage("已更新关系。")

    def _add_manual_node(self) -> None:
        if not self.current_record:
            QMessageBox.information(self, "暂无材料", "请先导入材料并生成候选图谱。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("新增知识点")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        name_input = QLineEdit()
        node_name_input = QLineEdit()
        type_input = self._knowledge_type_combo()
        parent_input = self._draft_combo(include_empty=True, empty_label="无父节点")
        source_input = self._draft_combo(include_empty=True, empty_label="不自动创建关系")
        relation_input = self._relation_type_combo()
        status_input = self._plain_status_combo(default="accepted")
        evidence_input = QTextEdit()
        evidence_input.setMinimumHeight(72)
        selected_node_id = self._selected_node_id()
        if selected_node_id:
            self._set_combo_data(parent_input, selected_node_id)
            self._set_combo_data(source_input, selected_node_id)
        form.addRow("显示名称", name_input)
        form.addRow("规范名称", node_name_input)
        form.addRow("知识类型", type_input)
        form.addRow("父节点", parent_input)
        form.addRow("关联来源", source_input)
        form.addRow("关联关系", relation_input)
        form.addRow("审查状态", status_input)
        form.addRow("证据说明", evidence_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        display_name = name_input.text().strip()
        if not display_name:
            QMessageBox.warning(self, "信息不完整", "请填写知识点显示名称。")
            return
        node_id = self._unique_manual_node_id(display_name)
        parent_id = str(parent_input.currentData() or "")
        parent_draft = self._draft_by_node_id(parent_id)
        source_id = str(source_input.currentData() or "")
        relation_type = normalize_relation_type(relation_input.currentData() or "explains")
        status = self._combo_status_value(status_input)
        evidence_text = evidence_input.toPlainText().strip()
        chapter = parent_draft.chapter if parent_draft else (self.scope_label_input.text().strip() or "manual_scope")
        anchor = self._manual_evidence_anchor(node_id, evidence_text, target_type="node") if evidence_text else None
        draft = DraftKnowledgeItemDTO(
            draft_id=f"manual_{node_id}",
            subject=self.subject_input.text().strip() or self.current_record.subject,
            grade=self.grade_input.text().strip() or self.current_record.grade,
            term=self.term_input.text().strip() or self.current_record.term,
            chapter=chapter,
            candidate_display_name=display_name,
            candidate_node_name=node_name_input.text().strip() or node_id,
            candidate_node_id=node_id,
            candidate_parent_name=parent_draft.candidate_display_name if parent_draft else "",
            candidate_parent_node_id=parent_id,
            candidate_relations=[],
            knowledge_type=normalize_node_type(type_input.currentData() or "concept"),
            cognitive_level="",
            education_stage=self.current_record.education_stage,
            grade_band=self.current_record.grade_band,
            subject_tags=list(self.current_record.subject_tags),
            source_id=self.current_record.source_id,
            source_path=self.current_record.source_path,
            source_format=self.current_record.source_format,
            source_document_type=self.current_record.source_document_type,
            source_text=evidence_text,
            source_location="teacher_manual",
            evidence_anchors=[anchor] if anchor else [],
            confidence=1.0,
            reasoning_summary="教师手动新增",
            extractor_source="teacher_manual",
            review_status=status,
            review_notes="teacher_manual",
        )
        if source_id:
            draft.candidate_relations.append(
                self._manual_relation_payload(
                    source_node_id=source_id,
                    target_node_id=node_id,
                    relation_type=relation_type,
                    status=status,
                    evidence=evidence_text,
                )
            )
        self._push_undo_snapshot(f"新增知识点：{display_name}")
        self.drafts.append(draft)
        self._place_manual_node_and_refresh(node_id)
        self.statusBar().showMessage(f"已新增知识点：{display_name}")

    def _add_manual_relation(self) -> None:
        if not self.drafts:
            QMessageBox.information(self, "暂无节点", "请先生成或新增知识点。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("新增关系")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        source_input = self._draft_combo(include_empty=False)
        target_input = self._draft_combo(include_empty=False)
        relation_input = self._relation_type_combo()
        status_input = self._plain_status_combo(default="accepted")
        evidence_input = QTextEdit()
        evidence_input.setMinimumHeight(72)
        selected_node_id = self._selected_node_id()
        if selected_node_id:
            self._set_combo_data(source_input, selected_node_id)
        form.addRow("起点节点", source_input)
        form.addRow("关系类型", relation_input)
        form.addRow("终点节点", target_input)
        form.addRow("审查状态", status_input)
        form.addRow("证据说明", evidence_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addLayout(form)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        source_id = str(source_input.currentData() or "")
        target_id = str(target_input.currentData() or "")
        if not source_id or not target_id or source_id == target_id:
            QMessageBox.warning(self, "关系无效", "请选择不同的起点和终点节点。")
            return
        source_draft = self._draft_by_node_id(source_id)
        if source_draft is None:
            QMessageBox.warning(self, "关系无效", "未找到起点节点。")
            return
        relation_type = normalize_relation_type(relation_input.currentData() or "explains")
        status = self._combo_status_value(status_input)
        evidence = evidence_input.toPlainText().strip()
        self._push_undo_snapshot("新增关系")
        self.review_session.add_or_update_relation(
            source_node_id=source_id,
            target_node_id=target_id,
            relation_type=relation_type,
            status=status,
            evidence=evidence,
        )
        self._refresh_review_after_status_change()
        self.statusBar().showMessage("已新增关系。")

    def _knowledge_type_combo(self) -> QComboBox:
        combo = QComboBox()
        for value in ["concept", "property", "rule", "method", "representation", "problem_type", "application"]:
            combo.addItem(self._knowledge_type_label(value), value)
        return combo

    def _relation_type_combo(self) -> QComboBox:
        combo = QComboBox()
        for value in ["contains", "prerequisite", "progressive", "derives_to", "explains", "equivalent", "parallel", "contrast", "applies_to", "represented_by"]:
            combo.addItem(self._relation_label(value), value)
        combo.setCurrentIndex(max(combo.findData("explains"), 0))
        return combo

    def _plain_status_combo(self, default: str = "accepted") -> QComboBox:
        combo = QComboBox()
        for option in STATUS_OPTIONS:
            combo.addItem(self._review_status_label(option), option)
        combo.setCurrentIndex(max(combo.findData(normalize_review_status(default)), 0))
        return combo

    def _draft_combo(
        self,
        *,
        include_empty: bool,
        empty_label: str = "",
        exclude_node_id: str = "",
    ) -> QComboBox:
        combo = QComboBox()
        if include_empty:
            combo.addItem(empty_label, "")
        for draft in self.drafts:
            if exclude_node_id and draft.candidate_node_id == exclude_node_id:
                continue
            combo.addItem(draft.candidate_display_name, draft.candidate_node_id)
        return combo

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _selected_node_id(self) -> str:
        row = self.node_table.currentRow()
        if 0 <= row < len(self.drafts):
            return self.drafts[row].candidate_node_id
        return ""

    def _selected_node_status(self) -> str:
        row = self.node_table.currentRow()
        if 0 <= row < len(self.drafts):
            return normalize_review_status(self.drafts[row].review_status)
        return ""

    def _selected_edge_id(self) -> str:
        row = self.edge_table.currentRow()
        if row < 0:
            return ""
        return self._edge_id_for_table_row(row)

    def _selected_relation_status(self) -> str:
        edge_id = self._selected_edge_id()
        relation = self._relation_details_by_edge_id(edge_id) if edge_id else None
        return normalize_review_status(relation["status"]) if relation else ""

    def _edge_id_for_table_row(self, row: int) -> str:
        if row < 0:
            return ""
        edge_id_item = self.edge_table.item(row, 1)
        if edge_id_item is None:
            return ""
        return str(edge_id_item.data(Qt.ItemDataRole.UserRole) or "")

    def _draft_by_node_id(self, node_id: str) -> DraftKnowledgeItemDTO | None:
        return self.review_session.draft_by_node_id(node_id)

    @staticmethod
    def _first_anchor_text(draft: DraftKnowledgeItemDTO) -> str:
        if draft.evidence_anchors:
            return draft.evidence_anchors[0].anchor_text
        return ""

    def _sync_node_anchor(self, draft: DraftKnowledgeItemDTO, evidence_text: str, status: str) -> None:
        normalized_status = normalize_review_status(status)
        if draft.evidence_anchors:
            anchor = draft.evidence_anchors[0]
            anchor.anchor_text = evidence_text
            anchor.target_ids = [draft.candidate_node_id]
            anchor.review_status = normalized_status
            for extra_anchor in draft.evidence_anchors[1:]:
                extra_anchor.review_status = normalized_status
            return
        if evidence_text:
            anchor = self._manual_evidence_anchor(draft.candidate_node_id, evidence_text, target_type="node")
            anchor.review_status = normalized_status
            draft.evidence_anchors = [anchor]

    def _sync_parent_display_names(self, parent_node_id: str, parent_name: str) -> None:
        self.review_session.sync_parent_display_names(parent_node_id, parent_name)

    def _relation_details_by_edge_id(self, edge_id: str) -> dict[str, str] | None:
        return self.review_session.relation_details_by_edge_id(
            edge_id,
            workbook=self.workbook,
        )

    def _node_id_from_inline_text(self, text: str) -> str:
        value = text.strip()
        if not value:
            return ""
        for draft in self.drafts:
            if value == draft.candidate_node_id:
                return draft.candidate_node_id
        lowered = value.lower()
        for draft in self.drafts:
            if lowered in {
                draft.candidate_display_name.strip().lower(),
                draft.candidate_node_name.strip().lower(),
            }:
                return draft.candidate_node_id
        return ""

    @staticmethod
    def _knowledge_type_from_inline_text(text: str) -> str:
        value = text.strip()
        if not value:
            return ""
        reverse_labels = {label: key for key, label in KNOWLEDGE_TYPE_LABELS.items()}
        if value in reverse_labels:
            return normalize_node_type(reverse_labels[value], default="")
        return normalize_node_type(value, default="")

    def _relation_type_from_inline_text(self, text: str) -> str:
        value = text.strip()
        if not value:
            return ""
        relation_values = [
            "prerequisite",
            "progressive",
            "derives_to",
            "explains",
            "equivalent",
            "parallel",
            "contrast",
            "applies_to",
            "represented_by",
        ]
        reverse_labels = {self._relation_label(item): item for item in relation_values}
        if value in reverse_labels:
            return reverse_labels[value]
        return normalize_relation_type(value, default="")

    def _apply_inline_relation_update(
        self,
        *,
        edge_id: str,
        source_id: str,
        target_id: str,
        relation_type: str,
        status: str,
        evidence: str,
    ) -> None:
        self.review_session.replace_relation(
            edge_id=edge_id,
            source_node_id=source_id,
            target_node_id=target_id,
            relation_type=relation_type,
            status=status,
            evidence=evidence,
        )

    def _unique_manual_node_id(self, display_name: str) -> str:
        prefix = "_".join(
            [
                self._safe_identifier(self.subject_input.text()) or "subject",
                self._safe_identifier(self.grade_input.text()) or "grade",
                self._safe_identifier(self.term_input.text()) or "term",
            ]
        )
        used = {draft.candidate_node_id for draft in self.drafts}
        base = self._safe_identifier(display_name) or "manual_node"
        index = 1
        while True:
            node_id = f"{prefix}_manual_{base}_{index}"
            if node_id not in used:
                return node_id
            index += 1

    @staticmethod
    def _safe_identifier(value: str) -> str:
        normalized = []
        for char in value.strip().lower():
            if char.isalnum():
                normalized.append(char)
            elif normalized and normalized[-1] != "_":
                normalized.append("_")
        return "".join(normalized).strip("_")[:48]

    def _manual_evidence_anchor(self, node_id: str, evidence_text: str, *, target_type: str) -> EvidenceAnchorDTO:
        source_id = self.current_record.source_id if self.current_record else self.source_id_input.text().strip()
        return EvidenceAnchorDTO(
            anchor_id=f"manual_anchor_{node_id}_{len(evidence_text)}",
            source_id=source_id,
            source_path=str(self.source_path or ""),
            source_format=self.source_format,
            source_document_type="electronic_textbook",
            source_location="teacher_manual",
            anchor_text=evidence_text,
            target_type=target_type,
            target_ids=[node_id],
            confidence=1.0,
            created_by="teacher_manual",
            review_status="accepted",
        )

    @staticmethod
    def _manual_relation_payload(
        *,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        status: str,
        evidence: str,
    ) -> dict[str, object]:
        return WorkbenchReviewSession.manual_relation_payload(
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation_type=relation_type,
            status=status,
            evidence=evidence,
        )

    def _find_candidate_relation(
        self,
        draft: DraftKnowledgeItemDTO,
        source_node_id: str,
        relation_type: str,
        target_node_id: str,
    ) -> dict[str, object] | None:
        return self.review_session.find_candidate_relation(
            draft,
            source_node_id,
            relation_type,
            target_node_id,
        )

    def _count_relations_for_node(self, node_id: str) -> int:
        return self.review_session.count_relations_for_node(node_id)

    def _remove_relation_by_edge_id(self, edge_id: str) -> bool:
        return self.review_session.remove_relation_by_edge_id(edge_id)

    def _review_relation_id_from_edge_id(self, edge_id: str) -> str:
        if self.review_document is None:
            return ""
        normalized_id = edge_id.removeprefix("edge:")
        for relation in self.review_document.relations:
            if relation.relation_id == normalized_id:
                return relation.relation_id
            if self._edge_status_key(
                relation.source_node_id,
                relation.relation_type,
                relation.target_node_id,
            ) == edge_id:
                return relation.relation_id
        return ""

    def _remove_manual_position(self, node_id: str) -> None:
        self.graph_node_positions.pop(node_id, None)
        graph_id = self._current_graph_id()
        view_config = self._load_g6_view_config(graph_id)
        manual_positions = dict(view_config.get("manual_positions", {}))
        if node_id not in manual_positions:
            return
        manual_positions.pop(node_id, None)
        view_config["manual_positions"] = manual_positions
        self.g6_view_config = view_config
        self._persist_graph_view_config(graph_id, view_config)

    def _place_manual_node_and_refresh(self, node_id: str) -> None:
        if self.g6_graph_view is None or not self.g6_graph_loaded:
            self._set_manual_node_position(node_id, self._fallback_manual_node_position())
            self._refresh_review_after_status_change()
            return
        script = (
            "window.getGraphReviewViewportCenter"
            " ? window.getGraphReviewViewportCenter()"
            " : null;"
        )
        self.g6_graph_view.page().runJavaScript(
            script,
            lambda result, target_id=node_id: self._finish_manual_node_placement(target_id, result),
        )

    def _finish_manual_node_placement(self, node_id: str, result: object) -> None:
        position = self._point_from_js_result(result) or self._fallback_manual_node_position()
        self._set_manual_node_position(node_id, position)
        self._refresh_review_after_status_change()

    @staticmethod
    def _point_from_js_result(result: object) -> tuple[float, float] | None:
        if not isinstance(result, dict):
            return None
        try:
            return (float(result["x"]), float(result["y"]))
        except (KeyError, TypeError, ValueError):
            return None

    def _fallback_manual_node_position(self) -> tuple[float, float]:
        selected_node_id = self._selected_node_id()
        view_config = self._load_g6_view_config(self._current_graph_id())
        manual_positions = self._clean_g6_manual_positions(
            view_config.get("manual_positions", {})
        )
        if selected_node_id in manual_positions:
            x = manual_positions[selected_node_id]["x"]
            y = manual_positions[selected_node_id]["y"]
            return (x + 260.0, y + 80.0)
        if manual_positions:
            last_position = next(reversed(manual_positions.values()))
            x, y = last_position["x"], last_position["y"]
            return (x + 260.0, y + 80.0)
        return (520.0, 160.0)

    def _set_manual_node_position(self, node_id: str, position: tuple[float, float]) -> None:
        graph_id = self._current_graph_id()
        view_config = self._load_g6_view_config(graph_id)
        manual_positions = dict(view_config.get("manual_positions", {}))
        manual_positions[node_id] = {"x": float(position[0]), "y": float(position[1])}
        view_config.update(
            {
                "graph_id": graph_id,
                "layout_revision": GRAPH_LAYOUT_VERSION,
                "layout_mode": view_config.get("layout_mode") or (
                    "knowledge_relations_v6"
                    if self.graph_view_mode == VIEW_MODE_RELATIONS
                    else "textbook_recursive_v6"
                ),
                "view_mode": self.graph_view_mode,
                "storage_mode": "desktop_file",
                "show_auxiliary_edges": view_config.get("show_auxiliary_edges", False),
                "manual_positions": manual_positions,
            }
        )
        self.g6_view_config = view_config
        GRAPH_REVIEW_VIEW_STORAGE.mkdir(parents=True, exist_ok=True)
        self._persist_graph_view_config(graph_id, view_config)

    def _push_undo_snapshot(self, label: str) -> None:
        self.review_session.push_snapshot(label)
        if self.review_document is not None:
            self._review_document_undo_stack.append(
                (label, deepcopy(self.review_document))
            )
            self._review_document_undo_stack = self._review_document_undo_stack[-12:]
        self._update_review_toolbar_visibility()

    def _undo_last_review_operation(self) -> None:
        if not self.undo_stack:
            QMessageBox.information(self, "暂无可撤销操作", "当前没有可撤销的审查操作。")
            return
        label = self.review_session.undo()
        if label is None:
            return
        if self.review_document is not None and self._review_document_undo_stack:
            snapshot_label, restored = self._review_document_undo_stack.pop()
            current = self.review_document
            restored.metadata.revision = current.metadata.revision + 1
            restored.metadata.updated_at = datetime.now().astimezone().isoformat(
                timespec="seconds"
            )
            restored.review_history = list(current.review_history) + [
                ReviewDecisionDTO(
                    decision_id=f"decision_{uuid4().hex}",
                    object_type="document",
                    object_id=current.metadata.document_id,
                    object_revision=restored.metadata.revision,
                    action="undo",
                    before={"business_fingerprint": current.business_fingerprint()},
                    after={"business_fingerprint": restored.business_fingerprint()},
                    reason=f"desktop_undo:{snapshot_label}",
                    created_at=restored.metadata.updated_at,
                )
            ]
            self.review_document = restored
            self.drafts = document_to_legacy_drafts(restored)
        self._restore_g6_view_config_snapshot()
        self._refresh_review_after_status_change()
        self.statusBar().showMessage(f"已撤销：{label}")

    def _restore_g6_view_config_snapshot(self) -> None:
        graph_id = self._current_graph_id()
        view_config = dict(self.g6_view_config or {})
        view_config["graph_id"] = graph_id
        view_config["layout_revision"] = GRAPH_LAYOUT_VERSION
        view_config.setdefault(
            "layout_mode",
            "knowledge_relations_v6"
            if self.graph_view_mode == VIEW_MODE_RELATIONS
            else "textbook_recursive_v6",
        )
        view_config["view_mode"] = self.graph_view_mode
        view_config["storage_mode"] = "desktop_file"
        view_config["manual_positions"] = self._clean_g6_manual_positions(
            view_config.get("manual_positions", {})
        )
        GRAPH_REVIEW_VIEW_STORAGE.mkdir(parents=True, exist_ok=True)
        self._persist_graph_view_config(graph_id, view_config)

    def _locate_selected_node_in_graph(self) -> None:
        node_id = self._selected_node_id()
        if not node_id:
            QMessageBox.information(self, "未选择节点", "请先在节点审查表中选择要定位的节点。")
            return
        self._focus_g6_item(node_id, "node")

    def _locate_selected_relation_in_graph(self) -> None:
        edge_id = self._selected_edge_id()
        if not edge_id:
            QMessageBox.information(self, "未选择关系", "请先在关系审查表中选择要定位的关系。")
            return
        self._focus_g6_item(edge_id, "edge")

    def _focus_g6_item(self, item_id: str, item_type: str) -> None:
        if self.g6_graph_view is None or not self.g6_graph_loaded:
            self.statusBar().showMessage("G6 图谱尚未加载完成，暂时无法定位。")
            return
        if self.review_document is not None:
            projection = self.review_service.build_review_render_bundle(
                self.review_document,
                renderer="g6",
                view_mode=self.graph_view_mode,
            ).projection
            if item_type == "node":
                instance = next(
                    (node for node in projection.node_instances if node.node_id == item_id),
                    None,
                )
                if instance is not None:
                    item_id = instance.view_id
            elif item_type == "edge":
                relation = next(
                    (
                        edge
                        for edge in self.review_document.relations
                        if self._edge_status_key(
                            edge.source_node_id,
                            edge.relation_type,
                            edge.target_node_id,
                        )
                        == item_id
                        or edge.relation_id == item_id
                    ),
                    None,
                )
                if relation is not None:
                    item_id = f"edge:{relation.relation_id}"
        script = (
            "window.focusGraphReviewItem"
            f" && window.focusGraphReviewItem({json.dumps(item_id, ensure_ascii=False)}, "
            f"{json.dumps(item_type, ensure_ascii=False)});"
        )
        self.g6_graph_view.page().runJavaScript(script)
        self.statusBar().showMessage("已定位到图谱对象。")

    def _delete_rejected_nodes(self) -> None:
        rejected = [
            draft
            for draft in self.drafts
            if normalize_review_status(draft.review_status) == "rejected"
        ]
        if not rejected:
            QMessageBox.information(self, "暂无已拒绝节点", "当前没有可清理的已拒绝节点。")
            return
        if QMessageBox.question(
            self,
            "确认清理已拒绝节点",
            f"确定删除 {len(rejected)} 个已拒绝节点吗？相关关系会同步移除。",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_snapshot("清理已拒绝节点")
        rejected_ids = {draft.candidate_node_id for draft in rejected}
        if self.review_document is not None:
            session = ReviewDocumentSession(self.review_document)
            session.archive_nodes(
                sorted(rejected_ids),
                expected_revision=self.review_document.metadata.revision,
                reason="desktop_archive_rejected_nodes",
            )
            self.review_document = session.snapshot()
            self.drafts = session.draft_rows()
        else:
            for node_id in rejected_ids:
                self.review_session.delete_node(node_id)
        for node_id in rejected_ids:
            self._remove_manual_position(node_id)
        self._refresh_review_after_status_change(synchronize_document=False)
        self.statusBar().showMessage(f"已归档 {len(rejected)} 个已拒绝节点。")

    def _delete_rejected_relations(self) -> None:
        edge_ids = self._rejected_relation_edge_ids()
        if not edge_ids:
            QMessageBox.information(self, "暂无已拒绝关系", "当前没有可清理的已拒绝关系。")
            return
        if QMessageBox.question(
            self,
            "确认清理已拒绝关系",
            f"确定删除 {len(edge_ids)} 条已拒绝关系吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        self._push_undo_snapshot("清理已拒绝关系")
        relation_ids = [
            relation_id
            for edge_id in edge_ids
            if (relation_id := self._review_relation_id_from_edge_id(edge_id))
        ]
        used_document_command = False
        if self.review_document is not None and len(relation_ids) == len(edge_ids):
            session = ReviewDocumentSession(self.review_document)
            result = session.archive_relations(
                relation_ids,
                expected_revision=self.review_document.metadata.revision,
                reason="desktop_archive_rejected_relations",
            )
            self.review_document = session.snapshot()
            self.drafts = session.draft_rows()
            removed_count = len(relation_ids) if result.changed else 0
            used_document_command = True
        else:
            removed_count = sum(
                1 for edge_id in edge_ids if self._remove_relation_by_edge_id(edge_id)
            )
        self._refresh_review_after_status_change(
            synchronize_document=not used_document_command
        )
        self.statusBar().showMessage(f"已归档 {removed_count} 条已拒绝关系。")

    def _rejected_relation_edge_ids(self) -> list[str]:
        return self.review_session.rejected_relation_edge_ids(self.workbook)

    def _accept_pending_nodes(self) -> None:
        if self._pending_node_count():
            self._push_undo_snapshot("通过待审查节点")
        count = self._set_pending_nodes_accepted()
        if count:
            self._refresh_review_after_status_change()
        self.statusBar().showMessage(f"已通过 {count} 个待审查节点。")

    def _accept_current_chapter_structure(self) -> None:
        selected_node_id = self._selected_node_id()
        if not selected_node_id:
            QMessageBox.information(self, "请选择章节", "请先在节点表中选择当前章节或其下属小节。")
            return
        if self.review_session.pending_structure_relation_count(self.workbook):
            self._push_undo_snapshot("通过当前章节结构")
        result = self.review_session.accept_catalog_structure(
            self.workbook,
            selected_node_id=selected_node_id,
        )
        if result.changed:
            self._refresh_review_after_status_change()
        self.statusBar().showMessage(
            f"已通过当前章节结构：{result.node_count} 个节点、"
            f"{result.relation_count} 条包含关系；跳过 {result.skipped_issue_count} 个结构冲突。"
        )

    def _accept_all_catalog_structure(self) -> None:
        if self.review_session.pending_structure_relation_count(self.workbook):
            self._push_undo_snapshot("通过全部目录结构")
        result = self.review_session.accept_catalog_structure(self.workbook)
        if result.changed:
            self._refresh_review_after_status_change()
        self.statusBar().showMessage(
            f"已通过全部目录结构：{result.node_count} 个节点、"
            f"{result.relation_count} 条包含关系；跳过 {result.skipped_issue_count} 个结构冲突。"
        )

    def _accept_pending_relations(self) -> None:
        if self._pending_relation_count():
            self._push_undo_snapshot("通过待审查关系")
        count = self._set_pending_relations_accepted()
        if count:
            self._refresh_review_after_status_change()
        self.statusBar().showMessage(f"已通过 {count} 条待审查关系。")

    def _accept_all_pending(self) -> None:
        if self._pending_node_count() or self._pending_relation_count():
            self._push_undo_snapshot("通过全部待审查")
        node_count = self._set_pending_nodes_accepted()
        relation_count = self._set_pending_relations_accepted()
        if node_count or relation_count:
            self._refresh_review_after_status_change()
        self.statusBar().showMessage(
            f"已通过 {node_count} 个待审查节点、{relation_count} 条待审查关系。"
        )

    def _pending_node_count(self) -> int:
        return self.review_session.pending_node_count()

    def _pending_relation_count(self) -> int:
        return self.review_session.pending_relation_count(self.workbook)

    def _set_pending_nodes_accepted(self) -> int:
        return self.review_session.accept_pending_nodes()

    def _set_pending_relations_accepted(self) -> int:
        return self.review_session.accept_pending_relations(self.workbook)

    def _render_tables(self) -> None:
        selected_node_id = self._selected_node_id()
        selected_edge_id = self._selected_edge_id()
        self.node_table.blockSignals(True)
        self.edge_table.blockSignals(True)
        self.quality_table.blockSignals(True)
        self._render_node_table()
        self._render_edge_table()
        self._render_anchor_table()
        self._render_quality_table()
        self.node_table.blockSignals(False)
        self.edge_table.blockSignals(False)
        self.quality_table.blockSignals(False)
        self._restore_table_selection(selected_node_id=selected_node_id, selected_edge_id=selected_edge_id)
        self._update_review_toolbar_visibility()
        self._update_summary_badges()

    def _restore_table_selection(self, *, selected_node_id: str, selected_edge_id: str) -> None:
        if selected_node_id:
            for row, draft in enumerate(self.drafts):
                if draft.candidate_node_id == selected_node_id:
                    self.node_table.setCurrentCell(row, 0)
                    self.node_table.selectRow(row)
                    break
        if selected_edge_id:
            for row in range(self.edge_table.rowCount()):
                if self._edge_id_for_table_row(row) == selected_edge_id:
                    self.edge_table.setCurrentCell(row, 0)
                    self.edge_table.selectRow(row)
                    break

    def _render_node_table(self) -> None:
        headers = ["节点", "类型", "状态", "置信度", "候选理由"]
        self.node_table.setColumnCount(len(headers))
        self.node_table.setHorizontalHeaderLabels(headers)
        self.node_table.setRowCount(len(self.drafts))
        self.node_inline_action_widgets = []
        for row, draft in enumerate(self.drafts):
            self.node_table.setCellWidget(row, 0, self._node_action_cell(row, draft))
            self.node_table.setCellWidget(row, 1, self._knowledge_type_selector(row, draft.knowledge_type))
            self.node_table.setCellWidget(row, 2, self._status_combo(draft.review_status))
            self.node_table.setItem(row, 3, self._item(f"{draft.confidence:.2f}"))
            self.node_table.setItem(row, 4, self._item(draft.reasoning_summary))
        self.node_table.setToolTip("可快速修改：节点名称可直接编辑，类型和状态使用下拉框，置信度和候选理由可双击编辑。")

    def _node_action_cell(self, row: int, draft: DraftKnowledgeItemDTO) -> QWidget:
        cell = InlineActionCell(row, self._select_node_row)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)
        editor = InlineLineEdit(row, self._select_node_row, draft.candidate_display_name)
        editor.setToolTip("可直接修改节点名称，回车或离开输入框后生效。")
        editor.setMinimumWidth(80)
        editor.setStyleSheet(
            "QLineEdit { border: 0; background: transparent; padding: 2px 0; }"
            "QLineEdit:focus { background: #f8fafc; border: 1px solid #bae6fd; border-radius: 4px; }"
        )
        editor.editingFinished.connect(
            lambda target_row=row, widget=editor: self._node_name_editor_finished(target_row, widget)
        )
        layout.addWidget(editor, 1)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(2)
        for text, tooltip, callback in [
            ("编", "编辑节点", self._edit_selected_node),
            ("档", "归档知识点（保留证据和历史，可撤销）", self._delete_selected_node),
            ("复", "复制节点", self._duplicate_selected_node),
            ("定", "定位到图谱", self._locate_selected_node_in_graph),
        ]:
            button = self._inline_action_button(text, tooltip)
            button.clicked.connect(
                lambda _checked=False, target_row=row, action=callback: self._run_node_row_action(target_row, action)
            )
            actions_layout.addWidget(button)
        actions.setVisible(False)
        self.node_inline_action_widgets.append(actions)
        layout.addWidget(actions)
        return cell

    def _inline_action_button(self, text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(24, 22)
        button.setProperty("role", "inline")
        return button

    def _select_node_row(self, row: int) -> None:
        if 0 <= row < self.node_table.rowCount():
            self.node_table.setCurrentCell(row, 0)
            self.node_table.selectRow(row)
            self._update_review_toolbar_visibility()

    def _run_node_row_action(self, row: int, action: Any) -> None:
        self._select_node_row(row)
        action()

    def _node_name_editor_finished(self, row: int, editor: QLineEdit) -> None:
        if not (0 <= row < len(self.drafts)):
            return
        value = editor.text().strip()
        draft = self.drafts[row]
        if not value:
            QMessageBox.warning(self, "节点名称无效", "节点名称不能为空。")
            editor.setText(draft.candidate_display_name)
            return
        if value == draft.candidate_display_name:
            return
        self._push_undo_snapshot("行内编辑节点名称")
        draft.candidate_display_name = value
        self._sync_parent_display_names(draft.candidate_node_id, value)
        self._refresh_review_after_status_change()

    def _knowledge_type_selector(self, row: int, knowledge_type: str) -> QComboBox:
        combo = self._knowledge_type_combo()
        self._set_combo_data(combo, normalize_node_type(knowledge_type))
        combo.setToolTip("点击直接切换知识点类型")
        combo.currentIndexChanged.connect(
            lambda _index, target_row=row, widget=combo: self._node_type_combo_changed(target_row, widget)
        )
        return combo

    def _render_edge_table(self) -> None:
        edges: list[FormalEdgeDTO] = []
        node_names = {draft.candidate_node_id: draft.candidate_display_name for draft in self.drafts}
        if self.workbook:
            edges.extend(self.workbook.edges)
        headers = ["起点", "关系", "终点", "状态", "证据"]
        self.edge_table.setColumnCount(len(headers))
        self.edge_table.setHorizontalHeaderLabels(headers)
        self.edge_table.setRowCount(len(edges))
        self.edge_inline_action_widgets = []
        for row, edge in enumerate(edges):
            edge_id = self._edge_status_key(edge.source_node_id, edge.relation_type, edge.target_node_id)
            status = self.relation_status_overrides.get(edge_id, normalize_review_status(edge.review_status))
            self.edge_table.setItem(row, 0, self._item(node_names.get(edge.source_node_id, edge.source_node_id), edge.source_node_id))
            self.edge_table.setItem(row, 1, self._item(self._relation_label(edge.relation_type), edge_id))
            self.edge_table.setItem(row, 2, self._item(node_names.get(edge.target_node_id, edge.target_node_id), edge.target_node_id))
            self.edge_table.setCellWidget(row, 0, self._edge_source_action_cell(row, edge))
            self.edge_table.setCellWidget(row, 1, self._relation_type_selector(row, edge_id, edge.relation_type))
            self.edge_table.setCellWidget(row, 2, self._edge_target_selector(row, edge_id, edge.target_node_id))
            self.edge_table.setCellWidget(row, 3, self._status_combo(status))
            self.edge_table.setItem(row, 4, self._item(edge.relation_evidence))
        self.edge_table.setToolTip("可快速修改：起点、关系、终点和状态使用下拉框，证据可双击编辑。")

    def _edge_source_action_cell(self, row: int, edge: FormalEdgeDTO) -> QWidget:
        cell = InlineActionCell(row, self._select_edge_row)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)
        selector = self._draft_selector(edge.source_node_id)
        selector.setToolTip("点击直接切换关系起点")
        selector.currentIndexChanged.connect(
            lambda _index, target_row=row, widget=selector: self._edge_source_combo_changed(target_row, widget)
        )
        layout.addWidget(selector, 1)
        actions = QWidget()
        actions_layout = QHBoxLayout(actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(2)
        for text, tooltip, callback in [
            ("编", "编辑关系", self._edit_selected_relation),
            ("删", "删除关系", self._delete_selected_relation),
            ("定", "定位到图谱", self._locate_selected_relation_in_graph),
        ]:
            button = self._inline_action_button(text, tooltip)
            button.clicked.connect(
                lambda _checked=False, target_row=row, action=callback: self._run_edge_row_action(target_row, action)
            )
            actions_layout.addWidget(button)
        actions.setVisible(False)
        self.edge_inline_action_widgets.append(actions)
        layout.addWidget(actions)
        return cell

    def _relation_type_selector(self, row: int, edge_id: str, relation_type: str) -> QComboBox:
        combo = self._relation_type_combo()
        self._set_combo_data(combo, normalize_relation_type(relation_type))
        combo.setToolTip("点击直接切换关系类型")
        combo.currentIndexChanged.connect(
            lambda _index, target_row=row, target_edge_id=edge_id, widget=combo: self._edge_relation_type_combo_changed(
                target_row, target_edge_id, widget
            )
        )
        return combo

    def _edge_target_selector(self, row: int, edge_id: str, target_node_id: str) -> QComboBox:
        combo = self._draft_selector(target_node_id)
        combo.setToolTip("点击直接切换关系终点")
        combo.currentIndexChanged.connect(
            lambda _index, target_row=row, target_edge_id=edge_id, widget=combo: self._edge_target_combo_changed(
                target_row, target_edge_id, widget
            )
        )
        return combo

    def _draft_selector(self, selected_node_id: str) -> QComboBox:
        combo = self._draft_combo(include_empty=False)
        self._set_combo_data(combo, selected_node_id)
        return combo

    def _select_edge_row(self, row: int) -> None:
        if 0 <= row < self.edge_table.rowCount():
            self.edge_table.setCurrentCell(row, 0)
            self.edge_table.selectRow(row)
            self._update_review_toolbar_visibility()

    def _run_edge_row_action(self, row: int, action: Any) -> None:
        self._select_edge_row(row)
        action()

    def _render_anchor_table(self) -> None:
        anchors = []
        for draft in self.drafts:
            for anchor in draft.evidence_anchors:
                anchors.append((draft, anchor))
        headers = ["节点", "位置", "状态", "证据文本"]
        self.anchor_table.setColumnCount(len(headers))
        self.anchor_table.setHorizontalHeaderLabels(headers)
        self.anchor_table.setRowCount(len(anchors))
        for row, (draft, anchor) in enumerate(anchors):
            self.anchor_table.setItem(row, 0, self._item(draft.candidate_display_name, anchor.anchor_id, editable=False))
            self.anchor_table.setItem(row, 1, self._item(anchor.source_location, editable=False))
            self.anchor_table.setItem(row, 2, self._status_item(anchor.review_status))
            self.anchor_table.setItem(row, 3, self._item(anchor.anchor_text, editable=False))

    def _render_quality_table(self) -> None:
        headers = ["级别", "问题类型", "关联对象", "说明"]
        self.quality_table.setColumnCount(len(headers))
        self.quality_table.setHorizontalHeaderLabels(headers)
        if not self.drafts:
            self.current_quality_report = None
            self.quality_table.setRowCount(0)
            self.quality_summary_label.setText("导入或生成图谱后，可在这里检查结构质量。")
            return
        if self.review_document is not None:
            document_report = self.review_service.check_review_document(
                self.review_document
            )
            node_ids = {item.node_id for item in self.review_document.nodes}
            relation_ids = {
                item.relation_id for item in self.review_document.relations
            }
            issues = []
            for item in document_report.issues:
                object_id = next(iter(item.object_ids), "")
                issues.append(
                    GraphQualityIssue(
                        severity=item.severity,
                        code=f"{item.dimension}:{item.code}",
                        message=item.message,
                        node_id=object_id if object_id in node_ids else "",
                        edge_id=object_id if object_id in relation_ids else "",
                        details={"object_ids": list(item.object_ids)},
                    )
                )
            report = GraphQualityReport(
                issues=issues,
                node_count=len(self.review_document.active_nodes()),
                edge_count=len(self.review_document.active_relations()),
                accepted_node_count=sum(
                    normalize_review_status(item.review_status) == "accepted"
                    for item in self.review_document.active_nodes()
                ),
                accepted_edge_count=sum(
                    normalize_review_status(item.review_status) == "accepted"
                    for item in self.review_document.active_relations()
                ),
                engine="review_document_v2",
            )
        else:
            report = self.review_service.check_quality(self.drafts)
        self.current_quality_report = report
        self.quality_summary_label.setText(
            f"{report.summary_text()} 已通过节点 {report.accepted_node_count} 个，"
            f"已通过关系 {report.accepted_edge_count} 条。"
        )
        self.quality_table.setRowCount(len(report.issues))
        for row, issue in enumerate(report.issues):
            target = issue.node_id or issue.edge_id or "-"
            severity_item = self._quality_severity_item(issue.severity)
            code_item = self._item(issue.code, str(row), editable=False)
            target_item = self._item(target, str(row), editable=False)
            message_item = self._item(issue.message, str(row), editable=False)
            for item in [severity_item, code_item, target_item, message_item]:
                item.setData(Qt.ItemDataRole.UserRole, row)
            self.quality_table.setItem(row, 0, severity_item)
            self.quality_table.setItem(row, 1, code_item)
            self.quality_table.setItem(row, 2, target_item)
            self.quality_table.setItem(row, 3, message_item)
        if not report.issues:
            self.quality_summary_label.setText("质量检查通过：未发现需要处理的问题。")

    def _run_quality_check(self) -> None:
        self.quality_table.blockSignals(True)
        self._render_quality_table()
        self.quality_table.blockSignals(False)
        self._update_summary_badges()
        if self.current_quality_report is None:
            self.statusBar().showMessage("暂无可检查的图谱数据。")
            return
        self.statusBar().showMessage(self.current_quality_report.summary_text())

    def _selected_quality_issue(self) -> GraphQualityIssue | None:
        if self.current_quality_report is None:
            return None
        row = self.quality_table.currentRow()
        if not (0 <= row < len(self.current_quality_report.issues)):
            return None
        return self.current_quality_report.issues[row]

    def _locate_selected_quality_issue(self) -> None:
        issue = self._selected_quality_issue()
        if issue is None:
            QMessageBox.information(self, "未选择问题", "请先在质量检查表中选择一条问题记录。")
            return
        if issue.edge_id:
            self._set_review_tab("关系审查")
            self._select_edge_by_id(issue.edge_id)
            self._focus_g6_item(issue.edge_id, "edge")
            return
        node_id = issue.node_id
        if not node_id:
            node_ids = issue.details.get("node_ids") if isinstance(issue.details, dict) else None
            if isinstance(node_ids, list) and node_ids:
                node_id = str(node_ids[0])
        if node_id:
            self._set_review_tab("节点审查")
            self._select_node_by_id(node_id)
            self._focus_g6_item(node_id, "node")
            return
        QMessageBox.information(self, "无法定位", "这条质量问题没有关联到具体节点或关系。")

    def _set_review_tab(self, title: str) -> None:
        for index in range(self.tabs.count()):
            tab_text = self.tabs.tabText(index)
            if tab_text == title or tab_text.startswith(f"{title} ·"):
                self.tabs.setCurrentIndex(index)
                return
                return

    def _select_node_by_id(self, node_id: str) -> None:
        for row, draft in enumerate(self.drafts):
            if draft.candidate_node_id == node_id:
                self.node_table.setCurrentCell(row, 0)
                self.node_table.selectRow(row)
                self._update_review_toolbar_visibility()
                return

    def _select_edge_by_id(self, edge_id: str) -> None:
        for row in range(self.edge_table.rowCount()):
            if self._edge_id_for_table_row(row) == edge_id:
                self.edge_table.setCurrentCell(row, 0)
                self.edge_table.selectRow(row)
                self._update_review_toolbar_visibility()
                return

    def _quality_severity_item(self, severity: str) -> QTableWidgetItem:
        labels = {"error": "错误", "warning": "警告", "info": "提示"}
        colors = {
            "error": {"bg": "#fee2e2", "fg": "#991b1b"},
            "warning": {"bg": "#fef3c7", "fg": "#92400e"},
            "info": {"bg": "#dbeafe", "fg": "#1d4ed8"},
        }.get(severity, {"bg": "#f1f5f9", "fg": "#334155"})
        item = self._item(labels.get(severity, severity), severity, editable=False)
        item.setBackground(QBrush(QColor(colors["bg"])))
        item.setForeground(QBrush(QColor(colors["fg"])))
        return item

    def _node_cell_changed(self, row: int, column: int) -> None:
        if row >= len(self.drafts):
            return
        if column == 2:
            combo = self.node_table.cellWidget(row, 2)
            if isinstance(combo, QComboBox):
                old_status = normalize_review_status(self.drafts[row].review_status)
                new_status = self._combo_status_value(combo)
                if old_status != new_status:
                    self._push_undo_snapshot("调整节点状态")
                self.drafts[row].review_status = new_status
                for anchor in self.drafts[row].evidence_anchors:
                    anchor.review_status = self.drafts[row].review_status
                self._refresh_review_after_status_change()
            return
        item = self.node_table.item(row, column)
        if item is None:
            return
        draft = self.drafts[row]
        value = item.text().strip()
        if column == 0:
            if not value:
                QMessageBox.warning(self, "节点名称无效", "节点名称不能为空。")
                self._render_tables()
                return
            if value == draft.candidate_display_name:
                return
            self._push_undo_snapshot("表格内编辑节点名称")
            draft.candidate_display_name = value
            self._sync_parent_display_names(draft.candidate_node_id, value)
        elif column == 1:
            knowledge_type = self._knowledge_type_from_inline_text(value)
            if not knowledge_type:
                QMessageBox.warning(self, "类型无效", "请填写有效的知识类型，例如：概念、性质、规则、方法、表征、题型、应用。")
                self._render_tables()
                return
            if knowledge_type == normalize_node_type(draft.knowledge_type):
                return
            self._push_undo_snapshot("表格内编辑节点类型")
            draft.knowledge_type = knowledge_type
        elif column == 3:
            try:
                confidence = max(0.0, min(1.0, float(value)))
            except ValueError:
                QMessageBox.warning(self, "置信度无效", "置信度需要填写 0 到 1 之间的数字。")
                self._render_tables()
                return
            if abs(confidence - float(draft.confidence)) < 0.0001:
                return
            self._push_undo_snapshot("表格内编辑节点置信度")
            draft.confidence = confidence
        elif column == 4:
            if value == draft.reasoning_summary:
                return
            self._push_undo_snapshot("表格内编辑候选理由")
            draft.reasoning_summary = value
        else:
            return
        self._refresh_review_after_status_change()

    def _edge_cell_changed(self, row: int, column: int) -> None:
        if column == 3:
            return
        edge_id = self._edge_id_for_table_row(row)
        if not edge_id:
            return
        relation = self._relation_details_by_edge_id(edge_id)
        if relation is None:
            self._render_tables()
            return
        item = self.edge_table.item(row, column)
        if item is None:
            return
        value = item.text().strip()
        next_source = relation["source"]
        next_target = relation["target"]
        next_relation_type = relation["relation_type"]
        next_evidence = relation["evidence"]
        if column == 0:
            next_source = self._node_id_from_inline_text(value)
            if not next_source:
                QMessageBox.warning(self, "起点无效", "请填写已有节点名称或节点 ID。")
                self._render_tables()
                return
        elif column == 1:
            next_relation_type = self._relation_type_from_inline_text(value)
            if not next_relation_type:
                QMessageBox.warning(self, "关系类型无效", "请填写有效关系类型，例如：前置、递进、推导、解释、等价、并列、对比、应用、表征。")
                self._render_tables()
                return
        elif column == 2:
            next_target = self._node_id_from_inline_text(value)
            if not next_target:
                QMessageBox.warning(self, "终点无效", "请填写已有节点名称或节点 ID。")
                self._render_tables()
                return
        elif column == 4:
            next_evidence = value
        else:
            return
        if not next_source or not next_target or next_source == next_target:
            QMessageBox.warning(self, "关系无效", "关系起点和终点不能为空，也不能相同。")
            self._render_tables()
            return
        if (
            next_source == relation["source"]
            and next_target == relation["target"]
            and next_relation_type == relation["relation_type"]
            and next_evidence == relation["evidence"]
        ):
            return
        self._push_undo_snapshot("表格内编辑关系")
        self._apply_inline_relation_update(
            edge_id=edge_id,
            source_id=next_source,
            target_id=next_target,
            relation_type=next_relation_type,
            status=relation["status"],
            evidence=next_evidence,
        )
        self._refresh_review_after_status_change()

    def _status_combo(self, status: str) -> QComboBox:
        combo = QComboBox()
        normalized = normalize_review_status(status)
        for option in STATUS_OPTIONS:
            combo.addItem(self._review_status_label(option), option)
        combo.setCurrentIndex(max(combo.findData(normalized), 0))
        self._apply_status_combo_style(combo, normalized)
        combo.currentIndexChanged.connect(
            lambda _index, widget=combo: self._apply_status_combo_style(widget, self._combo_status_value(widget))
        )
        combo.currentIndexChanged.connect(lambda _index, widget=combo: self._status_combo_changed(widget))
        return combo

    @staticmethod
    def _review_status_label(status: str) -> str:
        return STATUS_LABELS.get(normalize_review_status(status), status)

    @staticmethod
    def _knowledge_type_label(knowledge_type: str) -> str:
        normalized = normalize_node_type(knowledge_type)
        return KNOWLEDGE_TYPE_LABELS.get(normalized, knowledge_type)

    @staticmethod
    def _combo_status_value(combo: QComboBox) -> str:
        value = combo.currentData()
        return normalize_review_status(str(value if value is not None else combo.currentText()))

    @staticmethod
    def _status_colors(status: str) -> dict[str, str]:
        return STATUS_COLORS.get(normalize_review_status(status), STATUS_COLORS["pending"])

    def _apply_status_combo_style(self, combo: QComboBox, status: str) -> None:
        colors = self._status_colors(status)
        combo.setStyleSheet(
            "QComboBox {"
            f"background: {colors['bg']};"
            f"color: {colors['fg']};"
            f"border: 1px solid {colors['border']};"
            "border-radius: 6px;"
            "padding: 3px 8px;"
            "font-weight: 600;"
            "}"
        )

    def _status_item(self, status: str) -> QTableWidgetItem:
        item = self._item(self._review_status_label(status), normalize_review_status(status), editable=False)
        colors = self._status_colors(status)
        item.setBackground(QBrush(QColor(colors["bg"])))
        item.setForeground(QBrush(QColor(colors["fg"])))
        return item

    def _status_combo_changed(self, sender: QComboBox | None = None) -> None:
        if sender is None:
            raw_sender = self.sender()
            if isinstance(raw_sender, QComboBox):
                sender = raw_sender
        if sender is None:
            return
        for row in range(self.node_table.rowCount()):
            if self.node_table.cellWidget(row, 2) is sender and row < len(self.drafts):
                old_status = normalize_review_status(self.drafts[row].review_status)
                new_status = self._combo_status_value(sender)
                if old_status != new_status:
                    self._push_undo_snapshot("调整节点状态")
                self.drafts[row].review_status = new_status
                for anchor in self.drafts[row].evidence_anchors:
                    anchor.review_status = self.drafts[row].review_status
                self._refresh_review_after_status_change()
                return
        for row in range(self.edge_table.rowCount()):
            if self.edge_table.cellWidget(row, 3) is sender:
                edge_id_item = self.edge_table.item(row, 1)
                if edge_id_item:
                    edge_id = str(edge_id_item.data(Qt.ItemDataRole.UserRole) or "")
                    old_status = self._relation_details_by_edge_id(edge_id)
                    new_status = self._combo_status_value(sender)
                    if old_status is None or normalize_review_status(old_status["status"]) != new_status:
                        self._push_undo_snapshot("调整关系状态")
                    self._update_relation_status(edge_id, new_status)
                    self._refresh_review_after_status_change()
                return

    def _node_type_combo_changed(self, row: int, combo: QComboBox) -> None:
        if not (0 <= row < len(self.drafts)):
            return
        self._select_node_row(row)
        knowledge_type = normalize_node_type(combo.currentData() or "", default="")
        if not knowledge_type:
            return
        old_type = normalize_node_type(self.drafts[row].knowledge_type)
        if old_type == knowledge_type:
            return
        self._push_undo_snapshot("下拉调整节点类型")
        self.drafts[row].knowledge_type = knowledge_type
        self._refresh_review_after_status_change()

    def _edge_source_combo_changed(self, row: int, combo: QComboBox) -> None:
        edge_id = self._edge_id_for_table_row(row)
        source_id = str(combo.currentData() or "")
        self._apply_edge_combo_change(row=row, edge_id=edge_id, source_id=source_id)

    def _edge_target_combo_changed(self, row: int, edge_id: str, combo: QComboBox) -> None:
        target_id = str(combo.currentData() or "")
        self._apply_edge_combo_change(row=row, edge_id=edge_id, target_id=target_id)

    def _edge_relation_type_combo_changed(self, row: int, edge_id: str, combo: QComboBox) -> None:
        relation_type = normalize_relation_type(combo.currentData() or "", default="")
        self._apply_edge_combo_change(row=row, edge_id=edge_id, relation_type=relation_type)

    def _apply_edge_combo_change(
        self,
        *,
        row: int,
        edge_id: str,
        source_id: str | None = None,
        target_id: str | None = None,
        relation_type: str | None = None,
    ) -> None:
        if not edge_id:
            return
        self._select_edge_row(row)
        relation = self._relation_details_by_edge_id(edge_id)
        if relation is None:
            self._render_tables()
            return
        next_source = source_id if source_id is not None else relation["source"]
        next_target = target_id if target_id is not None else relation["target"]
        next_relation_type = relation_type if relation_type is not None else relation["relation_type"]
        if not next_source or not next_target or not next_relation_type:
            self._render_tables()
            return
        if next_source == next_target:
            QMessageBox.warning(self, "关系无效", "关系起点和终点不能相同。")
            self._render_tables()
            return
        if (
            next_source == relation["source"]
            and next_target == relation["target"]
            and next_relation_type == relation["relation_type"]
        ):
            return
        self._push_undo_snapshot("下拉调整关系")
        self._apply_inline_relation_update(
            edge_id=edge_id,
            source_id=next_source,
            target_id=next_target,
            relation_type=next_relation_type,
            status=relation["status"],
            evidence=relation["evidence"],
        )
        self._refresh_review_after_status_change()

    def _update_relation_status(self, edge_id: str, status: str) -> None:
        self.review_session.update_relation_status(edge_id, status)

    def _refresh_review_after_status_change(self, *, synchronize_document: bool = True) -> None:
        self._rebuild_review_workbook(synchronize_document=synchronize_document)
        pending_count = self._pending_node_count() + self._pending_relation_count()
        self._set_workspace_state(
            "待审查" if pending_count else "审查完成",
            "warning" if pending_count else "success",
        )
        self.statusBar().showMessage("审查状态已同步到图谱。")

    def _rebuild_review_workbook(self, *, synchronize_document: bool = True) -> None:
        if not self.current_record:
            return
        try:
            if synchronize_document:
                self._synchronize_review_document_from_drafts()
            self.workbook = self.review_service.build_review_workbook(
                self.drafts,
                source_id=self.current_record.source_id,
            )
            self._apply_relation_status_overrides()
        except Exception:
            self.workbook = None
        self._render_tables()
        self._render_graph()

    def _synchronize_review_document_from_drafts(self) -> None:
        """Commit one completed legacy UI operation into the v2 aggregate."""

        if self.review_document is None:
            return
        previous = self.review_document
        candidate = self.review_service.create_review_document(
            self.drafts,
            source_identity=previous.metadata.source_identity,
            analysis_run_id=previous.metadata.analysis_run_id,
            staged_outcome=self.current_staged_outcome,
        )
        candidate.metadata = deepcopy(previous.metadata)
        candidate.export_profiles = deepcopy(previous.export_profiles)
        candidate.view_states = deepcopy(previous.view_states)
        candidate.review_history = deepcopy(previous.review_history)
        candidate.migration_audit = deepcopy(previous.migration_audit)
        self._retain_archived_review_records(candidate, previous)
        old_nodes = {item.node_id: item for item in previous.nodes}
        for node in candidate.nodes:
            old = old_nodes.get(node.node_id)
            if old is None:
                continue
            content_changed = any(
                getattr(old, field_name) != getattr(node, field_name)
                for field_name in (
                    "display_name",
                    "node_name",
                    "node_type",
                    "cognitive_level",
                    "reasoning_summary",
                )
            )
            if (
                content_changed
                and normalize_review_status(old.review_status) == "accepted"
                and normalize_review_status(node.review_status) == "accepted"
            ):
                node.review_status = "needs_revision"
        old_relations_by_id = {item.relation_id: item for item in previous.relations}
        old_relations_by_fact = {
            (item.source_node_id, item.relation_type, item.target_node_id): item
            for item in previous.relations
        }
        for relation in candidate.relations:
            old = old_relations_by_id.get(relation.relation_id) or old_relations_by_fact.get(
                (relation.source_node_id, relation.relation_type, relation.target_node_id)
            )
            if old is None:
                continue
            content_changed = any(
                getattr(old, field_name) != getattr(relation, field_name)
                for field_name in (
                    "source_node_id",
                    "target_node_id",
                    "relation_type",
                    "reasoning_summary",
                    "confidence",
                )
            )
            if (
                content_changed
                and normalize_review_status(old.review_status) == "accepted"
                and normalize_review_status(relation.review_status) == "accepted"
            ):
                relation.review_status = "needs_revision"
        session = ReviewDocumentSession(previous)
        result = session.replace_document(
            candidate,
            expected_revision=previous.metadata.revision,
            action="desktop_ui_transaction",
            reason="legacy_row_adapter",
        )
        if result.changed:
            self.review_document = session.snapshot()
            self.drafts = session.draft_rows()

    @staticmethod
    def _retain_archived_review_records(
        candidate: P2ReviewDocumentDTO,
        previous: P2ReviewDocumentDTO,
    ) -> None:
        """Keep logically archived records when rebuilding legacy-compatible rows."""

        collections = (
            ("nodes", "node_id"),
            ("scopes", "scope_id"),
            ("occurrences", "occurrence_id"),
            ("relations", "relation_id"),
        )
        for attribute, id_field in collections:
            current_items = getattr(candidate, attribute)
            current_ids = {getattr(item, id_field) for item in current_items}
            for item in getattr(previous, attribute):
                if (
                    getattr(item, "lifecycle_state", "active") == "archived"
                    and getattr(item, id_field) not in current_ids
                ):
                    current_items.append(deepcopy(item))
        evidence_ids = {item.anchor_id for item in candidate.evidence}
        candidate.evidence.extend(
            deepcopy(item) for item in previous.evidence if item.anchor_id not in evidence_ids
        )

    def export_reviewed(self) -> None:
        if not self.drafts:
            QMessageBox.information(self, "暂无内容", "请先生成候选图谱。")
            return
        output_dir = QFileDialog.getExistingDirectory(self, "选择导出目录", str(WORKBENCH_STORAGE))
        if not output_dir:
            return
        if self.review_document is not None:
            self._export_review_document_v2(Path(output_dir))
            return
        result = self.review_service.export_reviewed(
            self.drafts,
            source_id=self.current_record.source_id if self.current_record else "LOCAL",
            output_dir=output_dir,
            dated_subdir=True,
            analysis_run_id=self.current_analysis_run_id,
            progress_recording_dir=self.current_progress_recording_dir,
        )
        self.last_export_directory = result.output_dir
        self.open_export_button.setToolTip(f"打开本轮输出目录：{result.output_dir}")
        formal_message = (
            str(result.formal_path)
            if result.formal_path is not None
            else "没有已通过节点，暂未生成正式图谱。"
        )
        layer_mapping_message = (
            str(result.layer_mapping_path)
            if result.layer_mapping_path is not None
            else "正式图谱未生成，暂未生成分层映射。"
        )
        if result.quality_report.has_errors:
            formal_message = "质量检查存在错误，暂未生成正式图谱。"
        quality_summary = self.review_service.quality_summary(result.quality_report)
        QMessageBox.information(
            self,
            "导出完成",
            f"工作底稿：{result.draft_path}\n"
            f"正式图谱：{formal_message}\n"
            f"分层映射：{layer_mapping_message}\n"
            f"质量报告：{result.quality_path}\n\n"
            f"导出清单：{result.manifest_path}\n"
            f"本轮目录：{result.output_dir}\n\n"
            f"{quality_summary}",
        )
        self._set_workspace_state(
            "需处理" if result.quality_report.has_errors else "已导出",
            "danger" if result.quality_report.has_errors else "success",
        )

    def _export_review_document_v2(self, output_root: Path) -> None:
        if self.review_document is None:
            return
        output = output_root / date.today().isoformat()
        output.mkdir(parents=True, exist_ok=True)
        review_path = output / "review_document_v2.xlsx"
        try:
            save_result = self.review_service.save_review_document(
                self.review_document,
                review_path,
            )
        except Exception as exc:
            QMessageBox.critical(self, "底稿保存失败", str(exc))
            self._set_workspace_state("保存失败", "danger")
            return

        profile_id = "desktop-p4-v1"
        profile = self.review_document.profile_by_id(profile_id) or ExportProfileDTO(profile_id)
        plan = self.review_service.preflight_publish(self.review_document, profile)
        if plan.status == "needs_decision":
            selected = self._choose_primary_memberships(plan, profile)
            if selected is None:
                self.last_export_directory = output
                QMessageBox.information(
                    self,
                    "审查底稿已保存",
                    f"审查底稿：{review_path}\n"
                    f"基线编号：{save_result.base_export_id}\n\n"
                    "已取消主归属裁决；未生成正式图谱。",
                )
                self._set_workspace_state("底稿已保存", "warning")
                return
            session = ReviewDocumentSession(self.review_document)
            session.execute(
                ReviewDocumentCommand(
                    action="confirm_publish_profile",
                    object_type="profile",
                    object_id=profile_id,
                    changes={
                        "target_protocol": "p4_v1_single_membership",
                        "primary_membership_by_node": selected,
                    },
                ),
                expected_revision=session.revision,
            )
            self.review_document = session.snapshot()
            self.drafts = session.draft_rows()
            profile = self.review_document.profile_by_id(profile_id) or profile
            save_result = self.review_service.save_review_document(
                self.review_document,
                review_path,
            )
            plan = self.review_service.preflight_publish(self.review_document, profile)

        if not plan.ready:
            details = "\n".join(
                f"- {issue.message} ({', '.join(issue.object_ids)})"
                for issue in plan.blockers[:12]
            )
            self.last_export_directory = output
            QMessageBox.warning(
                self,
                "底稿已保存，正式发布被阻断",
                f"审查底稿：{review_path}\n"
                f"基线编号：{save_result.base_export_id}\n\n"
                f"{details or '发布前检查未通过。'}",
            )
            self._set_workspace_state("发布待处理", "warning")
            return

        result = self.review_service.publish(
            self.review_document,
            plan,
            expected_revision=self.review_document.metadata.revision,
            output_root=output / "published",
        )
        self.last_export_directory = result.output_dir or output
        if result.status != "completed":
            QMessageBox.critical(
                self,
                "正式发布失败",
                f"审查底稿已保存：{review_path}\n\n{result.message}",
            )
            self._set_workspace_state("发布失败", "danger")
            return
        QMessageBox.information(
            self,
            "保存与发布完成",
            f"审查底稿：{review_path}\n"
            f"正式图谱：{result.formal_path}\n"
            f"分层映射：{result.layer_mapping_path}\n"
            f"发布清单：{result.manifest_path}\n\n"
            f"文档 revision：{self.review_document.metadata.revision}",
        )
        self._set_workspace_state("已发布", "success")

    def _choose_primary_memberships(
        self,
        plan: Any,
        profile: ExportProfileDTO,
    ) -> dict[str, str] | None:
        if self.review_document is None:
            return None
        membership_relations = {
            relation.relation_id: relation
            for relation in self.review_document.relations
            if relation.relation_family == RELATION_FAMILY_MEMBERSHIP
            and relation.lifecycle_state == "active"
            and normalize_review_status(relation.review_status) == "accepted"
        }
        memberships_by_node: dict[str, list[Any]] = {}
        for relation in membership_relations.values():
            if relation.target_node_id in plan.selected_node_ids:
                memberships_by_node.setdefault(relation.target_node_id, []).append(relation)
        nodes = {node.node_id: node for node in self.review_document.nodes}
        dialog = QDialog(self)
        dialog.setWindowTitle("确认 P4 主归属")
        dialog.resize(720, 420)
        layout = QVBoxLayout(dialog)
        intro = QLabel(
            "P4 v1 每个知识点只能携带一个教材归属。请确认下列选择；"
            "未选中的次归属仍保留在审查底稿中。"
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        selectors: dict[str, QComboBox] = {}
        for node_id in sorted(plan.selected_node_ids):
            candidates = sorted(
                memberships_by_node.get(node_id, []),
                key=lambda item: (item.source_node_id, item.relation_id),
            )
            if not candidates:
                continue
            combo = QComboBox()
            for relation in candidates:
                scope_node = nodes.get(relation.source_node_id)
                label = scope_node.display_name if scope_node else relation.source_node_id
                combo.addItem(f"{label}  [{relation.source_node_id}]", relation.relation_id)
            current_id = (
                profile.primary_membership_by_node.get(node_id)
                or plan.suggested_primary_membership_by_node.get(node_id, "")
            )
            if current_id:
                combo.setCurrentIndex(max(combo.findData(current_id), 0))
            selectors[node_id] = combo
            node_label = nodes[node_id].display_name if node_id in nodes else node_id
            form.addRow(f"{node_label}  [{node_id}]", combo)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        selected = dict(profile.primary_membership_by_node)
        for node_id, combo in selectors.items():
            selected[node_id] = str(combo.currentData() or "")
        return selected

    def _document_options(self) -> DocumentReadOptions:
        metadata = derive_subject_metadata(
            self.subject_input.text(),
            self.grade_input.text(),
            self.term_input.text(),
        )
        return DocumentReadOptions(
            subject=metadata.subject,
            grade=metadata.grade,
            term=metadata.term,
            source_id=self.source_id_input.text() or (self.source_path.stem if self.source_path else "LOCAL_TEXTBOOK"),
            source_document_type="electronic_textbook",
            education_stage=metadata.education_stage,
            grade_band=metadata.grade_band,
            subject_tags=list(metadata.subject_tags),
        )

    @staticmethod
    def _item(text: str, user_data: str | None = None, *, editable: bool = True) -> QTableWidgetItem:
        item = QTableWidgetItem(str(text or ""))
        if user_data is not None:
            item.setData(Qt.ItemDataRole.UserRole, user_data)
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def _clip(text: str, length: int) -> str:
        value = str(text or "")
        return value if len(value) <= length else value[: length - 3] + "..."


def main() -> None:
    app = QApplication(sys.argv)
    app_info = load_app_info(PROJECT_ROOT / "config" / "app_info.json")
    app.setApplicationName(app_info.application.display_name)
    window = TextbookWorkbenchWindow()
    window.show()
    sys.exit(app.exec())
