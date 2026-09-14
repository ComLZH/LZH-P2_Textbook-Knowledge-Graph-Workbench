from __future__ import annotations

from dataclasses import dataclass, replace

from textbook_builder.contracts import REVIEW_STATUS_NEEDS_EXPERT_REVIEW
from textbook_builder.normalizers.node_canonicalizer import normalized_mention
from textbook_builder.pipeline_contracts import (
    ALLOWED_EVIDENCE_TYPES,
    ALLOWED_INFERENCE_SCOPES,
    EVIDENCE_TYPE_MODEL_INFERRED,
    CanonicalNodeDTO,
    LocalRelationClaimDTO,
    NodeAliasMappingDTO,
    RelationCandidateDTO,
)
from textbook_builder.utils.graph_semantics import normalize_relation_type


@dataclass(slots=True)
class RelationConsolidationResult:
    relations: list[RelationCandidateDTO]
    unresolved_claims: list[LocalRelationClaimDTO]
    warnings: list[str]


class RelationClaimConsolidator:
    """Resolves local mentions to canonical endpoints and rejects unsafe edges."""

    def consolidate(
        self,
        claims: list[LocalRelationClaimDTO],
        *,
        canonical_nodes: list[CanonicalNodeDTO],
        alias_mappings: list[NodeAliasMappingDTO],
    ) -> RelationConsolidationResult:
        node_ids = {node.node_id for node in canonical_nodes}
        temporary_index = {
            mapping.temporary_id: mapping.canonical_node_id
            for mapping in alias_mappings
            if mapping.temporary_id and mapping.canonical_node_id in node_ids
        }
        mention_index: dict[str, set[str]] = {}
        for node in canonical_nodes:
            for mention in [node.display_name, node.node_name, *node.aliases]:
                normalized = normalized_mention(mention)
                if normalized:
                    mention_index.setdefault(normalized, set()).add(node.node_id)
        for mapping in alias_mappings:
            normalized = normalized_mention(mapping.source_mention)
            if normalized and mapping.canonical_node_id in node_ids:
                mention_index.setdefault(normalized, set()).add(mapping.canonical_node_id)

        unresolved: list[LocalRelationClaimDTO] = []
        warnings: list[str] = []
        deduplicated: dict[tuple[str, str, str], RelationCandidateDTO] = {}
        for claim in claims:
            source_id = self._resolve_endpoint(
                temporary_id=claim.source_temporary_id,
                mention=claim.source_mention,
                temporary_index=temporary_index,
                mention_index=mention_index,
            )
            target_id = self._resolve_endpoint(
                temporary_id=claim.target_temporary_id,
                mention=claim.target_mention,
                temporary_index=temporary_index,
                mention_index=mention_index,
            )
            relation_type = normalize_relation_type(claim.relation_type_candidate)
            if not source_id or not target_id or not relation_type:
                unresolved.append(claim)
                warnings.append(f"关系线索 {claim.claim_id or '<无ID>'} 的端点或类型无法唯一解析。")
                continue
            if source_id == target_id:
                unresolved.append(claim)
                warnings.append(f"关系线索 {claim.claim_id or '<无ID>'} 形成自环，已阻止进入候选关系。")
                continue

            evidence_type = (
                claim.evidence_type if claim.evidence_type in ALLOWED_EVIDENCE_TYPES else EVIDENCE_TYPE_MODEL_INFERRED
            )
            inference_scope = (
                claim.inference_scope if claim.inference_scope in ALLOWED_INFERENCE_SCOPES else "same_section"
            )
            review_status = claim.review_status
            if evidence_type == EVIDENCE_TYPE_MODEL_INFERRED and not claim.evidence_refs:
                review_status = REVIEW_STATUS_NEEDS_EXPERT_REVIEW
            relation = RelationCandidateDTO(
                source_node_id=source_id,
                target_node_id=target_id,
                relation_type=relation_type,
                evidence_type=evidence_type,
                inference_scope=inference_scope,
                evidence_refs=_ordered_unique(claim.evidence_refs),
                evidence_text=claim.evidence_text.strip(),
                confidence=max(0.0, min(1.0, claim.confidence)),
                reasoning_summary=claim.reasoning_summary.strip(),
                relation_source="first_pass_local_claim",
                review_status=review_status,
            )
            key = (source_id, relation_type, target_id)
            existing = deduplicated.get(key)
            if existing is None:
                deduplicated[key] = relation
            else:
                deduplicated[key] = replace(
                    existing,
                    evidence_refs=_ordered_unique([*existing.evidence_refs, *relation.evidence_refs]),
                    evidence_text=_merge_text(existing.evidence_text, relation.evidence_text),
                    confidence=max(existing.confidence, relation.confidence),
                    reasoning_summary=_merge_text(existing.reasoning_summary, relation.reasoning_summary),
                    review_status=(
                        REVIEW_STATUS_NEEDS_EXPERT_REVIEW
                        if REVIEW_STATUS_NEEDS_EXPERT_REVIEW
                        in {existing.review_status, relation.review_status}
                        else existing.review_status
                    ),
                )
        return RelationConsolidationResult(
            relations=sorted(
                deduplicated.values(),
                key=lambda item: (item.source_node_id, item.relation_type, item.target_node_id),
            ),
            unresolved_claims=unresolved,
            warnings=warnings,
        )

    @staticmethod
    def _resolve_endpoint(
        *,
        temporary_id: str,
        mention: str,
        temporary_index: dict[str, str],
        mention_index: dict[str, set[str]],
    ) -> str:
        if temporary_id and temporary_id in temporary_index:
            return temporary_index[temporary_id]
        matches = mention_index.get(normalized_mention(mention), set())
        return next(iter(matches)) if len(matches) == 1 else ""


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _merge_text(left: str, right: str) -> str:
    values = _ordered_unique([left, right])
    return "；".join(values)
