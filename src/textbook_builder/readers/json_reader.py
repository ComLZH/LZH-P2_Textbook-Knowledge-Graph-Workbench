from __future__ import annotations

import json
from pathlib import Path

from ..contracts import SourceRecordDTO


class StructuredTextbookJsonReader:
    def read(self, file_path: str | Path) -> list[SourceRecordDTO]:
        payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
        records = payload.get("records", payload if isinstance(payload, list) else [])
        return [self._normalize_record(record) for record in records]

    def _normalize_record(self, payload: dict[str, object]) -> SourceRecordDTO:
        return SourceRecordDTO(
            source_id=str(payload["source_id"]),
            source_type=str(payload.get("source_type", "structured_textbook")),
            source_path=str(payload.get("source_path", "")),
            subject=str(payload["subject"]),
            grade=str(payload["grade"]),
            term=str(payload["term"]),
            raw_text=str(payload.get("raw_text", "")),
            raw_structure=dict(payload.get("raw_structure", {})),
            source_format=str(payload.get("source_format", "structured_json")).strip()
            or "structured_json",
            source_document_type=str(
                payload.get("source_document_type", "textbook")
            ).strip()
            or "textbook",
            education_stage=str(payload.get("education_stage", "")).strip(),
            grade_band=str(payload.get("grade_band", "")).strip(),
            subject_tags=self._normalize_string_list(payload.get("subject_tags", [])),
            source_metadata=dict(payload.get("source_metadata", {})),
        )

    @staticmethod
    def _normalize_string_list(raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, (list, tuple, set)):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        value = str(raw_value).strip()
        return [value] if value else []
