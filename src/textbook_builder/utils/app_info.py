from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ApplicationInfo:
    project_code: str = "LZH-P2"
    name: str = "教材知识图谱本地工作台"
    description: str = ""

    @property
    def display_name(self) -> str:
        return " ".join(part for part in (self.project_code, self.name) if part)


@dataclass(frozen=True)
class DeveloperInfo:
    name: str = "未配置"
    contact: str = "未配置"


@dataclass(frozen=True)
class AppInfo:
    schema_version: int = 2
    application: ApplicationInfo = ApplicationInfo()
    developer: DeveloperInfo = DeveloperInfo()


def load_app_info(file_path: str | Path) -> AppInfo:
    """Load non-secret application metadata without making startup depend on it."""

    defaults = AppInfo()
    path = Path(file_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return defaults
    if not isinstance(payload, dict):
        return defaults

    application_payload = _mapping(payload.get("application"))
    developer_payload = _mapping(payload.get("developer"))
    raw_version = payload.get("schema_version")
    schema_version = (
        raw_version
        if isinstance(raw_version, int) and not isinstance(raw_version, bool) and raw_version >= 1
        else defaults.schema_version
    )
    return AppInfo(
        schema_version=schema_version,
        application=ApplicationInfo(
            project_code=_text(
                application_payload.get("project_code"),
                defaults.application.project_code,
            ),
            name=_text(application_payload.get("name"), defaults.application.name),
            description=_text(
                application_payload.get("description"),
                defaults.application.description,
            ),
        ),
        developer=DeveloperInfo(
            name=_text(developer_payload.get("name"), defaults.developer.name),
            contact=_text(
                developer_payload.get("contact"),
                defaults.developer.contact,
            ),
        ),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, default: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else default
