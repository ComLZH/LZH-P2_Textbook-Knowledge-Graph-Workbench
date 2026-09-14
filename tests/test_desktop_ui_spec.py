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

from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QScrollArea

from textbook_builder.contracts import DraftKnowledgeItemDTO, EvidenceAnchorDTO
from textbook_builder.desktop_app import GraphReviewBridge, TextbookWorkbenchWindow
from textbook_builder.exporters import FormalGraphWorkbookBuilder
from textbook_builder.readers import DraftWorkbookReader
from textbook_builder.review_document import (
    P2ReviewDocumentDTO,
    ReviewDocumentMetadataDTO,
    ReviewNodeDTO,
)
from textbook_builder.review_document_io import ReviewDocumentExporter
from textbook_builder.utils.chapter_layout import build_chapter_layout_plans, build_child_map
from textbook_builder.utils.relation_layout import (
    build_relation_layouts,
    relation_directionality,
    relation_edge_id,
)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qt_app: QApplication) -> TextbookWorkbenchWindow:
    workbench = TextbookWorkbenchWindow()
    yield workbench
    workbench.close()
    qt_app.processEvents()


def test_workbench_starts_with_clear_command_and_empty_states(
    window: TextbookWorkbenchWindow,
) -> None:
    assert window.file_menu.title() == "文件"
    assert window.open_action.shortcut().toString() == "Ctrl+O"
    assert window.export_action.shortcut().toString() == "Ctrl+Shift+S"
    assert window.developer_info_action.shortcut().toString() == "F1"
    assert window.import_button.isEnabled() is True
    assert window.analyze_button.isEnabled() is False
    assert window.export_button.isEnabled() is False
    assert "请先导入" in window.analyze_button.toolTip()
    assert window.workspace_status_badge.text() == "等待导入"
    assert window.windowTitle() == "LZH-P2 教材知识图谱本地工作台"
    assert window.app_title_label.text() == "LZH-P2 教材知识图谱本地工作台"
    assert window.preview_content_stack.currentIndex() == 0
    assert window.graph_content_stack.currentIndex() == 0
    assert window.control_panel.findChild(QScrollArea, "controlScroll") is not None
    assert set(window.layout_mode_buttons) == {"focus_preview", "focus_graph", "show_all"}


def test_graph_review_bridge_dispatches_explicit_relayout_request() -> None:
    calls: list[str] = []
    bridge = GraphReviewBridge(lambda _config: None, relayout_callback=lambda: calls.append("reflow"))

    bridge.requestRelayout()

    assert calls == ["reflow"]


def test_model_details_and_api_key_visibility_follow_mode(
    window: TextbookWorkbenchWindow,
) -> None:
    assert window.model_details_container.isHidden() is True
    assert window.api_key_input.isEnabled() is False
    assert window.api_key_input.echoMode() == QLineEdit.EchoMode.Password

    window.model_details_button.setChecked(True)
    window.model_mode_input.setCurrentText("真实大模型")
    assert window.model_details_container.isHidden() is False
    assert window.api_key_input.isEnabled() is True

    window.api_key_visibility_button.setChecked(True)
    assert window.api_key_input.echoMode() == QLineEdit.EchoMode.Normal
    assert window.api_key_visibility_button.text() == "隐藏"

    window.model_mode_input.setCurrentText("本地模拟")
    assert window.api_key_input.isEnabled() is False
    assert window.api_key_input.echoMode() == QLineEdit.EchoMode.Password
    assert "本地模拟" in window.model_summary_label.text()


def test_progress_recording_defaults_to_full_and_contains_relation_is_preserved(
    window: TextbookWorkbenchWindow,
) -> None:
    assert window.progress_recording_input.currentData() == "full"
    assert "完整过程记录" in window.model_summary_label.text()
    relation_combo = window._relation_type_combo()
    contains_index = relation_combo.findData("contains")

    assert contains_index >= 0
    assert relation_combo.itemText(contains_index) == "包含"


def test_real_sample_pdf_moves_workbench_into_ready_state(
    window: TextbookWorkbenchWindow,
) -> None:
    source_path = PROJECT_ROOT / "textbook" / "目录页构建测试.pdf"

    assert window._load_source_path(source_path) is True

    assert window.source_path == source_path
    assert window.file_label.text() == source_path.name
    assert window.file_label.toolTip() == str(source_path)
    assert window.preview_content_stack.currentIndex() == 1
    assert window.preview_status_badge.text().startswith("PDF · ")
    assert window.workspace_status_badge.text() == "已就绪"
    assert window.analyze_button.isEnabled() is True
    assert window.export_button.isEnabled() is False
    assert window.graph_content_stack.currentIndex() == 0


def test_review_document_package_can_be_reopened_for_review(
    window: TextbookWorkbenchWindow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    document = P2ReviewDocumentDTO(
        metadata=ReviewDocumentMetadataDTO("doc-ui", "book-ui", revision=3),
        nodes=[ReviewNodeDTO("node-ui", "三角形", "triangle", subject="math")],
    )
    package_path = tmp_path / "review_document_v2.xlsx"
    ReviewDocumentExporter().export_review_document(document, package_path)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(package_path), "审查工作簿 (*.xlsx)"),
    )

    window.choose_review_document()

    assert window.review_document is not None
    assert window.review_document.metadata.document_id == "doc-ui"
    assert window.review_document.metadata.revision == 3
    assert [item.candidate_node_id for item in window.drafts] == ["node-ui"]
    assert window.analyze_button.isEnabled() is False
    assert window.export_button.isEnabled() is True
    assert window.preview_status_badge.text() == "审查包"


def test_global_layout_switcher_focuses_and_restores_panels(
    window: TextbookWorkbenchWindow,
) -> None:
    window._apply_layout_preset("focus_preview")
    assert window.preview_panel.isHidden() is False
    assert window.graph_panel.isHidden() is True
    assert window.layout_mode_buttons["focus_preview"].isChecked() is True

    window._apply_layout_preset("focus_graph")
    assert window.preview_panel.isHidden() is True
    assert window.graph_panel.isHidden() is False
    assert window.layout_mode_buttons["focus_graph"].isChecked() is True

    window._apply_layout_preset("show_all")
    assert window.preview_panel.isHidden() is False
    assert window.graph_panel.isHidden() is False
    assert window.layout_mode_buttons["show_all"].isChecked() is True


def test_review_and_graph_badges_track_current_draft_counts(
    window: TextbookWorkbenchWindow,
) -> None:
    draft = DraftKnowledgeItemDTO(
        draft_id="draft_ui",
        subject="math",
        grade="g8",
        term="term1",
        chapter="chapter_ui",
        candidate_display_name="测试知识点",
        candidate_node_name="test_node",
        candidate_node_id="node_ui",
        source_text="测试证据",
        evidence_anchors=[
            EvidenceAnchorDTO(
                anchor_id="anchor_ui",
                anchor_text="测试证据",
                target_ids=["node_ui"],
                review_status="pending",
            )
        ],
        review_status="pending",
    )
    window.drafts = [draft]
    window.workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        window.drafts,
        graph_id="UI_SPEC",
    )

    window._render_tables()

    assert window.graph_node_badge.text() == "节点 1"
    assert window.graph_edge_badge.text() == "关系 0"
    assert window.graph_pending_badge.text() == "待审 1"
    assert window.graph_revision_badge.text() == "待修 0"
    assert window.tabs.tabText(0) == "节点审查 · 1"
    assert window.tabs.tabText(3) == "证据锚点 · 1"
    assert window.review_empty_hint.isHidden() is True


def test_compatibility_layout_ignores_g6_center_positions_and_contains_children(
    window: TextbookWorkbenchWindow,
) -> None:
    draft_path = PROJECT_ROOT / "导出测试" / "reviewed_draft.xlsx"
    window.drafts = DraftWorkbookReader().read(draft_path)
    window.workbook = FormalGraphWorkbookBuilder().build_review_workbook(
        window.drafts,
        graph_id="UI_COMPATIBILITY_LAYOUT",
    )
    child_map = build_child_map(window.workbook.nodes, window.workbook.edges)
    layout_plans = build_chapter_layout_plans(window.workbook.nodes, window.workbook.edges)
    relation_layouts = build_relation_layouts(window.workbook.nodes, window.workbook.edges)
    roles = window._node_role_map(window.workbook.nodes, child_map, layout_plans)
    geometry = window._graph_geometry(
        window.workbook.nodes,
        child_map,
        layout_plans,
        roles,
        relation_layouts,
    )

    chapter_id = next(node_id for node_id, children in child_map.items() if children)
    child_id = child_map[chapter_id][1]
    chapter_rect = geometry[chapter_id]
    original_child_rect = geometry[child_id]
    window.g6_view_config = {
        "manual_positions": {
            chapter_id: {"x": 520.0, "y": 160.0},
            child_id: {"x": 520.0, "y": 160.0},
        }
    }
    window.graph_node_positions = {}

    adjusted = window._apply_graph_node_positions(geometry)

    assert adjusted[child_id] == original_child_rect
    ordered_chapters = [
        node.display_name
        for node in sorted(
            [node for node in window.workbook.nodes if child_map.get(node.node_id)],
            key=lambda node: adjusted[node.node_id][1],
        )
    ]
    assert ordered_chapters == [
        "第十三章 三角形",
        "第十四章 全等三角形",
        "第十五章 轴对称",
        "第十六章 整式的乘法",
        "第十七章 因式分解",
        "第十八章 分式",
    ]
    for parent_id, children in child_map.items():
        parent_x, parent_y, parent_width, parent_height = adjusted[parent_id]
        for current_child_id in children:
            child_x, child_y, child_width, child_height = adjusted[current_child_id]
            assert child_x >= parent_x + 24.0
            assert child_y >= parent_y + 58.0
            assert child_x + child_width <= parent_x + parent_width - 24.0
            assert child_y + child_height <= parent_y + parent_height - 18.0
        row_rects = sorted(
            [adjusted[current_child_id] for current_child_id in children],
            key=lambda rect: (rect[1], rect[0]),
        )
        for left_rect, right_rect in zip(row_rects, row_rects[1:]):
            if left_rect[1] == right_rect[1]:
                assert right_rect[0] - (left_rect[0] + left_rect[2]) >= 96.0
    layout_by_node = {
        node_id: layout
        for layout in relation_layouts.values()
        for node_id in layout.node_ranks
    }
    for edge in window.workbook.edges:
        if relation_directionality(edge.relation_type) != "directional":
            continue
        layout = layout_by_node.get(edge.source_node_id)
        if layout is None or layout is not layout_by_node.get(edge.target_node_id):
            continue
        edge_id = relation_edge_id(
            edge.source_node_id,
            edge.relation_type,
            edge.target_node_id,
        )
        if edge_id in layout.cycle_edge_ids:
            continue
        source_rect = geometry[edge.source_node_id]
        target_rect = geometry[edge.target_node_id]
        assert source_rect[0] < target_rect[0]


def test_legacy_g6_positions_are_invalidated_by_relation_layout_revision(
    window: TextbookWorkbenchWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "legacy_view.json"
    config_path.write_text(
        """{
          "graph_id": "LEGACY",
          "manual_positions": {
            "left": {"x": 260.0, "y": 160.0},
            "middle": {"x": 520.0, "y": 160.0},
            "right": {"x": 780.0, "y": 160.0}
          }
        }""",
        encoding="utf-8",
    )
    monkeypatch.setattr(window, "_g6_view_config_path", lambda _graph_id: config_path)

    migrated = window._load_g6_view_config("LEGACY")

    assert migrated["layout_revision"] == 6
    assert migrated["layout_mode"] == "knowledge_relations_v6"
    assert migrated["view_mode"] == "relations"
    assert migrated["show_auxiliary_edges"] is False
    assert migrated["manual_positions"] == {}
    assert window.graph_node_positions == {}


def test_future_g6_layout_state_is_read_only_and_not_applied(
    window: TextbookWorkbenchWindow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "future_view.json"
    original = {
        "graph_id": "FUTURE",
        "layout_revision": 99,
        "view_mode": "relations",
        "manual_positions": {"node": {"x": 10.0, "y": 20.0}},
    }
    config_path.write_text(__import__("json").dumps(original), encoding="utf-8")
    monkeypatch.setattr(window, "_g6_view_config_path", lambda _graph_id: config_path)

    loaded = window._load_g6_view_config("FUTURE")

    assert loaded["layout_revision"] == 99
    assert loaded["manual_positions"] == {}
    assert loaded["read_only_future_version"] is True
    assert __import__("json").loads(config_path.read_text(encoding="utf-8")) == original
    window.g6_view_config = loaded
    assert window._save_g6_view_config(loaded) is False
    assert __import__("json").loads(config_path.read_text(encoding="utf-8")) == original


def test_g6_view_save_failure_preserves_in_memory_state_and_reports_error(
    window: TextbookWorkbenchWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_id = window._current_graph_id()
    original = {"graph_id": graph_id, "view_mode": "relations", "marker": "old"}
    window.g6_view_config = dict(original)

    def fail_save(_graph_id: str, _config: dict) -> None:
        raise OSError("locked fixture")

    monkeypatch.setattr(window, "_persist_graph_view_config", fail_save)

    saved = window._save_g6_view_config({"graph_id": graph_id, "manual_positions": {}})

    assert saved is False
    assert window.g6_view_config == original
    assert "未保存" in window.statusBar().currentMessage()


def test_g6_manual_position_cleaner_rejects_nonfinite_and_unbounded_values(
    window: TextbookWorkbenchWindow,
) -> None:
    cleaned = window._clean_g6_manual_positions(
        {
            "valid": {"x": 12.5, "y": -8.0},
            "nan": {"x": float("nan"), "y": 1.0},
            "infinite": {"x": 1.0, "y": float("inf")},
            "unbounded": {"x": 10_000_001, "y": 1.0},
            "incomplete": {"x": 1.0},
        }
    )

    assert cleaned == {"valid": {"x": 12.5, "y": -8.0}}
