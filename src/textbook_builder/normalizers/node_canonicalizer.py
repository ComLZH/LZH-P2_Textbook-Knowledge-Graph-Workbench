from __future__ import annotations

import re
from collections import defaultdict

from textbook_builder.pipeline_contracts import (
    CanonicalNodeDTO,
    LocalNodeCandidateDTO,
    NodeAliasMappingDTO,
)
from textbook_builder.utils.graph_semantics import normalize_node_type
from textbook_builder.utils.naming import build_node_id, slugify_display_name


class NodeCanonicalizer:
    """Conservatively merges exact normalized mentions and records every mapping."""

    def canonicalize(
        self,
        candidates: list[LocalNodeCandidateDTO],
        *,
        subject: str,
        grade: str,
        term: str,
    ) -> tuple[list[CanonicalNodeDTO], list[NodeAliasMappingDTO]]:
        groups: dict[tuple[str, str, str], list[LocalNodeCandidateDTO]] = defaultdict(list)
        for candidate in candidates:
            display_name = candidate.display_name.strip()
            if not display_name:
                continue
            node_type = normalize_node_type(candidate.node_type)
            key = (_normalized_mention(display_name), node_type, _normalized_mention(candidate.chapter))
            groups[key].append(candidate)

        canonical_nodes: list[CanonicalNodeDTO] = []
        mappings: list[NodeAliasMappingDTO] = []
        used_ids: set[str] = set()
        for key in sorted(groups):
            group = groups[key]
            representative = max(group, key=lambda item: (item.confidence, len(item.definition)))
            display_name = representative.display_name.strip()
            chapter = representative.chapter.strip()
            node_type = normalize_node_type(representative.node_type)
            node_name = representative.node_name.strip() or slugify_display_name(display_name)
            node_id = build_node_id(
                subject=subject,
                grade=grade,
                term=term,
                chapter=chapter,
                node_name=slugify_display_name(node_name),
            )
            if node_id in used_ids:
                node_id = f"{node_id}_{node_type}"
            suffix = 2
            base_node_id = node_id
            while node_id in used_ids:
                node_id = f"{base_node_id}_{suffix}"
                suffix += 1
            used_ids.add(node_id)

            aliases = _ordered_unique(
                alias.strip()
                for item in group
                for alias in [item.display_name, item.node_name, *item.aliases]
                if alias.strip() and _normalized_mention(alias) != _normalized_mention(display_name)
            )
            evidence_refs = _ordered_unique(
                ref.strip() for item in group for ref in item.evidence_refs if ref.strip()
            )
            temporary_ids = _ordered_unique(
                item.temporary_id.strip() for item in group if item.temporary_id.strip()
            )
            confidence = round(sum(max(0.0, min(1.0, item.confidence)) for item in group) / len(group), 3)
            canonical_nodes.append(
                CanonicalNodeDTO(
                    node_id=node_id,
                    display_name=display_name,
                    node_name=slugify_display_name(node_name),
                    node_type=node_type,
                    chapter=chapter,
                    section=representative.section.strip(),
                    definition=representative.definition.strip(),
                    aliases=aliases,
                    source_temporary_ids=temporary_ids,
                    evidence_refs=evidence_refs,
                    occurrences=_occurrences(group),
                    confidence=confidence,
                )
            )
            for item in group:
                mappings.append(
                    NodeAliasMappingDTO(
                        temporary_id=item.temporary_id,
                        source_mention=item.display_name,
                        canonical_node_id=node_id,
                        mapping_method="exact_normalized_mention",
                        confidence=1.0 if len(group) == 1 else 0.98,
                        needs_review=False,
                    )
                )
                for alias in item.aliases:
                    if alias.strip():
                        mappings.append(
                            NodeAliasMappingDTO(
                                temporary_id=item.temporary_id,
                                source_mention=alias.strip(),
                                canonical_node_id=node_id,
                                mapping_method="declared_alias",
                                confidence=max(0.0, min(1.0, item.confidence)),
                                needs_review=item.confidence < 0.7,
                            )
                        )
        return canonical_nodes, mappings


def normalized_mention(value: str) -> str:
    return _normalized_mention(value)


def _normalized_mention(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[\s_\-—–·•,，。；;：:（）()【】\[\]{}]+", "", normalized)
    return normalized


def _ordered_unique(values: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:  # type: ignore[union-attr]
        value = str(raw)
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _occurrences(group: list[LocalNodeCandidateDTO]) -> list[dict[str, object]]:
    occurrences: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in group:
        key = (item.chapter.strip(), item.section.strip(), item.temporary_id.strip())
        if key in seen:
            continue
        seen.add(key)
        occurrences.append(
            {
                "chapter": key[0],
                "section": key[1],
                "temporary_ids": [key[2]] if key[2] else [],
                "evidence_refs": list(item.evidence_refs),
            }
        )
    return occurrences
