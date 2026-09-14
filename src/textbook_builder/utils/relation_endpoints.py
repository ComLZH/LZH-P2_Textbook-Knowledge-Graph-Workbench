from __future__ import annotations

from ..contracts import DraftKnowledgeItemDTO


def build_node_reference_index(drafts: list[DraftKnowledgeItemDTO]) -> dict[str, str]:
    index: dict[str, str] = {}
    for draft in drafts:
        for value in [
            draft.candidate_node_id,
            draft.candidate_node_name,
            draft.candidate_display_name,
        ]:
            _add_reference(index, value, draft.candidate_node_id)
    return index


def resolve_node_reference(
    value: object,
    reference_index: dict[str, str],
    *,
    default: str = "",
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    return reference_index.get(raw) or reference_index.get(raw.lower()) or raw


def normalize_draft_relation_endpoints(drafts: list[DraftKnowledgeItemDTO]) -> None:
    reference_index = build_node_reference_index(drafts)
    for draft in drafts:
        draft.candidate_prerequisites = [
            resolved
            for prerequisite in draft.candidate_prerequisites
            if (resolved := resolve_node_reference(prerequisite, reference_index))
        ]
        for relation in draft.candidate_relations:
            relation["source_node_id"] = resolve_node_reference(
                relation.get("source_node_id") or draft.candidate_node_id,
                reference_index,
                default=draft.candidate_node_id,
            )
            relation["target_node_id"] = resolve_node_reference(
                relation.get("target_node_id"),
                reference_index,
            )


def _add_reference(index: dict[str, str], value: str, node_id: str) -> None:
    raw = str(value or "").strip()
    if not raw:
        return
    index.setdefault(raw, node_id)
    index.setdefault(raw.lower(), node_id)
