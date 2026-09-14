from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import networkx as nx

from ..review_document import (
    LIFECYCLE_ACTIVE,
    RELATION_FAMILY_MEMBERSHIP,
    RELATION_FAMILY_STRUCTURE,
)
from ..utils.review_status import normalize_review_status
from .reading_order import ReadingSequence


RELATION_GROUPING_VERSION = 1


@dataclass(frozen=True, slots=True)
class VisualRelationGroup:
    group_id: str
    kind: str
    member_view_ids: tuple[str, ...]
    member_node_ids: tuple[str, ...]
    edge_relation_ids: tuple[str, ...]
    title: str
    grouping_basis: str


def build_relation_groups(
    *,
    document_id: str,
    node_instances: list[object],
    relations: list[object],
    reading_sequences: list[ReadingSequence],
    include_rejected: bool,
) -> list[VisualRelationGroup]:
    """Build stable base-topology groups before temporary visibility filtering."""

    view_by_node = {item.node_id: item.view_id for item in node_instances}
    label_by_node = {item.node_id: item.label for item in node_instances}
    graph = nx.Graph()
    graph.add_nodes_from(sorted(view_by_node))
    relation_by_pair: dict[frozenset[str], list[str]] = {}
    for relation in relations:
        if getattr(relation, "lifecycle_state", "") != LIFECYCLE_ACTIVE:
            continue
        if (
            not include_rejected
            and normalize_review_status(getattr(relation, "review_status", "")) == "rejected"
        ):
            continue
        if getattr(relation, "relation_family", "") in {
            RELATION_FAMILY_STRUCTURE,
            RELATION_FAMILY_MEMBERSHIP,
        }:
            continue
        source_id = getattr(relation, "source_node_id", "")
        target_id = getattr(relation, "target_node_id", "")
        if source_id not in view_by_node or target_id not in view_by_node:
            continue
        if source_id != target_id:
            graph.add_edge(source_id, target_id)
        relation_by_pair.setdefault(frozenset({source_id, target_id}), []).append(
            getattr(relation, "relation_id", "")
        )

    preferred_parents = [
        item.parent_node_id for item in reading_sequences if len(item.items) >= 2
    ]
    groups: list[VisualRelationGroup] = []
    isolated: list[str] = []
    components = [sorted(component) for component in nx.connected_components(graph)]
    components.sort(key=lambda members: (-len(members), members))
    for members in components:
        if len(members) == 1 and graph.degree[members[0]] == 0:
            isolated.extend(members)
            continue
        member_set = set(members)
        relation_ids = sorted(
            {
                relation_id
                for pair, pair_relation_ids in relation_by_pair.items()
                if pair <= member_set
                for relation_id in pair_relation_ids
                if relation_id
            }
        )
        representative = next(
            (node_id for node_id in preferred_parents if node_id in member_set),
            max(members, key=lambda node_id: (graph.degree[node_id], node_id)),
        )
        groups.append(
            _make_group(
                document_id=document_id,
                kind="relation_component",
                member_node_ids=members,
                member_view_ids=[view_by_node[node_id] for node_id in members],
                relation_ids=relation_ids,
                title=f"{label_by_node.get(representative, representative)}相关",
                basis="active_semantic_connected_component_v1",
            )
        )
    if isolated:
        groups.append(
            _make_group(
                document_id=document_id,
                kind="unconnected_region",
                member_node_ids=sorted(isolated),
                member_view_ids=[view_by_node[node_id] for node_id in sorted(isolated)],
                relation_ids=[],
                title="当前未连接知识点",
                basis="active_semantic_isolated_region_v1",
            )
        )
    return sorted(groups, key=lambda item: (item.kind == "unconnected_region", item.group_id))


def _make_group(
    *,
    document_id: str,
    kind: str,
    member_node_ids: list[str],
    member_view_ids: list[str],
    relation_ids: list[str],
    title: str,
    basis: str,
) -> VisualRelationGroup:
    raw = json.dumps(
        {
            "document_id": document_id,
            "kind": kind,
            "members": sorted(member_node_ids),
            "basis": basis,
            "version": RELATION_GROUPING_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    group_id = "visual-group:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return VisualRelationGroup(
        group_id=group_id,
        kind=kind,
        member_view_ids=tuple(sorted(member_view_ids)),
        member_node_ids=tuple(sorted(member_node_ids)),
        edge_relation_ids=tuple(sorted(relation_ids)),
        title=title,
        grouping_basis=basis,
    )


__all__ = [
    "RELATION_GROUPING_VERSION",
    "VisualRelationGroup",
    "build_relation_groups",
]
