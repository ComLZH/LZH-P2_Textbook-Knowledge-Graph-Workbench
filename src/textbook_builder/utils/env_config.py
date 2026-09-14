from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_file(file_path: str | Path) -> dict[str, str]:
    path = Path(file_path)
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_quotes(value.strip())
        if key:
            values[key] = value
    return values


def load_project_env(project_root: str | Path) -> dict[str, str]:
    return load_dotenv_file(Path(project_root) / ".env")


def env_value(
    key: str,
    *,
    dotenv_values: dict[str, str] | None = None,
    default: str = "",
) -> str:
    if dotenv_values and key in dotenv_values and dotenv_values[key].strip():
        return dotenv_values[key].strip()
    return os.getenv(key, default).strip()


def env_bool(value: str, *, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
