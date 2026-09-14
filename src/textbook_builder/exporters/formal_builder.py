from __future__ import annotations

from ..contracts import (
    DraftKnowledgeItemDTO,
    FormalEdgeDTO,
    FormalGraphMetadataDTO,
    FormalGraphWorkbookDTO,
    FormalNodeDTO,
    NODE_TYPE_CONTAINER,
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_NEEDS_EXPERT_REVIEW,
    REVIEW_STATUS_NEEDS_REVISION,
)
from ..utils.graph_semantics import normalize_node_type, normalize_relation_type
from ..utils.hierarchy import project_draft_hierarchy
from ..utils.relation_endpoints import build_node_reference_index, resolve_node_reference
from ..utils.review_status import normalize_review_status


class FormalGraphWorkbookBuilder:
    def build(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        *,
        graph_id: str,
        version: str = "v1",
        textbook_version: str = "default_mvp_v1",
    ) -> FormalGraphWorkbookDTO:
        return self._build_for_review_statuses(
            drafts,
            graph_id=graph_id,
            version=version,
            textbook_version=textbook_version,
            node_statuses={REVIEW_STATUS_ACCEPTED},
            relation_statuses={REVIEW_STATUS_ACCEPTED},
            empty_error="Formal export requires at least one accepted draft item.",
        )

    def build_review_workbook(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        *,
        graph_id: str,
        version: str = "v1",
        textbook_version: str = "default_mvp_v1",
    ) -> FormalGraphWorkbookDTO:
        candidate_statuses = {
            REVIEW_STATUS_ACCEPTED,
            REVIEW_STATUS_PENDING,
            REVIEW_STATUS_NEEDS_REVISION,
            REVIEW_STATUS_NEEDS_EXPERT_REVIEW,
            REVIEW_STATUS_REJECTED,
        }
        return self._build_for_review_statuses(
            drafts,
            graph_id=graph_id,
            version=version,
            textbook_version=textbook_version,
            node_statuses=candidate_statuses,
            relation_statuses=candidate_statuses,
            empty_error="Review export requires at least one non-rejected draft item.",
        )

    def _build_for_review_statuses(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        *,
        graph_id: str,
        version: str,
        textbook_version: str,
        node_statuses: set[str],
        relation_statuses: set[str],
        empty_error: str,
    ) -> FormalGraphWorkbookDTO:
        included = [
            draft
            for draft in drafts
            if normalize_review_status(draft.review_status) in node_statuses
        ]
        if not included:
            raise ValueError(empty_error)

        subject = included[0].subject
        grade_scope = list(dict.fromkeys(draft.grade for draft in included))
        term_scope = list(dict.fromkeys(draft.term for draft in included))
        node_ids = {draft.candidate_node_id for draft in included}
        reference_index = build_node_reference_index(included)
        hierarchy = project_draft_hierarchy(
            drafts,
            relation_statuses=relation_statuses,
            included_node_ids=node_ids,
        )
        nodes = [
            FormalNodeDTO(
                node_id=draft.candidate_node_id,
                subject=draft.subject,
                grade=draft.grade,
                term=draft.term,
                chapter=draft.chapter,
                display_name=draft.candidate_display_name,
                node_name=draft.candidate_node_name,
                parent_node_id=hierarchy.parent_for(draft.candidate_node_id),
                prerequisite_nodes=[
                    resolved_node_id
                    for node_id in draft.candidate_prerequisites
                    for resolved_node_id in [
                        resolve_node_reference(
                            node_id,
                            reference_index,
                        )
                    ]
                    if resolved_node_id in node_ids
                ],
                node_type=normalize_node_type(
                    draft.knowledge_type,
                    default=NODE_TYPE_CONTAINER
                    if not draft.candidate_parent_node_id
                    else normalize_node_type("concept"),
                ),
                knowledge_type=normalize_node_type(
                    draft.knowledge_type,
                    default=NODE_TYPE_CONTAINER
                    if not draft.candidate_parent_node_id
                    else normalize_node_type("concept"),
                ),
                cognitive_level=draft.cognitive_level,
                education_stage=draft.education_stage,
                grade_band=draft.grade_band,
                subject_tags=list(draft.subject_tags),
                textbook_version=textbook_version,
                source_locations=[draft.source_location] if draft.source_location else [],
                version=version,
            )
            for draft in included
        ]
        return FormalGraphWorkbookDTO(
            metadata=FormalGraphMetadataDTO(
                graph_id=graph_id,
                subject=subject,
                grade_scope=grade_scope,
                term_scope=term_scope,
                version=version,
            ),
            nodes=nodes,
            edges=self._derive_edges(nodes, included, relation_statuses=relation_statuses),
        )

    @staticmethod
    def _derive_edges(
        nodes: list[FormalNodeDTO],
        drafts: list[DraftKnowledgeItemDTO],
        *,
        relation_statuses: set[str],
    ) -> list[FormalEdgeDTO]:
        edges: dict[tuple[str, str, str], FormalEdgeDTO] = {}
        node_ids = {node.node_id for node in nodes}
        draft_by_id = {draft.candidate_node_id: draft for draft in drafts}
        reference_index = build_node_reference_index(drafts)
        explicit_relation_keys: set[tuple[str, str, str]] = set()
        for draft in drafts:
            for relation in draft.candidate_relations:
                source_node_id = resolve_node_reference(
                    relation.get("source_node_id") or draft.candidate_node_id,
                    reference_index,
                    default=draft.candidate_node_id,
                )
                target_node_id = resolve_node_reference(
                    relation.get("target_node_id", ""),
                    reference_index,
                )
                relation_type = normalize_relation_type(relation.get("relation_type", ""))
                if source_node_id and target_node_id and relation_type:
                    explicit_relation_keys.add((source_node_id, target_node_id, relation_type))
        for node in nodes:
            if (
                node.parent_node_id
                and (node.parent_node_id, node.node_id, "contains")
                not in explicit_relation_keys
            ):
                parent_status = normalize_review_status(
                    draft_by_id[node.node_id].review_status
                    if node.node_id in draft_by_id
                    else REVIEW_STATUS_ACCEPTED
                )
                edge = FormalEdgeDTO(
                    source_node_id=node.parent_node_id,
                    target_node_id=node.node_id,
                    relation_type="contains",
                    confidence=1.0,
                    relation_source="parent_node_id",
                    review_status=parent_status,
                )
                edges[(edge.source_node_id, edge.target_node_id, edge.relation_type)] = edge
            for prerequisite in node.prerequisite_nodes:
                prerequisite = resolve_node_reference(prerequisite, reference_index)
                if (prerequisite, node.node_id, "prerequisite") in explicit_relation_keys:
                    # An explicit review relation is authoritative even when its status
                    # excludes it from this export. Do not recreate an accepted edge
                    # from the legacy convenience list (F13).
                    continue
                edge = FormalEdgeDTO(
                    source_node_id=prerequisite,
                    target_node_id=node.node_id,
                    relation_type="prerequisite",
                    confidence=1.0,
                    relation_source="prerequisite_nodes",
                    review_status=normalize_review_status(
                        draft_by_id[node.node_id].review_status
                        if node.node_id in draft_by_id
                        else REVIEW_STATUS_ACCEPTED
                    ),
                )
                edges[(edge.source_node_id, edge.target_node_id, edge.relation_type)] = edge
        for draft in drafts:
            for relation in draft.candidate_relations:
                review_status = normalize_review_status(relation.get("review_status", "pending"))
                if review_status not in relation_statuses:
                    continue
                source_node_id = resolve_node_reference(
                    relation.get("source_node_id") or draft.candidate_node_id,
                    reference_index,
                    default=draft.candidate_node_id,
                )
                target_node_id = resolve_node_reference(
                    relation.get("target_node_id", ""),
                    reference_index,
                )
                relation_type = normalize_relation_type(relation.get("relation_type", ""))
                if (
                    not source_node_id
                    or not target_node_id
                    or not relation_type
                    or source_node_id not in node_ids
                    or target_node_id not in node_ids
                ):
                    continue
                edge = FormalEdgeDTO(
                    source_node_id=source_node_id,
                    target_node_id=target_node_id,
                    relation_type=relation_type,
                    confidence=float(relation.get("confidence", 0.8)),
                    relation_evidence=str(relation.get("relation_evidence", "")),
                    relation_source=str(relation.get("relation_source", "candidate_relations")),
                    review_status=review_status,
                )
                edges[(edge.source_node_id, edge.target_node_id, edge.relation_type)] = edge
        return list(edges.values())
