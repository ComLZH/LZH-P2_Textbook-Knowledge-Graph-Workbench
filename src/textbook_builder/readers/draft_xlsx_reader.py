from __future__ import annotations

from pathlib import Path

from ..contracts import DraftKnowledgeItemDTO, EvidenceAnchorDTO
from ..utils.graph_semantics import normalize_node_type, normalize_relation_type
from ..utils.review_status import normalize_review_status
from ._xlsx_support import SimpleXlsxReaderConfig, SimpleXlsxWorkbookReader


class DraftWorkbookReader:
    def __init__(self, sheet_name: str = "draft") -> None:
        self._sheet_name = sheet_name
        self._reader = SimpleXlsxWorkbookReader(
            SimpleXlsxReaderConfig(
                json_columns={
                    "candidate_prerequisites",
                    "candidate_relations",
                    "evidence_anchors",
                    "subject_tags",
                }
            )
        )

    def read(self, file_path: str | Path) -> list[DraftKnowledgeItemDTO]:
        workbook = self._reader.read_sheets(file_path, [self._sheet_name])
        rows = workbook.get(self._sheet_name, [])
        return [self._to_draft(row) for row in rows]

    def _to_draft(self, payload: dict[str, object]) -> DraftKnowledgeItemDTO:
        return DraftKnowledgeItemDTO(
            draft_id=str(payload["draft_id"]),
            subject=str(payload["subject"]),
            grade=str(payload["grade"]),
            term=str(payload["term"]),
            chapter=str(payload["chapter"]),
            section=str(payload.get("section", "")),
            candidate_display_name=str(payload["candidate_display_name"]),
            candidate_node_name=str(payload["candidate_node_name"]),
            candidate_node_id=str(payload["candidate_node_id"]),
            candidate_parent_name=str(payload.get("candidate_parent_name", "")),
            candidate_parent_node_id=str(payload.get("candidate_parent_node_id", "")),
            candidate_prerequisites=self._string_list(
                payload.get("candidate_prerequisites", [])
            ),
            candidate_relations=self._relation_list(
                payload.get("candidate_relations", [])
            ),
            knowledge_type=normalize_node_type(payload.get("knowledge_type", "concept")),
            cognitive_level=str(payload.get("cognitive_level", "")),
            education_stage=str(payload.get("education_stage", "")),
            grade_band=str(payload.get("grade_band", "")),
            subject_tags=self._string_list(payload.get("subject_tags", [])),
            source_id=str(payload.get("source_id", "")),
            source_path=str(payload.get("source_path", "")),
            source_format=str(payload.get("source_format", "")),
            source_document_type=str(payload.get("source_document_type", "")),
            source_text=str(payload.get("source_text", "")),
            source_location=str(payload.get("source_location", "")),
            evidence_anchors=self._evidence_anchor_list(
                payload.get("evidence_anchors", [])
            ),
            confidence=float(payload.get("confidence", 1.0)),
            reasoning_summary=str(payload.get("reasoning_summary", "")),
            extractor_source=str(
                payload.get("extractor_source", "structured_extractor")
            )
            or "structured_extractor",
            review_status=normalize_review_status(
                payload.get("review_status", "pending")
            ),
            review_notes=str(payload.get("review_notes", "")),
        )

    @staticmethod
    def _string_list(raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, list):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        value = str(raw_value).strip()
        return [value] if value else []

    @staticmethod
    def _relation_list(raw_value: object) -> list[dict[str, object]]:
        if not isinstance(raw_value, list):
            return []
        relations: list[dict[str, object]] = []
        for item in raw_value:
            if not isinstance(item, dict):
                continue
            relation_type = normalize_relation_type(item.get("relation_type", ""))
            if not relation_type:
                continue
            normalized = dict(item)
            normalized["relation_type"] = relation_type
            relations.append(normalized)
        return relations

    def _evidence_anchor_list(self, raw_value: object) -> list[EvidenceAnchorDTO]:
        if not isinstance(raw_value, list):
            return []
        anchors: list[EvidenceAnchorDTO] = []
        for item in raw_value:
            if not isinstance(item, dict):
                continue
            anchors.append(
                EvidenceAnchorDTO(
                    anchor_id=str(item.get("anchor_id", "")),
                    source_id=str(item.get("source_id", "")),
                    source_path=str(item.get("source_path", "")),
                    source_format=str(item.get("source_format", "")),
                    source_document_type=str(item.get("source_document_type", "")),
                    source_location=str(item.get("source_location", "")),
                    page_index=self._optional_int(item.get("page_index")),
                    image_index=self._optional_int(item.get("image_index")),
                    block_id=str(item.get("block_id", "")),
                    text_span=dict(item.get("text_span", {}))
                    if isinstance(item.get("text_span", {}), dict)
                    else {},
                    bbox=dict(item.get("bbox", {}))
                    if isinstance(item.get("bbox", {}), dict)
                    else {},
                    anchor_text=str(item.get("anchor_text", "")),
                    target_type=str(item.get("target_type", "node")) or "node",
                    target_ids=self._string_list(item.get("target_ids", [])),
                    confidence=float(item.get("confidence", 1.0)),
                    created_by=str(
                        item.get("created_by", "manual_or_structured_input")
                    )
                    or "manual_or_structured_input",
                    review_status=normalize_review_status(
                        item.get("review_status", "pending")
                    ),
                )
            )
        return anchors

    @staticmethod
    def _optional_int(raw_value: object) -> int | None:
        if raw_value in (None, ""):
            return None
        return int(raw_value)
