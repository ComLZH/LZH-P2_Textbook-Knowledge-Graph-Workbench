from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .review_document import (
    ExportProfileDTO,
    KnowledgeOccurrenceDTO,
    P2ReviewDocumentDTO,
    ReviewDecisionDTO,
    ReviewNodeDTO,
    ReviewRelationDTO,
    StructuralScopeDTO,
)
from .review_document_io import ReviewDocumentFormatError, ReviewDocumentReader


@dataclass(frozen=True, slots=True)
class ImportFieldChange:
    record_type: str
    object_id: str
    field_name: str
    baseline_value: Any
    local_value: Any
    imported_value: Any


@dataclass(frozen=True, slots=True)
class ImportConflict:
    record_type: str
    object_id: str
    field_name: str
    baseline_value: Any
    local_value: Any
    imported_value: Any
    code: str = "three_way_conflict"


@dataclass(slots=True)
class ExcelImportPlan:
    status: str
    base_export_id: str
    base_revision: int
    current_revision: int
    changes: list[ImportFieldChange] = field(default_factory=list)
    conflicts: list[ImportConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    imported_document: P2ReviewDocumentDTO | None = field(default=None, repr=False)

    @property
    def applicable(self) -> bool:
        return self.status == "ready" and not self.conflicts


class ExcelImportPlanner:
    """Plans and applies ID-based, field-level three-way merges for v2 review packages."""

    _GROUPS = {
        "node": ("nodes", "node_id", ReviewNodeDTO),
        "scope": ("scopes", "scope_id", StructuralScopeDTO),
        "occurrence": ("occurrences", "occurrence_id", KnowledgeOccurrenceDTO),
        "relation": ("relations", "relation_id", ReviewRelationDTO),
        "profile": ("export_profiles", "profile_id", ExportProfileDTO),
    }

    def __init__(self, *, reader: ReviewDocumentReader | None = None) -> None:
        self._reader = reader or ReviewDocumentReader()

    def plan(
        self,
        file_path: str | Path,
        current_document: P2ReviewDocumentDTO,
    ) -> ExcelImportPlan:
        imported = self._reader.read_document(file_path, migrate_v1=False)
        if imported.metadata.document_id != current_document.metadata.document_id:
            return ExcelImportPlan(
                status="blocked",
                base_export_id="",
                base_revision=-1,
                current_revision=current_document.metadata.revision,
                warnings=[
                    "Excel 审查包 document_id 与当前会话不一致；请使用“打开审查包”而不是合并。"
                ],
                imported_document=imported,
            )
        baseline_rows = self._reader.read_baseline(file_path)
        if not baseline_rows:
            return ExcelImportPlan(
                status="blocked",
                base_export_id="",
                base_revision=-1,
                current_revision=current_document.metadata.revision,
                warnings=["缺少可信 import_baseline，不能假装进行精确三方合并。"],
                imported_document=imported,
            )
        baseline: dict[tuple[str, str, str], Any] = {}
        export_ids: set[str] = set()
        revisions: set[int] = set()
        for row in baseline_rows:
            record_type = str(row.get("record_type", ""))
            object_id = str(row.get("object_id", ""))
            field_name = str(row.get("field_name", ""))
            value = row.get("value_json")
            export_ids.add(str(row.get("base_export_id", "")))
            try:
                revisions.add(int(row.get("base_revision", 0)))
                expected_hash = str(row.get("field_sha256", ""))
                canonical = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                if not expected_hash or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != expected_hash:
                    raise ReviewDocumentFormatError(
                        f"导入基线哈希不匹配：{record_type}/{object_id}/{field_name}"
                    )
            except (TypeError, ValueError) as exc:
                raise ReviewDocumentFormatError("导入基线 revision 无效。") from exc
            if not record_type or not object_id or not field_name:
                raise ReviewDocumentFormatError("导入基线存在缺失身份字段。")
            baseline[(record_type, object_id, field_name)] = value
        if len(export_ids) != 1 or len(revisions) != 1:
            raise ReviewDocumentFormatError("导入基线混入多个导出版本。")
        current_values = self._document_values(current_document)
        imported_values = self._document_values(imported)
        changes: list[ImportFieldChange] = []
        conflicts: list[ImportConflict] = []
        warnings: list[str] = []
        identities = set(baseline) | set(current_values) | set(imported_values)
        for key in sorted(identities):
            record_type, object_id, field_name = key
            base_exists = key in baseline
            local_exists = key in current_values
            remote_exists = key in imported_values
            base = baseline.get(key)
            local = current_values.get(key)
            remote = imported_values.get(key)
            if base_exists and not remote_exists:
                warnings.append(
                    f"忽略 Excel 隐式删除：{record_type}/{object_id}/{field_name}；删除须走显式归档命令。"
                )
                continue
            if not remote_exists:
                continue
            if not base_exists:
                if not local_exists or _equivalent(local, remote):
                    if not local_exists:
                        changes.append(
                            ImportFieldChange(record_type, object_id, field_name, None, None, remote)
                        )
                else:
                    conflicts.append(
                        ImportConflict(record_type, object_id, field_name, None, local, remote, "new_object_conflict")
                    )
                continue
            remote_changed = not _equivalent(remote, base)
            local_changed = not local_exists or not _equivalent(local, base)
            if not remote_changed:
                continue
            if not local_changed:
                changes.append(
                    ImportFieldChange(record_type, object_id, field_name, base, local, remote)
                )
            elif _equivalent(local, remote):
                continue
            else:
                conflicts.append(
                    ImportConflict(record_type, object_id, field_name, base, local, remote)
                )
        status = "conflicts" if conflicts else "ready"
        return ExcelImportPlan(
            status=status,
            base_export_id=next(iter(export_ids)),
            base_revision=next(iter(revisions)),
            current_revision=current_document.metadata.revision,
            changes=changes,
            conflicts=conflicts,
            warnings=list(dict.fromkeys(warnings)),
            imported_document=imported,
        )

    def apply(
        self,
        plan: ExcelImportPlan,
        current_document: P2ReviewDocumentDTO,
        *,
        expected_revision: int,
    ) -> P2ReviewDocumentDTO:
        if expected_revision != current_document.metadata.revision or expected_revision != plan.current_revision:
            raise ValueError("导入计划基于较旧会话，必须重新预览。")
        if not plan.applicable:
            raise ValueError("导入计划尚有冲突或缺少可信基线。")
        merged = deepcopy(current_document)
        imported = plan.imported_document
        if imported is None:
            raise ValueError("导入计划缺少来源快照。")
        imported_objects = self._objects(imported)
        changed_content: set[tuple[str, str]] = set()
        applied: list[dict[str, Any]] = []
        for change in plan.changes:
            collection_name, id_field, data_type = self._GROUPS[change.record_type]
            collection = getattr(merged, collection_name)
            target = next(
                (item for item in collection if getattr(item, id_field) == change.object_id), None
            )
            if target is None:
                imported_target = imported_objects.get((change.record_type, change.object_id))
                if imported_target is None:
                    raise ValueError(f"导入对象已不存在：{change.record_type}/{change.object_id}")
                target = deepcopy(imported_target)
                collection.append(target)
            setattr(target, change.field_name, deepcopy(change.imported_value))
            if hasattr(target, "revision") and change.field_name != "revision":
                target.revision += 1
            if change.record_type in {"node", "relation"} and change.field_name not in {
                "review_status",
                "review_notes",
                "revision",
                "lifecycle_state",
            }:
                changed_content.add((change.record_type, change.object_id))
            applied.append(
                {
                    "record_type": change.record_type,
                    "object_id": change.object_id,
                    "field_name": change.field_name,
                    "before": change.local_value,
                    "after": change.imported_value,
                }
            )
        for record_type, object_id in changed_content:
            target = self._objects(merged).get((record_type, object_id))
            if target is not None and getattr(target, "review_status", "") == "accepted":
                setattr(target, "review_status", "needs_revision")
        errors = merged.validate()
        if errors:
            raise ValueError("导入结果会破坏文档完整性：" + ", ".join(errors[:12]))
        if not applied:
            return merged
        merged.metadata.revision = current_document.metadata.revision + 1
        merged.metadata.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        merged.review_history.append(
            ReviewDecisionDTO(
                decision_id=f"decision_{uuid4().hex}",
                object_type="document",
                object_id=merged.metadata.document_id,
                object_revision=merged.metadata.revision,
                action="excel_three_way_merge",
                before={"base_export_id": plan.base_export_id},
                after={"changes": applied},
                created_at=merged.metadata.updated_at,
            )
        )
        return merged

    @staticmethod
    def resolve_conflicts(
        plan: ExcelImportPlan,
        *,
        use_imported_values: bool,
    ) -> ExcelImportPlan:
        """Resolve every displayed conflict explicitly before an atomic apply."""

        resolved = deepcopy(plan)
        if not resolved.conflicts:
            return resolved
        if use_imported_values:
            resolved.changes.extend(
                ImportFieldChange(
                    conflict.record_type,
                    conflict.object_id,
                    conflict.field_name,
                    conflict.baseline_value,
                    conflict.local_value,
                    conflict.imported_value,
                )
                for conflict in resolved.conflicts
            )
            resolved.warnings.append(
                f"用户明确选择采用 Excel 值解决 {len(resolved.conflicts)} 个冲突。"
            )
        else:
            resolved.warnings.append(
                f"用户明确选择保留当前会话值，忽略 {len(resolved.conflicts)} 个 Excel 冲突。"
            )
        resolved.conflicts = []
        resolved.status = "ready"
        return resolved

    def _document_values(self, document: P2ReviewDocumentDTO) -> dict[tuple[str, str, str], Any]:
        values: dict[tuple[str, str, str], Any] = {}
        for record_type, (collection_name, id_field, _data_type) in self._GROUPS.items():
            for item in getattr(document, collection_name):
                object_id = str(getattr(item, id_field))
                for field_name, value in asdict(item).items():
                    values[(record_type, object_id, field_name)] = value
        return values

    def _objects(self, document: P2ReviewDocumentDTO) -> dict[tuple[str, str], object]:
        return {
            (record_type, str(getattr(item, id_field))): item
            for record_type, (collection_name, id_field, _data_type) in self._GROUPS.items()
            for item in getattr(document, collection_name)
        }


def _equivalent(left: Any, right: Any) -> bool:
    return json.dumps(left, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


__all__ = [
    "ExcelImportPlan",
    "ExcelImportPlanner",
    "ImportConflict",
    "ImportFieldChange",
]
