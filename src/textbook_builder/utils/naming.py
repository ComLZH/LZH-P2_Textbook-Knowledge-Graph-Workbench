from __future__ import annotations

import re


def slugify_display_name(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized or "knowledge"


def build_node_id(
    *,
    subject: str,
    grade: str,
    term: str,
    chapter: str,
    node_name: str,
) -> str:
    parts = [subject, grade, term, chapter, node_name]
    return "_".join(part.strip().lower() for part in parts if part.strip())
