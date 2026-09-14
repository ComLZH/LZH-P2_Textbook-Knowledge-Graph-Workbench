from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .contracts import (
    ALLOWED_REVIEW_STATUSES,
    DraftKnowledgeItemDTO,
    EvidenceAnchorDTO,
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_PENDING,
)
from .utils.graph_semantics import is_container_node_type, normalize_node_type, normalize_relation_type
from .utils.review_status import normalize_review_status


REVIEW_DOCUMENT_SCHEMA_VERSION = 2

RELATION_FAMILY_STRUCTURE = "structure"
RELATION_FAMILY_MEMBERSHIP = "membership"
RELATION_FAMILY_SEMANTIC = "semantic"
RELATION_FAMILY_UNRESOLVED = "unresolved"
ALLOWED_RELATION_FAMILIES = {
    RELATION_FAMILY_STRUCTURE,
    RELATION_FAMILY_MEMBERSHIP,
    RELATION_FAMILY_SEMANTIC,
    RELATION_FAMILY_UNRESOLVED,
}

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_ARCHIVED = "archived"
ALLOWED_LIFECYCLE_STATES = {LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED}


@dataclass(slots=True)
class ReviewDocumentMetadataDTO:
    document_id: str
    source_identity: str
    source_sha256: str = ""
    selection_fingerprint: str = ""
    analysis_run_id: str = ""
    schema_version: int = REVIEW_DOCUMENT_SCHEMA_VERSION
    revision: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class ReviewNodeDTO:
    node_id: str
    display_name: str
    node_name: str
    node_type: str = "concept"
    subject: str = ""
    grade: str = ""
    term: str = ""
    chapter: str = ""
    cognitive_level: str = ""
    education_stage: str = ""
    grade_band: str = ""
    subject_tags: list[str] = field(default_factory=list)
    source_id: str = ""
    source_path: str = ""
    source_format: str = ""
    source_document_type: str = ""
    source_text: str = ""
    source_location: str = ""
    evidence_anchor_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    reasoning_summary: str = ""
    extractor_source: str = ""
    review_status: str = REVIEW_STATUS_PENDING
    review_notes: str = ""
    lifecycle_state: str = LIFECYCLE_ACTIVE
    revision: int = 0


@dataclass(slots=True)
class StructuralScopeDTO:
    scope_id: str
    source_identity: str
    structural_role: str
    structural_number: str = ""
    reading_order: int = 0
    evidence_anchor_ids: list[str] = field(default_factory=list)
    review_status: str = REVIEW_STATUS_PENDING
    lifecycle_state: str = LIFECYCLE_ACTIVE
    revision: int = 0


@dataclass(slots=True)
class KnowledgeOccurrenceDTO:
    occurrence_id: str
    node_id: str
    source_identity: str
    chapter_hint: str = ""
    section_hint: str = ""
    page_indexes: list[int] = field(default_factory=list)
    block_ids: list[str] = field(default_factory=list)
    temporary_ids: list[str] = field(default_factory=list)
    evidence_anchor_ids: list[str] = field(default_factory=list)
    lifecycle_state: str = LIFECYCLE_ACTIVE
    revision: int = 0


@dataclass(slots=True)
class ReviewRelationDTO:
    relation_id: str
    relation_family: str
    source_node_id: str
    target_node_id: str
    relation_type: str
    evidence_anchor_ids: list[str] = field(default_factory=list)
    occurrence_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    reasoning_summary: str = ""
    relation_source: str = ""
    review_status: str = REVIEW_STATUS_PENDING
    lifecycle_state: str = LIFECYCLE_ACTIVE
    origin_record_ids: list[str] = field(default_factory=list)
    original_relation_type: str = ""
    original_review_status: str = ""
    proposed_relation_type: str = ""
    migration_version: str = ""
    revision: int = 0


@dataclass(slots=True)
class ReviewDecisionDTO:
    decision_id: str
    object_type: str
    object_id: str
    object_revision: int
    action: str
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)
    actor: str = "local_user"
    reason: str = ""
    created_at: str = ""


@dataclass(slots=True)
class ExportProfileDTO:
    profile_id: str
    target_protocol: str = "p4_v1_single_membership"
    selected_node_ids: list[str] = field(default_factory=list)
    selected_relation_ids: list[str] = field(default_factory=list)
    primary_membership_by_node: dict[str, str] = field(default_factory=dict)
    based_on_revision: int = 0


@dataclass(slots=True)
class ReviewViewStateDTO:
    renderer: str
    projection_schema_version: int = 2
    layout_version: int = 6
    view_mode: str = "relations"
    structure_fingerprint: str = ""
    view_revision: int = 0
    focused_scope_id: str = ""
    collapsed_scope_ids: list[str] = field(default_factory=list)
    hidden_view_ids: list[str] = field(default_factory=list)
    manual_positions: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(slots=True)
class MigrationAuditDTO:
    migration_id: str
    migration_version: str
    source_schema_version: int
    created_at: str
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class P2ReviewDocumentDTO:
    metadata: ReviewDocumentMetadataDTO
    nodes: list[ReviewNodeDTO] = field(default_factory=list)
    scopes: list[StructuralScopeDTO] = field(default_factory=list)
    occurrences: list[KnowledgeOccurrenceDTO] = field(default_factory=list)
    relations: list[ReviewRelationDTO] = field(default_factory=list)
    evidence: list[EvidenceAnchorDTO] = field(default_factory=list)
    review_history: list[ReviewDecisionDTO] = field(default_factory=list)
    export_profiles: list[ExportProfileDTO] = field(default_factory=list)
    view_states: list[ReviewViewStateDTO] = field(default_factory=list)
    migration_audit: list[MigrationAuditDTO] = field(default_factory=list)

    def node_by_id(self, node_id: str) -> ReviewNodeDTO | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def relation_by_id(self, relation_id: str) -> ReviewRelationDTO | None:
        return next(
            (relation for relation in self.relations if relation.relation_id == relation_id),
            None,
        )

    def profile_by_id(self, profile_id: str) -> ExportProfileDTO | None:
        return next(
            (profile for profile in self.export_profiles if profile.profile_id == profile_id),
            None,
        )

    def active_nodes(self) -> list[ReviewNodeDTO]:
        return [node for node in self.nodes if node.lifecycle_state == LIFECYCLE_ACTIVE]

    def active_relations(self) -> list[ReviewRelationDTO]:
        return [
            relation
            for relation in self.relations
            if relation.lifecycle_state == LIFECYCLE_ACTIVE
        ]

    def business_fingerprint(self) -> str:
        payload = {
            "source_identity": self.metadata.source_identity,
            "source_sha256": self.metadata.source_sha256,
            "selection_fingerprint": self.metadata.selection_fingerprint,
            "nodes": [asdict(item) for item in self.nodes],
            "scopes": [asdict(item) for item in self.scopes],
            "occurrences": [asdict(item) for item in self.occurrences],
            "relations": [asdict(item) for item in self.relations],
            "export_profiles": [asdict(item) for item in self.export_profiles],
        }
        return _fingerprint(payload)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.metadata.schema_version != REVIEW_DOCUMENT_SCHEMA_VERSION:
            errors.append(f"schema_version_invalid:{self.metadata.schema_version}")
        if not self.metadata.document_id:
            errors.append("document_id_missing")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            errors.append("duplicate_node_id")
        relation_ids = [relation.relation_id for relation in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            errors.append("duplicate_relation_id")
        occurrence_ids = [item.occurrence_id for item in self.occurrences]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            errors.append("duplicate_occurrence_id")
        node_id_set = set(node_ids)
        occurrence_id_set = set(occurrence_ids)
        evidence_ids = {anchor.anchor_id for anchor in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            errors.append("duplicate_evidence_id")
        scope_id_values = [scope.scope_id for scope in self.scopes]
        scope_ids = set(scope_id_values)
        if len(scope_ids) != len(scope_id_values):
            errors.append("duplicate_scope_id")
        profile_ids = [profile.profile_id for profile in self.export_profiles]
        if len(profile_ids) != len(set(profile_ids)):
            errors.append("duplicate_profile_id")
        node_lifecycle_by_id = {node.node_id: node.lifecycle_state for node in self.nodes}
        for node in self.nodes:
            if node.lifecycle_state not in ALLOWED_LIFECYCLE_STATES:
                errors.append(f"node_lifecycle_invalid:{node.node_id}")
            if node.review_status not in ALLOWED_REVIEW_STATUSES:
                errors.append(f"node_review_status_invalid:{node.node_id}")
        for scope in self.scopes:
            if scope.scope_id not in node_id_set:
                errors.append(f"scope_node_missing:{scope.scope_id}")
            if scope.structural_role not in {"chapter", "section", "subsection"}:
                errors.append(f"scope_role_invalid:{scope.scope_id}")
            if scope.lifecycle_state not in ALLOWED_LIFECYCLE_STATES:
                errors.append(f"scope_lifecycle_invalid:{scope.scope_id}")
            if scope.review_status not in ALLOWED_REVIEW_STATUSES:
                errors.append(f"scope_review_status_invalid:{scope.scope_id}")
            if node_lifecycle_by_id.get(scope.scope_id) != scope.lifecycle_state:
                errors.append(f"scope_node_lifecycle_mismatch:{scope.scope_id}")
        for occurrence in self.occurrences:
            if occurrence.node_id not in node_id_set:
                errors.append(f"occurrence_node_missing:{occurrence.occurrence_id}")
            if occurrence.lifecycle_state not in ALLOWED_LIFECYCLE_STATES:
                errors.append(f"occurrence_lifecycle_invalid:{occurrence.occurrence_id}")
            if (
                occurrence.lifecycle_state == LIFECYCLE_ACTIVE
                and node_lifecycle_by_id.get(occurrence.node_id) == LIFECYCLE_ARCHIVED
            ):
                errors.append(f"active_occurrence_archived_node:{occurrence.occurrence_id}")
            for anchor_id in occurrence.evidence_anchor_ids:
                if anchor_id not in evidence_ids:
                    errors.append(f"occurrence_evidence_missing:{occurrence.occurrence_id}:{anchor_id}")
        structure_parent_by_child: dict[str, list[str]] = {}
        relation_facts: set[tuple[str, str, str, str]] = set()
        for relation in self.relations:
            if relation.relation_family not in ALLOWED_RELATION_FAMILIES:
                errors.append(f"relation_family_invalid:{relation.relation_id}")
            if relation.source_node_id not in node_id_set or relation.target_node_id not in node_id_set:
                errors.append(f"relation_endpoint_missing:{relation.relation_id}")
            if relation.lifecycle_state not in ALLOWED_LIFECYCLE_STATES:
                errors.append(f"relation_lifecycle_invalid:{relation.relation_id}")
            if relation.review_status not in ALLOWED_REVIEW_STATUSES:
                errors.append(f"relation_review_status_invalid:{relation.relation_id}")
            if relation.lifecycle_state == LIFECYCLE_ACTIVE and any(
                node_lifecycle_by_id.get(endpoint_id) == LIFECYCLE_ARCHIVED
                for endpoint_id in (relation.source_node_id, relation.target_node_id)
            ):
                errors.append(f"active_relation_archived_endpoint:{relation.relation_id}")
            fact = (
                relation.relation_family,
                relation.source_node_id,
                relation.relation_type,
                relation.target_node_id,
            )
            if fact in relation_facts:
                errors.append(f"duplicate_relation_fact:{relation.relation_id}")
            relation_facts.add(fact)
            for occurrence_id in relation.occurrence_ids:
                if occurrence_id not in occurrence_id_set:
                    errors.append(f"relation_occurrence_missing:{relation.relation_id}:{occurrence_id}")
            if relation.relation_family == RELATION_FAMILY_STRUCTURE:
                if relation.relation_type != "contains":
                    errors.append(f"structure_relation_type_invalid:{relation.relation_id}")
                if relation.source_node_id not in scope_ids or relation.target_node_id not in scope_ids:
                    errors.append(f"structure_endpoint_role_invalid:{relation.relation_id}")
                if relation.lifecycle_state == LIFECYCLE_ACTIVE and normalize_review_status(
                    relation.review_status
                ) != "rejected":
                    structure_parent_by_child.setdefault(relation.target_node_id, []).append(
                        relation.source_node_id
                    )
            if relation.relation_family == RELATION_FAMILY_MEMBERSHIP:
                if relation.relation_type != "contains":
                    errors.append(f"membership_relation_type_invalid:{relation.relation_id}")
                if relation.source_node_id not in scope_ids:
                    errors.append(f"membership_scope_missing:{relation.relation_id}")
                if relation.target_node_id in scope_ids:
                    errors.append(f"membership_target_is_scope:{relation.relation_id}")
        for child_id, parents in structure_parent_by_child.items():
            if len(set(parents)) > 1:
                errors.append(f"structure_multiple_parents:{child_id}")
        errors.extend(_structure_cycle_errors(structure_parent_by_child))
        relation_id_set = set(relation_ids)
        membership_ids = {
            relation.relation_id
            for relation in self.relations
            if relation.relation_family == RELATION_FAMILY_MEMBERSHIP
        }
        for profile in self.export_profiles:
            missing_nodes = set(profile.selected_node_ids) - node_id_set
            missing_relations = set(profile.selected_relation_ids) - relation_id_set
            if missing_nodes:
                errors.append(f"profile_node_missing:{profile.profile_id}")
            if missing_relations:
                errors.append(f"profile_relation_missing:{profile.profile_id}")
            if set(profile.primary_membership_by_node.values()) - membership_ids:
                errors.append(f"profile_membership_missing:{profile.profile_id}")
        return list(dict.fromkeys(errors))


class ReviewDocumentMigrator:
    """Converts legacy draft rows into the lossless schema-v2 review document."""

    MIGRATION_VERSION = "legacy_draft_to_review_v2_1"

    def migrate(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        *,
        source_identity: str = "",
        source_sha256: str = "",
        selection_fingerprint: str = "",
        analysis_run_id: str = "",
        occurrence_hints: dict[str, dict[str, Any] | list[dict[str, Any]]] | None = None,
    ) -> P2ReviewDocumentDTO:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        source_identity = source_identity or self._source_identity(drafts, source_sha256)
        document_id = _stable_id("doc", source_identity, source_sha256, selection_fingerprint)
        evidence = self._collect_evidence(drafts)
        evidence_by_node: dict[str, list[str]] = {}
        for anchor in evidence:
            for target_id in anchor.target_ids:
                if target_id:
                    evidence_by_node.setdefault(target_id, []).append(anchor.anchor_id)
        nodes = [self._node_from_draft(draft, evidence_by_node) for draft in drafts]
        node_by_id = {node.node_id: node for node in nodes}
        scope_ids = {
            node.node_id for node in nodes if is_container_node_type(node.node_type)
        }
        relations = self._relations_from_drafts(drafts, node_by_id, scope_ids)
        scopes = self._scopes(nodes, relations, source_identity)
        scope_ids = {scope.scope_id for scope in scopes}
        occurrences = self._occurrences(
            drafts,
            source_identity=source_identity,
            evidence_by_node=evidence_by_node,
            occurrence_hints=occurrence_hints or {},
            scope_ids=scope_ids,
        )
        warnings: list[str] = []
        self._add_missing_memberships(
            nodes=nodes,
            scopes=scopes,
            occurrences=occurrences,
            relations=relations,
            warnings=warnings,
        )
        document = P2ReviewDocumentDTO(
            metadata=ReviewDocumentMetadataDTO(
                document_id=document_id,
                source_identity=source_identity,
                source_sha256=source_sha256,
                selection_fingerprint=selection_fingerprint,
                analysis_run_id=analysis_run_id,
                created_at=now,
                updated_at=now,
            ),
            nodes=nodes,
            scopes=scopes,
            occurrences=occurrences,
            relations=relations,
            evidence=evidence,
            migration_audit=[
                MigrationAuditDTO(
                    migration_id=_stable_id("migration", document_id, self.MIGRATION_VERSION),
                    migration_version=self.MIGRATION_VERSION,
                    source_schema_version=1,
                    created_at=now,
                    counts={
                        "nodes": len(nodes),
                        "scopes": len(scopes),
                        "occurrences": len(occurrences),
                        "relations": len(relations),
                        "memberships": sum(
                            relation.relation_family == RELATION_FAMILY_MEMBERSHIP
                            for relation in relations
                        ),
                    },
                    warnings=warnings,
                )
            ],
        )
        return document

    @staticmethod
    def _source_identity(drafts: list[DraftKnowledgeItemDTO], source_sha256: str) -> str:
        source_ids = [draft.source_id for draft in drafts if draft.source_id]
        if source_ids:
            return source_ids[0]
        return f"sha256:{source_sha256}" if source_sha256 else "legacy-local-source"

    @staticmethod
    def _collect_evidence(drafts: list[DraftKnowledgeItemDTO]) -> list[EvidenceAnchorDTO]:
        anchors: list[EvidenceAnchorDTO] = []
        used_ids: set[str] = set()
        for draft in drafts:
            for index, anchor in enumerate(draft.evidence_anchors, start=1):
                anchor_id = anchor.anchor_id or _stable_id(
                    "evidence", draft.candidate_node_id, anchor.source_location, index
                )
                if anchor_id in used_ids:
                    continue
                used_ids.add(anchor_id)
                payload = asdict(anchor)
                payload["anchor_id"] = anchor_id
                payload["review_status"] = normalize_review_status(anchor.review_status)
                anchors.append(EvidenceAnchorDTO(**payload))
        return anchors

    @staticmethod
    def _node_from_draft(
        draft: DraftKnowledgeItemDTO,
        evidence_by_node: dict[str, list[str]],
    ) -> ReviewNodeDTO:
        return ReviewNodeDTO(
            node_id=draft.candidate_node_id,
            display_name=draft.candidate_display_name,
            node_name=draft.candidate_node_name,
            node_type=normalize_node_type(draft.knowledge_type),
            subject=draft.subject,
            grade=draft.grade,
            term=draft.term,
            chapter=draft.chapter,
            cognitive_level=draft.cognitive_level,
            education_stage=draft.education_stage,
            grade_band=draft.grade_band,
            subject_tags=list(draft.subject_tags),
            source_id=draft.source_id,
            source_path=draft.source_path,
            source_format=draft.source_format,
            source_document_type=draft.source_document_type,
            source_text=draft.source_text,
            source_location=draft.source_location,
            evidence_anchor_ids=list(evidence_by_node.get(draft.candidate_node_id, [])),
            confidence=draft.confidence,
            reasoning_summary=draft.reasoning_summary,
            extractor_source=draft.extractor_source,
            review_status=normalize_review_status(draft.review_status),
            review_notes=draft.review_notes,
        )

    def _relations_from_drafts(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        node_by_id: dict[str, ReviewNodeDTO],
        scope_ids: set[str],
    ) -> list[ReviewRelationDTO]:
        relations: list[ReviewRelationDTO] = []
        seen_legacy_keys: set[tuple[str, str, str]] = set()
        relation_by_key: dict[tuple[str, str, str], ReviewRelationDTO] = {}
        names = {
            value: draft.candidate_node_id
            for draft in drafts
            for value in {
                draft.candidate_node_id,
                draft.candidate_node_name,
                draft.candidate_display_name,
            }
            if value
        }
        for draft in drafts:
            for index, raw in enumerate(draft.candidate_relations, start=1):
                source_id = names.get(
                    str(raw.get("source_node_id") or draft.candidate_node_id).strip(),
                    str(raw.get("source_node_id") or draft.candidate_node_id).strip(),
                )
                target_id = names.get(
                    str(raw.get("target_node_id") or "").strip(),
                    str(raw.get("target_node_id") or "").strip(),
                )
                relation_type = normalize_relation_type(raw.get("relation_type"), default="")
                if not source_id or not target_id or not relation_type:
                    continue
                family = self._classify_family(source_id, target_id, relation_type, scope_ids)
                status = normalize_review_status(raw.get("review_status", draft.review_status))
                origin_id = str(raw.get("relation_id") or raw.get("claim_id") or "")
                key = (source_id, relation_type, target_id)
                if key in relation_by_key:
                    existing = relation_by_key[key]
                    if origin_id and origin_id not in existing.origin_record_ids:
                        existing.origin_record_ids.append(origin_id)
                    if status != existing.review_status:
                        existing.review_status = "needs_expert_review"
                        existing.reasoning_summary = (
                            existing.reasoning_summary
                            or "重复旧关系的审核状态不一致，需人工确认。"
                        )
                    continue
                relation_id = origin_id or _stable_id(
                    "relation", family, source_id, target_id, relation_type
                )
                relation_record = ReviewRelationDTO(
                        relation_id=relation_id,
                        relation_family=family,
                        source_node_id=source_id,
                        target_node_id=target_id,
                        relation_type=relation_type,
                        confidence=float(raw.get("confidence", 0.8)),
                        reasoning_summary=str(raw.get("reasoning_summary", "")),
                        relation_source=str(raw.get("relation_source", "candidate_relations")),
                        review_status=status,
                        origin_record_ids=[origin_id] if origin_id else [],
                        original_relation_type=relation_type,
                        original_review_status=status,
                        migration_version=self.MIGRATION_VERSION,
                    )
                relations.append(relation_record)
                relation_by_key[key] = relation_record
                seen_legacy_keys.add(key)
        for draft in drafts:
            child_id = draft.candidate_node_id
            parent_id = names.get(draft.candidate_parent_node_id, draft.candidate_parent_node_id)
            if parent_id and (parent_id, "contains", child_id) not in seen_legacy_keys:
                family = self._classify_family(parent_id, child_id, "contains", scope_ids)
                relations.append(
                    ReviewRelationDTO(
                        relation_id=_stable_id("relation", "legacy_parent", parent_id, child_id),
                        relation_family=family,
                        source_node_id=parent_id,
                        target_node_id=child_id,
                        relation_type="contains",
                        relation_source="legacy_parent",
                        review_status=normalize_review_status(draft.review_status),
                        original_relation_type="contains",
                        original_review_status=normalize_review_status(draft.review_status),
                        migration_version=self.MIGRATION_VERSION,
                    )
                )
                seen_legacy_keys.add((parent_id, "contains", child_id))
            for prerequisite in draft.candidate_prerequisites:
                source_id = names.get(prerequisite, prerequisite)
                key = (source_id, "prerequisite", child_id)
                if not source_id or key in seen_legacy_keys:
                    continue
                relations.append(
                    ReviewRelationDTO(
                        relation_id=_stable_id(
                            "relation", "legacy_prerequisite", source_id, child_id
                        ),
                        relation_family=RELATION_FAMILY_SEMANTIC,
                        source_node_id=source_id,
                        target_node_id=child_id,
                        relation_type="prerequisite",
                        relation_source="legacy_prerequisite_list",
                        review_status=normalize_review_status(draft.review_status),
                        original_relation_type="prerequisite",
                        original_review_status=normalize_review_status(draft.review_status),
                        migration_version=self.MIGRATION_VERSION,
                    )
                )
                seen_legacy_keys.add(key)
        return relations

    @staticmethod
    def _classify_family(
        source_id: str,
        target_id: str,
        relation_type: str,
        scope_ids: set[str],
    ) -> str:
        if relation_type != "contains":
            return RELATION_FAMILY_SEMANTIC
        if source_id in scope_ids and target_id in scope_ids:
            return RELATION_FAMILY_STRUCTURE
        if source_id in scope_ids and target_id not in scope_ids:
            return RELATION_FAMILY_MEMBERSHIP
        return RELATION_FAMILY_UNRESOLVED

    @staticmethod
    def _scopes(
        nodes: list[ReviewNodeDTO],
        relations: list[ReviewRelationDTO],
        source_identity: str,
    ) -> list[StructuralScopeDTO]:
        scope_nodes = [node for node in nodes if is_container_node_type(node.node_type)]
        scope_ids = {node.node_id for node in scope_nodes}
        parent_by_child = {
            relation.target_node_id: relation.source_node_id
            for relation in relations
            if relation.relation_family == RELATION_FAMILY_STRUCTURE
            and relation.source_node_id in scope_ids
            and relation.target_node_id in scope_ids
            and normalize_review_status(relation.review_status) != "rejected"
        }
        scopes: list[StructuralScopeDTO] = []
        for order, node in enumerate(scope_nodes):
            depth = 0
            current = node.node_id
            seen = {current}
            while current in parent_by_child and parent_by_child[current] not in seen:
                current = parent_by_child[current]
                seen.add(current)
                depth += 1
            role = "chapter" if depth == 0 else "section" if depth == 1 else "subsection"
            scopes.append(
                StructuralScopeDTO(
                    scope_id=node.node_id,
                    source_identity=source_identity,
                    structural_role=role,
                    structural_number=_structural_number(node.display_name, node.chapter),
                    reading_order=order,
                    evidence_anchor_ids=list(node.evidence_anchor_ids),
                    review_status=node.review_status,
                )
            )
        return scopes

    @staticmethod
    def _occurrences(
        drafts: list[DraftKnowledgeItemDTO],
        *,
        source_identity: str,
        evidence_by_node: dict[str, list[str]],
        occurrence_hints: dict[str, dict[str, Any] | list[dict[str, Any]]],
        scope_ids: set[str],
    ) -> list[KnowledgeOccurrenceDTO]:
        occurrences: list[KnowledgeOccurrenceDTO] = []
        for draft in drafts:
            if draft.candidate_node_id in scope_ids:
                continue
            raw_hints = occurrence_hints.get(draft.candidate_node_id, {})
            hints = raw_hints if isinstance(raw_hints, list) else [raw_hints]
            if not hints:
                hints = [{}]
            anchors = [
                anchor
                for anchor in draft.evidence_anchors
                if anchor.anchor_id in evidence_by_node.get(draft.candidate_node_id, [])
            ]
            page_indexes = list(
                dict.fromkeys(anchor.page_index for anchor in anchors if anchor.page_index is not None)
            )
            block_ids = list(dict.fromkeys(anchor.block_id for anchor in anchors if anchor.block_id))
            for hint in hints:
                chapter_hint = str(hint.get("chapter") or draft.chapter or "")
                section_hint = str(hint.get("section") or draft.section or "")
                temporary_ids = [
                    str(value) for value in hint.get("temporary_ids", []) if str(value)
                ]
                occurrence_id = _stable_id(
                    "occurrence",
                    source_identity,
                    draft.candidate_node_id,
                    chapter_hint,
                    section_hint,
                    ",".join(map(str, page_indexes)),
                    ",".join(block_ids),
                    ",".join(temporary_ids),
                )
                occurrences.append(
                    KnowledgeOccurrenceDTO(
                        occurrence_id=occurrence_id,
                        node_id=draft.candidate_node_id,
                        source_identity=source_identity,
                        chapter_hint=chapter_hint,
                        section_hint=section_hint,
                        page_indexes=page_indexes,
                        block_ids=block_ids,
                        temporary_ids=temporary_ids,
                        evidence_anchor_ids=list(
                            evidence_by_node.get(draft.candidate_node_id, [])
                        ),
                    )
                )
        return occurrences

    def _add_missing_memberships(
        self,
        *,
        nodes: list[ReviewNodeDTO],
        scopes: list[StructuralScopeDTO],
        occurrences: list[KnowledgeOccurrenceDTO],
        relations: list[ReviewRelationDTO],
        warnings: list[str],
    ) -> None:
        scope_by_number: dict[str, list[StructuralScopeDTO]] = {}
        for scope in scopes:
            if scope.structural_number:
                scope_by_number.setdefault(_normalize_number(scope.structural_number), []).append(scope)
        existing = {
            (relation.source_node_id, relation.target_node_id)
            for relation in relations
            if relation.relation_family == RELATION_FAMILY_MEMBERSHIP
        }
        node_by_id = {node.node_id: node for node in nodes}
        for occurrence in occurrences:
            if not occurrence.section_hint:
                warnings.append(f"unresolved_membership:{occurrence.node_id}:section_missing")
                continue
            candidates = scope_by_number.get(_normalize_number(occurrence.section_hint), [])
            candidates = [
                scope
                for scope in candidates
                if not occurrence.chapter_hint
                or not node_by_id[scope.scope_id].chapter
                or _normalize_number(node_by_id[scope.scope_id].chapter)
                == _normalize_number(occurrence.chapter_hint)
            ]
            if len(candidates) != 1:
                warnings.append(
                    f"unresolved_membership:{occurrence.node_id}:scope_candidates={len(candidates)}"
                )
                continue
            scope = candidates[0]
            key = (scope.scope_id, occurrence.node_id)
            if key in existing:
                relation = next(
                    relation
                    for relation in relations
                    if relation.relation_family == RELATION_FAMILY_MEMBERSHIP
                    and (relation.source_node_id, relation.target_node_id) == key
                )
                if occurrence.occurrence_id not in relation.occurrence_ids:
                    relation.occurrence_ids.append(occurrence.occurrence_id)
                relation.evidence_anchor_ids = list(
                    dict.fromkeys(relation.evidence_anchor_ids + occurrence.evidence_anchor_ids)
                )
                continue
            relations.append(
                ReviewRelationDTO(
                    relation_id=_stable_id("membership", scope.scope_id, occurrence.node_id),
                    relation_family=RELATION_FAMILY_MEMBERSHIP,
                    source_node_id=scope.scope_id,
                    target_node_id=occurrence.node_id,
                    relation_type="contains",
                    evidence_anchor_ids=list(occurrence.evidence_anchor_ids),
                    occurrence_ids=[occurrence.occurrence_id],
                    confidence=0.8,
                    reasoning_summary="由旧规范记录中的章节位置恢复，需教师确认。",
                    relation_source="migration_occurrence_hint",
                    review_status=REVIEW_STATUS_PENDING,
                    migration_version=self.MIGRATION_VERSION,
                )
            )
            existing.add(key)


def document_to_legacy_drafts(
    document: P2ReviewDocumentDTO,
    *,
    profile_id: str = "",
    include_archived: bool = False,
) -> list[DraftKnowledgeItemDTO]:
    """Builds detached v1-compatible rows; callers must never mutate them as authority."""
    evidence_by_id = {anchor.anchor_id: anchor for anchor in document.evidence}
    scopes = {scope.scope_id: scope for scope in document.scopes}
    active_relations = document.active_relations()
    eligible_relations = [
        relation
        for relation in active_relations
        if normalize_review_status(relation.review_status) != "rejected"
    ]
    parent_by_child: dict[str, str] = {
        relation.target_node_id: relation.source_node_id
        for relation in eligible_relations
        if relation.relation_family == RELATION_FAMILY_STRUCTURE
    }
    memberships_by_node: dict[str, list[ReviewRelationDTO]] = {}
    for relation in eligible_relations:
        if relation.relation_family == RELATION_FAMILY_MEMBERSHIP:
            memberships_by_node.setdefault(relation.target_node_id, []).append(relation)
    profile = document.profile_by_id(profile_id) if profile_id else None
    for node_id, memberships in memberships_by_node.items():
        primary_id = profile.primary_membership_by_node.get(node_id, "") if profile else ""
        selected = next((item for item in memberships if item.relation_id == primary_id), None)
        if selected is None and len(memberships) == 1:
            selected = memberships[0]
        if selected is not None:
            parent_by_child[node_id] = selected.source_node_id
    relation_rows_by_source: dict[str, list[dict[str, Any]]] = {}
    prerequisites_by_target: dict[str, list[str]] = {}
    for relation in active_relations:
        payload = {
            "relation_id": relation.relation_id,
            "relation_family": relation.relation_family,
            "source_node_id": relation.source_node_id,
            "target_node_id": relation.target_node_id,
            "relation_type": relation.relation_type,
            "confidence": relation.confidence,
            "reasoning_summary": relation.reasoning_summary,
            "relation_source": relation.relation_source,
            "review_status": relation.review_status,
            "evidence_refs": list(relation.evidence_anchor_ids),
        }
        relation_rows_by_source.setdefault(relation.source_node_id, []).append(payload)
        if (
            relation.relation_family == RELATION_FAMILY_SEMANTIC
            and relation.relation_type == "prerequisite"
            and normalize_review_status(relation.review_status) == REVIEW_STATUS_ACCEPTED
        ):
            prerequisites_by_target.setdefault(relation.target_node_id, []).append(
                relation.source_node_id
            )
    occurrences_by_node: dict[str, list[KnowledgeOccurrenceDTO]] = {}
    for occurrence in document.occurrences:
        if occurrence.lifecycle_state == LIFECYCLE_ACTIVE:
            occurrences_by_node.setdefault(occurrence.node_id, []).append(occurrence)
    drafts: list[DraftKnowledgeItemDTO] = []
    node_names = {node.node_id: node.display_name for node in document.nodes}
    source_nodes = document.nodes if include_archived else document.active_nodes()
    for node in source_nodes:
        parent_id = parent_by_child.get(node.node_id, "")
        occurrences = occurrences_by_node.get(node.node_id, [])
        section = occurrences[0].section_hint if occurrences else ""
        anchors = [
            EvidenceAnchorDTO(**asdict(evidence_by_id[anchor_id]))
            for anchor_id in node.evidence_anchor_ids
            if anchor_id in evidence_by_id
        ]
        drafts.append(
            DraftKnowledgeItemDTO(
                draft_id=f"draft_{node.node_id}",
                subject=node.subject,
                grade=node.grade,
                term=node.term,
                chapter=node.chapter,
                section=section,
                candidate_display_name=node.display_name,
                candidate_node_name=node.node_name,
                candidate_node_id=node.node_id,
                candidate_parent_name=node_names.get(parent_id, ""),
                candidate_parent_node_id=parent_id,
                candidate_prerequisites=list(
                    dict.fromkeys(prerequisites_by_target.get(node.node_id, []))
                ),
                candidate_relations=list(relation_rows_by_source.get(node.node_id, [])),
                knowledge_type=node.node_type,
                cognitive_level=node.cognitive_level,
                education_stage=node.education_stage,
                grade_band=node.grade_band,
                subject_tags=list(node.subject_tags),
                source_id=node.source_id,
                source_path=node.source_path,
                source_format=node.source_format,
                source_document_type=node.source_document_type,
                source_text=node.source_text,
                source_location=node.source_location,
                evidence_anchors=anchors,
                confidence=node.confidence,
                reasoning_summary=node.reasoning_summary,
                extractor_source=node.extractor_source,
                review_status=node.review_status,
                review_notes=node.review_notes,
            )
        )
    return drafts


def _stable_id(prefix: str, *parts: object) -> str:
    text = "\x1f".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _fingerprint(value: object) -> str:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _structural_number(display_name: str, chapter: str) -> str:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){0,4})(?!\d)", display_name)
    if match:
        return match.group(1)
    chapter_match = re.search(r"(?<!\d)(\d+)(?!\d)", chapter)
    return chapter_match.group(1) if chapter_match else ""


def _normalize_number(value: object) -> str:
    return str(value or "").strip().replace(" ", "")


def _structure_cycle_errors(parent_by_child: dict[str, list[str]]) -> list[str]:
    single_parent = {
        child_id: parents[0]
        for child_id, parents in parent_by_child.items()
        if len(set(parents)) == 1
    }
    errors: list[str] = []
    for start in single_parent:
        current = start
        path: set[str] = set()
        while current in single_parent:
            if current in path:
                errors.append(f"structure_cycle:{current}")
                break
            path.add(current)
            current = single_parent[current]
    return errors


__all__ = [
    "ALLOWED_LIFECYCLE_STATES",
    "ALLOWED_RELATION_FAMILIES",
    "ExportProfileDTO",
    "KnowledgeOccurrenceDTO",
    "LIFECYCLE_ACTIVE",
    "LIFECYCLE_ARCHIVED",
    "MigrationAuditDTO",
    "P2ReviewDocumentDTO",
    "RELATION_FAMILY_MEMBERSHIP",
    "RELATION_FAMILY_SEMANTIC",
    "RELATION_FAMILY_STRUCTURE",
    "RELATION_FAMILY_UNRESOLVED",
    "REVIEW_DOCUMENT_SCHEMA_VERSION",
    "ReviewDecisionDTO",
    "ReviewDocumentMetadataDTO",
    "ReviewDocumentMigrator",
    "ReviewNodeDTO",
    "ReviewRelationDTO",
    "ReviewViewStateDTO",
    "StructuralScopeDTO",
    "document_to_legacy_drafts",
]
