from __future__ import annotations

from dataclasses import replace

from ..contracts import DraftKnowledgeItemDTO


class DraftKnowledgeNormalizer:
    def normalize(self, drafts: list[DraftKnowledgeItemDTO]) -> list[DraftKnowledgeItemDTO]:
        deduplicated: dict[str, DraftKnowledgeItemDTO] = {}
        for draft in drafts:
            if draft.candidate_node_id not in deduplicated:
                deduplicated[draft.candidate_node_id] = draft
                continue
            existing = deduplicated[draft.candidate_node_id]
            merged_prerequisites = list(
                dict.fromkeys(existing.candidate_prerequisites + draft.candidate_prerequisites)
            )
            merged_relations = existing.candidate_relations + [
                relation
                for relation in draft.candidate_relations
                if relation not in existing.candidate_relations
            ]
            merged_anchors = existing.evidence_anchors + [
                anchor
                for anchor in draft.evidence_anchors
                if anchor not in existing.evidence_anchors
            ]
            merged_tags = list(dict.fromkeys(existing.subject_tags + draft.subject_tags))
            deduplicated[draft.candidate_node_id] = replace(
                existing,
                candidate_prerequisites=merged_prerequisites,
                candidate_relations=merged_relations,
                evidence_anchors=merged_anchors,
                subject_tags=merged_tags,
                confidence=max(existing.confidence, draft.confidence),
                reasoning_summary=existing.reasoning_summary or draft.reasoning_summary,
            )
        return sorted(deduplicated.values(), key=lambda item: item.candidate_node_id)
