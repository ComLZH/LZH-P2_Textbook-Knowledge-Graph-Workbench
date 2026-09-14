from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox

from textbook_builder.desktop_app import TextbookWorkbenchWindow
from textbook_builder.utils.app_info import load_app_info


def test_project_app_info_contains_current_developer_details() -> None:
    app_info = load_app_info(PROJECT_ROOT / "config" / "app_info.json")

    assert app_info.schema_version == 2
    assert app_info.application.project_code == "LZH-P2"
    assert app_info.application.name == "教材知识图谱本地工作台"
    assert app_info.application.display_name == "LZH-P2 教材知识图谱本地工作台"
    assert app_info.developer.name == "刘钊昊"
    assert app_info.developer.contact == "comlzh@outlook.com"


def test_app_info_loader_trims_values_and_falls_back_per_field(tmp_path: Path) -> None:
    config_path = tmp_path / "app_info.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "application": {
                    "project_code": "  TEST-P2 ",
                    "name": "  测试工作台  ",
                    "description": 123,
                },
                "developer": {"name": "  测试开发者 ", "contact": ""},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    app_info = load_app_info(config_path)

    assert app_info.schema_version == 2
    assert app_info.application.project_code == "TEST-P2"
    assert app_info.application.name == "测试工作台"
    assert app_info.application.display_name == "TEST-P2 测试工作台"
    assert app_info.application.description == ""
    assert app_info.developer.name == "测试开发者"
    assert app_info.developer.contact == "未配置"


def test_app_info_loader_does_not_block_startup_for_invalid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "app_info.json"
    config_path.write_text("{invalid", encoding="utf-8")

    app_info = load_app_info(config_path)

    assert app_info.schema_version == 2
    assert app_info.application.name == "教材知识图谱本地工作台"
    assert app_info.application.display_name == "LZH-P2 教材知识图谱本地工作台"
    assert app_info.developer.name == "未配置"
    assert app_info.developer.contact == "未配置"


def test_developer_info_menu_opens_configured_dialog(monkeypatch: object) -> None:
    app = QApplication.instance() or QApplication([])
    captured: dict[str, str] = {}

    def capture_dialog(dialog: QMessageBox) -> int:
        captured["title"] = dialog.windowTitle()
        captured["text"] = dialog.text()
        captured["details"] = dialog.informativeText()
        return int(QMessageBox.StandardButton.Ok)

    monkeypatch.setattr(QMessageBox, "exec", capture_dialog)
    window = TextbookWorkbenchWindow()
    try:
        assert window.help_menu.title() == "帮助"
        assert window.developer_info_action.text() == "关于 LZH-P2 / 开发者信息"

        window.developer_info_action.trigger()

        assert captured["title"] == "LZH-P2 · 开发者信息"
        assert captured["text"] == "LZH-P2 教材知识图谱本地工作台"
        assert "项目标识：LZH-P2" in captured["details"]
        assert "开发者：刘钊昊" in captured["details"]
        assert "开发者联系方式：comlzh@outlook.com" in captured["details"]
    finally:
        window.close()
        app.processEvents()
