from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, TypeVar

from .contracts import DraftKnowledgeItemDTO, EvidenceAnchorDTO
from .exporters.xlsx_exporter import XlsxWorkbookExporter
from .readers._xlsx_support import SimpleXlsxReaderConfig, SimpleXlsxWorkbookReader
from .readers.draft_xlsx_reader import DraftWorkbookReader
from .review_document import (
    ExportProfileDTO,
    KnowledgeOccurrenceDTO,
    MigrationAuditDTO,
    P2ReviewDocumentDTO,
    REVIEW_DOCUMENT_SCHEMA_VERSION,
    ReviewDecisionDTO,
    ReviewDocumentMetadataDTO,
    ReviewDocumentMigrator,
    ReviewNodeDTO,
    ReviewRelationDTO,
    ReviewViewStateDTO,
    StructuralScopeDTO,
    document_to_legacy_drafts,
)


REVIEW_DOCUMENT_SHEETS = (
    "review_meta",
    "draft",
    "scopes",
    "occurrences",
    "relations",
    "evidence",
    "migration_audit",
    "review_history",
    "export_profiles",
    "view_state",
    "import_baseline",
)

_JSON_COLUMNS = {
    "subject_tags",
    "candidate_prerequisites",
    "candidate_relations",
    "evidence_anchors",
    "evidence_anchor_ids",
    "page_indexes",
    "block_ids",
    "temporary_ids",
    "occurrence_ids",
    "origin_record_ids",
    "before",
    "after",
    "selected_node_ids",
    "selected_relation_ids",
    "primary_membership_by_node",
    "collapsed_scope_ids",
    "hidden_view_ids",
    "manual_positions",
    "counts",
    "warnings",
    "target_ids",
    "text_span",
    "bbox",
    "value_json",
}


class ReviewDocumentFormatError(ValueError):
    pass


class ReviewDocumentExporter:
    def __init__(self, *, workbook_exporter: XlsxWorkbookExporter | None = None) -> None:
        self._workbook_exporter = workbook_exporter or XlsxWorkbookExporter()

    def export_review_document(
        self,
        document: P2ReviewDocumentDTO,
        file_path: str | Path,
    ) -> str:
        errors = document.validate()
        if errors:
            raise ReviewDocumentFormatError(
                "审查文档引用或层级不完整：" + ", ".join(errors[:12])
            )
        base_export_id = _fingerprint(
            {
                "document_id": document.metadata.document_id,
                "revision": document.metadata.revision,
                "business": document.business_fingerprint(),
            }
        )[:24]
        sheets = self._sheet_rows(document, base_export_id=base_export_id)
        self._workbook_exporter.export_rows(
            sheets,
            file_path,
            hidden_sheet_names={
                "review_meta",
                "occurrences",
                "evidence",
                "migration_audit",
                "review_history",
                "view_state",
                "import_baseline",
            },
            atomic=True,
        )
        return base_export_id

    def _sheet_rows(
        self,
        document: P2ReviewDocumentDTO,
        *,
        base_export_id: str,
    ) -> dict[str, list[list[str]]]:
        metadata = asdict(document.metadata)
        metadata["base_export_id"] = base_export_id
        metadata["business_fingerprint"] = document.business_fingerprint()
        meta_rows = [["field", "value"]] + [
            [key, _cell(value)] for key, value in metadata.items()
        ]
        draft_rows = _legacy_draft_rows(
            document,
            document_to_legacy_drafts(document, include_archived=True),
        )
        object_sheets = {
            "scopes": _dataclass_rows(document.scopes, StructuralScopeDTO),
            "occurrences": _dataclass_rows(document.occurrences, KnowledgeOccurrenceDTO),
            "relations": _dataclass_rows(document.relations, ReviewRelationDTO),
            "evidence": _dataclass_rows(document.evidence, EvidenceAnchorDTO),
            "migration_audit": _dataclass_rows(document.migration_audit, MigrationAuditDTO),
            "review_history": _dataclass_rows(document.review_history, ReviewDecisionDTO),
            "export_profiles": _dataclass_rows(document.export_profiles, ExportProfileDTO),
            "view_state": _dataclass_rows(document.view_states, ReviewViewStateDTO),
        }
        baseline = _baseline_rows(document, base_export_id=base_export_id)
        return {
            "review_meta": meta_rows,
            "draft": draft_rows,
            **object_sheets,
            "import_baseline": baseline,
        }


class ReviewDocumentReader:
    REQUIRED_V2_SHEETS = {
        "review_meta",
        "draft",
        "scopes",
        "occurrences",
        "relations",
        "evidence",
        "migration_audit",
        "review_history",
        "export_profiles",
    }

    def __init__(self) -> None:
        self._reader = SimpleXlsxWorkbookReader(
            SimpleXlsxReaderConfig(json_columns=set(_JSON_COLUMNS))
        )

    def read_document(
        self,
        file_path: str | Path,
        *,
        migrate_v1: bool = True,
    ) -> P2ReviewDocumentDTO:
        path = Path(file_path)
        try:
            sheets = self._reader.read_sheets(path, list(REVIEW_DOCUMENT_SHEETS))
        except (KeyError, json.JSONDecodeError, OSError, ValueError) as exc:
            raise ReviewDocumentFormatError(f"无法读取审查包 {path.name}：{exc}") from exc
        if "review_meta" not in sheets:
            if not migrate_v1:
                raise ReviewDocumentFormatError("文件没有 review_meta，不能按 v2 写回。")
            drafts = DraftWorkbookReader().read(path)
            return ReviewDocumentMigrator().migrate(drafts)
        metadata_values = {
            str(row.get("field", "")): row.get("value", "")
            for row in sheets["review_meta"]
        }
        try:
            schema_version = int(metadata_values.get("schema_version", 0))
        except (TypeError, ValueError) as exc:
            raise ReviewDocumentFormatError("review_meta.schema_version 无效。") from exc
        if schema_version > REVIEW_DOCUMENT_SCHEMA_VERSION:
            raise ReviewDocumentFormatError(
                f"审查包 schema {schema_version} 高于当前支持的 {REVIEW_DOCUMENT_SCHEMA_VERSION}，拒绝有损写回。"
            )
        if schema_version != REVIEW_DOCUMENT_SCHEMA_VERSION:
            raise ReviewDocumentFormatError(f"不支持的审查包 schema：{schema_version}。")
        missing = sorted(self.REQUIRED_V2_SHEETS - set(sheets))
        if missing:
            raise ReviewDocumentFormatError("v2 审查包缺少工作表：" + ", ".join(missing))
        metadata = _metadata_from_values(metadata_values)
        nodes = self._read_nodes(sheets["draft"])
        document = P2ReviewDocumentDTO(
            metadata=metadata,
            nodes=nodes,
            scopes=_rows_to_dataclasses(sheets["scopes"], StructuralScopeDTO),
            occurrences=_rows_to_dataclasses(
                sheets["occurrences"], KnowledgeOccurrenceDTO
            ),
            relations=_rows_to_dataclasses(sheets["relations"], ReviewRelationDTO),
            evidence=_rows_to_dataclasses(sheets["evidence"], EvidenceAnchorDTO),
            migration_audit=_rows_to_dataclasses(
                sheets["migration_audit"], MigrationAuditDTO
            ),
            review_history=_rows_to_dataclasses(
                sheets["review_history"], ReviewDecisionDTO
            ),
            export_profiles=_rows_to_dataclasses(
                sheets["export_profiles"], ExportProfileDTO
            ),
            view_states=_rows_to_dataclasses(
                sheets.get("view_state", []), ReviewViewStateDTO
            ),
        )
        errors = document.validate()
        if errors:
            raise ReviewDocumentFormatError(
                "v2 审查包引用或层级无效：" + ", ".join(errors[:12])
            )
        return document

    @staticmethod
    def read_baseline(file_path: str | Path) -> list[dict[str, Any]]:
        reader = SimpleXlsxWorkbookReader(
            SimpleXlsxReaderConfig(json_columns={"value_json"})
        )
        sheets = reader.read_sheets(file_path, ["import_baseline"])
        return list(sheets.get("import_baseline", []))

    @staticmethod
    def _read_nodes(rows: list[dict[str, object]]) -> list[ReviewNodeDTO]:
        nodes: list[ReviewNodeDTO] = []
        for row in rows:
            required = [
                "candidate_node_id",
                "candidate_display_name",
                "candidate_node_name",
            ]
            if any(not str(row.get(key, "")).strip() for key in required):
                raise ReviewDocumentFormatError(
                    "draft 表的节点 ID、显示名和规范名不能为空。"
                )
            anchors = row.get("evidence_anchors", [])
            anchor_ids = [
                str(item.get("anchor_id", ""))
                for item in anchors
                if isinstance(item, dict) and str(item.get("anchor_id", ""))
            ]
            nodes.append(
                ReviewNodeDTO(
                    node_id=str(row["candidate_node_id"]),
                    display_name=str(row["candidate_display_name"]),
                    node_name=str(row["candidate_node_name"]),
                    node_type=str(row.get("knowledge_type", "concept")),
                    subject=str(row.get("subject", "")),
                    grade=str(row.get("grade", "")),
                    term=str(row.get("term", "")),
                    chapter=str(row.get("chapter", "")),
                    cognitive_level=str(row.get("cognitive_level", "")),
                    education_stage=str(row.get("education_stage", "")),
                    grade_band=str(row.get("grade_band", "")),
                    subject_tags=_string_list(row.get("subject_tags", [])),
                    source_id=str(row.get("source_id", "")),
                    source_path=str(row.get("source_path", "")),
                    source_format=str(row.get("source_format", "")),
                    source_document_type=str(row.get("source_document_type", "")),
                    source_text=str(row.get("source_text", "")),
                    source_location=str(row.get("source_location", "")),
                    evidence_anchor_ids=anchor_ids,
                    confidence=float(row.get("confidence", 1.0)),
                    reasoning_summary=str(row.get("reasoning_summary", "")),
                    extractor_source=str(row.get("extractor_source", "")),
                    review_status=str(row.get("review_status", "pending")),
                    review_notes=str(row.get("review_notes", "")),
                    lifecycle_state=str(row.get("lifecycle_state", "active")),
                    revision=int(row.get("revision", 0)),
                )
            )
        return nodes


def _metadata_from_values(values: dict[str, object]) -> ReviewDocumentMetadataDTO:
    return ReviewDocumentMetadataDTO(
        document_id=str(values.get("document_id", "")),
        source_identity=str(values.get("source_identity", "")),
        source_sha256=str(values.get("source_sha256", "")),
        selection_fingerprint=str(values.get("selection_fingerprint", "")),
        analysis_run_id=str(values.get("analysis_run_id", "")),
        schema_version=int(values.get("schema_version", REVIEW_DOCUMENT_SCHEMA_VERSION)),
        revision=int(values.get("revision", 0)),
        created_at=str(values.get("created_at", "")),
        updated_at=str(values.get("updated_at", "")),
    )


def _legacy_draft_rows(
    document: P2ReviewDocumentDTO,
    drafts: list[DraftKnowledgeItemDTO],
) -> list[list[str]]:
    columns = [field.name for field in fields(DraftKnowledgeItemDTO)] + [
        "lifecycle_state",
        "revision",
    ]
    rows = [columns]
    node_by_id = {node.node_id: node for node in document.nodes}
    for draft in drafts:
        payload = asdict(draft)
        node = node_by_id[draft.candidate_node_id]
        payload["lifecycle_state"] = node.lifecycle_state
        payload["revision"] = node.revision
        rows.append([_cell(payload.get(column, "")) for column in columns])
    return rows


T = TypeVar("T")


def _dataclass_rows(items: list[Any], data_type: type[Any]) -> list[list[str]]:
    columns = [item.name for item in fields(data_type)]
    rows = [columns]
    for item in items:
        payload = asdict(item)
        rows.append([_cell(payload.get(column, "")) for column in columns])
    return rows


def _rows_to_dataclasses(rows: list[dict[str, object]], data_type: type[T]) -> list[T]:
    data_fields = {item.name: item for item in fields(data_type)}
    results: list[T] = []
    for row in rows:
        payload = {
            key: _coerce_dataclass_value(key, value)
            for key, value in row.items()
            if key in data_fields
        }
        try:
            results.append(data_type(**payload))
        except (TypeError, ValueError) as exc:
            object_id = str(
                row.get("relation_id")
                or row.get("scope_id")
                or row.get("occurrence_id")
                or row.get("anchor_id")
                or "?"
            )
            raise ReviewDocumentFormatError(
                f"工作表对象 {object_id} 字段无效：{exc}"
            ) from exc
    return results


def _coerce_dataclass_value(field_name: str, value: object) -> object:
    if field_name in {
        "schema_version",
        "revision",
        "reading_order",
        "object_revision",
        "based_on_revision",
        "projection_schema_version",
        "layout_version",
        "view_revision",
    }:
        return int(value or 0)
    if field_name == "confidence":
        return float(value or 0.0)
    return value


def _baseline_rows(
    document: P2ReviewDocumentDTO,
    *,
    base_export_id: str,
) -> list[list[str]]:
    columns = [
        "base_export_id",
        "base_revision",
        "record_type",
        "object_id",
        "field_name",
        "value_json",
        "field_sha256",
    ]
    rows = [columns]
    groups = {
        "node": [(item.node_id, asdict(item)) for item in document.nodes],
        "scope": [(item.scope_id, asdict(item)) for item in document.scopes],
        "occurrence": [
            (item.occurrence_id, asdict(item)) for item in document.occurrences
        ],
        "relation": [
            (item.relation_id, asdict(item)) for item in document.relations
        ],
        "profile": [
            (item.profile_id, asdict(item)) for item in document.export_profiles
        ],
    }
    for record_type, objects in groups.items():
        for object_id, payload in objects:
            for field_name, value in payload.items():
                value_json = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                rows.append(
                    [
                        base_export_id,
                        str(document.metadata.revision),
                        record_type,
                        object_id,
                        field_name,
                        value_json,
                        hashlib.sha256(value_json.encode("utf-8")).hexdigest(),
                    ]
                )
    return rows


def _cell(value: object) -> str:
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value or "") else []


def _fingerprint(value: object) -> str:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


__all__ = [
    "REVIEW_DOCUMENT_SHEETS",
    "ReviewDocumentExporter",
    "ReviewDocumentFormatError",
    "ReviewDocumentReader",
]
