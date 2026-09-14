from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field

from ..review_document import (
    LIFECYCLE_ACTIVE,
    P2ReviewDocumentDTO,
    RELATION_FAMILY_MEMBERSHIP,
    RELATION_FAMILY_STRUCTURE,
)
from ..utils.graph_semantics import normalize_relation_type
from ..utils.review_status import normalize_review_status


READING_ORDER_EXTRACTOR_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReadingSequenceItem:
    node_id: str
    ordinal: int
    original_token: str
    anchor_ids: tuple[str, ...] = ()
    source_span: tuple[int, int] | None = None
    match_method: str = ""


@dataclass(frozen=True, slots=True)
class ReadingSequence:
    sequence_id: str
    parent_node_id: str
    context_key: str
    items: tuple[ReadingSequenceItem, ...]
    verification_state: str
    presentation: str = "ordered_fanout"
    evidence_fingerprint: str = ""
    extractor_version: int = READING_ORDER_EXTRACTOR_VERSION
    routing_only_relation_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


_ARABIC = re.compile(r"(?m)(?<!\d)(\d{1,2})\s*[.\u3001。）)]\s*([^\r\n]+)")
_STEP_ARABIC = re.compile(r"(?m)(?:步骤\s*)?[\uff08(]?(\d{1,2})[\uff09)]?\s*(?:步|[:：.\u3001])\s*([^\r\n]+)")
_CHINESE_STEP = re.compile(
    r"(?m)(?:步骤\s*)?[\u3010\uff08(]?(第?[\u4e00二三四五六七八九十]{1,3})(?:步)?[\u3011\uff09)]?\s*[.\u3001：:]?\s*([^\r\n]+)"
)
_CIRCLED = {
    "①": 1,
    "②": 2,
    "③": 3,
    "④": 4,
    "⑤": 5,
    "⑥": 6,
    "⑦": 7,
    "⑧": 8,
    "⑨": 9,
    "⑩": 10,
}
_CIRCLED_PATTERN = re.compile(r"(?m)([①-⑩])\s*([^\r\n]+)")


def build_reading_sequences(document: P2ReviewDocumentDTO) -> list[ReadingSequence]:
    """Derive local, evidence-backed ordered fanouts without changing business data."""

    active_nodes = {
        item.node_id: item
        for item in document.nodes
        if item.lifecycle_state == LIFECYCLE_ACTIVE
    }
    evidence = {item.anchor_id: item for item in document.evidence if item.anchor_id}
    occurrences = {
        item.occurrence_id: item
        for item in document.occurrences
        if item.lifecycle_state == LIFECYCLE_ACTIVE
    }
    outgoing: dict[str, list[object]] = {}
    active_relations: list[object] = []
    for relation in document.relations:
        if relation.lifecycle_state != LIFECYCLE_ACTIVE:
            continue
        if normalize_review_status(relation.review_status) == "rejected":
            continue
        if relation.relation_family in {RELATION_FAMILY_STRUCTURE, RELATION_FAMILY_MEMBERSHIP}:
            continue
        active_relations.append(relation)
        if normalize_relation_type(relation.relation_type) == "contains":
            outgoing.setdefault(relation.source_node_id, []).append(relation)

    results: list[ReadingSequence] = []
    for parent_id, contains_relations in sorted(outgoing.items()):
        if parent_id not in active_nodes or len(contains_relations) < 2:
            continue
        child_ids = sorted(
            {
                relation.target_node_id
                for relation in contains_relations
                if relation.target_node_id in active_nodes
            }
        )
        if len(child_ids) < 2:
            continue
        context_key = _context_key(document, parent_id, child_ids, occurrences)
        source_anchors = _candidate_anchors(
            parent_id,
            child_ids,
            contains_relations,
            active_nodes,
            evidence,
            occurrences,
        )
        parsed_items = [
            item
            for anchor_id in sorted(source_anchors)
            for item in _parse_numbered_items(anchor_id, source_anchors[anchor_id].anchor_text)
        ]
        bound: list[ReadingSequenceItem] = []
        diagnostics: list[str] = []
        for child_id in child_ids:
            node = active_nodes[child_id]
            names = {
                _normalize_match_text(node.display_name),
                _normalize_match_text(node.node_name),
            } - {""}
            matches = [
                item
                for item in parsed_items
                if any(name in _normalize_match_text(item[3]) for name in names)
            ]
            ordinals = {item[0] for item in matches}
            if len(ordinals) != 1:
                diagnostics.append(
                    f"{child_id}:{'unavailable' if not matches else 'conflicted'}"
                )
                continue
            ordinal = next(iter(ordinals))
            same = [item for item in matches if item[0] == ordinal]
            anchors = tuple(sorted({item[1] for item in same}))
            span = min((item[2] for item in same), default=None)
            token = next(item[4] for item in same if item[0] == ordinal)
            bound.append(
                ReadingSequenceItem(
                    node_id=child_id,
                    ordinal=ordinal,
                    original_token=token,
                    anchor_ids=anchors,
                    source_span=span,
                    match_method="evidence_exact_name",
                )
            )
        duplicate_ordinals = {
            ordinal
            for ordinal in {item.ordinal for item in bound}
            if sum(item.ordinal == ordinal for item in bound) > 1
        }
        if duplicate_ordinals:
            diagnostics.append(
                "duplicate_ordinals:" + ",".join(map(str, sorted(duplicate_ordinals)))
            )
            bound = [item for item in bound if item.ordinal not in duplicate_ordinals]
        bound.sort(key=lambda item: (item.ordinal, item.node_id))
        if len(bound) < 2:
            continue
        state = "evidence_backed" if len(bound) == len(child_ids) and not diagnostics else "partial"
        ordered_ids = {item.node_id: item.ordinal for item in bound}
        routing_only = tuple(
            sorted(
                relation.relation_id
                for relation in active_relations
                if normalize_relation_type(relation.relation_type) == "progressive"
                and relation.source_node_id in ordered_ids
                and relation.target_node_id in ordered_ids
            )
        )
        evidence_payload = [
            {
                "anchor_id": anchor_id,
                "text": source_anchors[anchor_id].anchor_text,
                "block_id": source_anchors[anchor_id].block_id,
            }
            for anchor_id in sorted(source_anchors)
        ]
        evidence_fingerprint = _fingerprint(evidence_payload)
        sequence_id = "reading-sequence:" + _fingerprint(
            {
                "document_id": document.metadata.document_id,
                "parent": parent_id,
                "context": context_key,
                "items": [(item.node_id, item.ordinal) for item in bound],
                "version": READING_ORDER_EXTRACTOR_VERSION,
            }
        )[:20]
        results.append(
            ReadingSequence(
                sequence_id=sequence_id,
                parent_node_id=parent_id,
                context_key=context_key,
                items=tuple(bound),
                verification_state=state,
                evidence_fingerprint=evidence_fingerprint,
                routing_only_relation_ids=routing_only,
                diagnostics=tuple(diagnostics),
            )
        )
    return results


def _candidate_anchors(
    parent_id: str,
    child_ids: list[str],
    contains_relations: list[object],
    nodes: dict[str, object],
    evidence: dict[str, object],
    occurrences: dict[str, object],
) -> dict[str, object]:
    anchor_ids: set[str] = set()
    for node_id in [parent_id, *child_ids]:
        anchor_ids.update(getattr(nodes[node_id], "evidence_anchor_ids", []))
    for relation in contains_relations:
        anchor_ids.update(getattr(relation, "evidence_anchor_ids", []))
        for occurrence_id in getattr(relation, "occurrence_ids", []):
            occurrence = occurrences.get(occurrence_id)
            if occurrence is not None:
                anchor_ids.update(getattr(occurrence, "evidence_anchor_ids", []))
    for occurrence in occurrences.values():
        if getattr(occurrence, "node_id", "") in {parent_id, *child_ids}:
            anchor_ids.update(getattr(occurrence, "evidence_anchor_ids", []))
    return {
        anchor_id: evidence[anchor_id]
        for anchor_id in anchor_ids
        if anchor_id in evidence and str(getattr(evidence[anchor_id], "anchor_text", "")).strip()
    }


def _parse_numbered_items(
    anchor_id: str,
    text: str,
) -> list[tuple[int, str, tuple[int, int], str, str]]:
    found: list[tuple[int, str, tuple[int, int], str, str]] = []
    occupied: set[tuple[int, int]] = set()
    for pattern, converter in (
        (_ARABIC, lambda value: int(value)),
        (_STEP_ARABIC, lambda value: int(value)),
        (_CIRCLED_PATTERN, lambda value: _CIRCLED[value]),
        (_CHINESE_STEP, _chinese_ordinal),
    ):
        for match in pattern.finditer(text or ""):
            span = match.span()
            if span in occupied:
                continue
            try:
                ordinal = converter(match.group(1))
            except (KeyError, TypeError, ValueError):
                continue
            if not 1 <= ordinal <= 99:
                continue
            occupied.add(span)
            found.append((ordinal, anchor_id, span, match.group(2).strip(), match.group(1)))
    return sorted(found, key=lambda item: (item[2][0], item[0], item[1]))


def _chinese_ordinal(value: str) -> int:
    raw = value.removeprefix("第")
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if raw == "十":
        return 10
    if "十" in raw:
        left, right = raw.split("十", 1)
        return (digits.get(left, 1) * 10) + digits.get(right, 0)
    if raw in digits:
        return digits[raw]
    raise ValueError(value)


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _context_key(
    document: P2ReviewDocumentDTO,
    parent_id: str,
    child_ids: list[str],
    occurrences: dict[str, object],
) -> str:
    scope_hints = sorted(
        {
            (getattr(item, "chapter_hint", ""), getattr(item, "section_hint", ""))
            for item in occurrences.values()
            if getattr(item, "node_id", "") in {parent_id, *child_ids}
        }
    )
    return _fingerprint(
        {
            "document_id": document.metadata.document_id,
            "parent": parent_id,
            "scope_hints": scope_hints,
        }
    )[:16]


def _fingerprint(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "READING_ORDER_EXTRACTOR_VERSION",
    "ReadingSequence",
    "ReadingSequenceItem",
    "build_reading_sequences",
]
