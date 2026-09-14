from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from ..contracts import DraftKnowledgeItemDTO, FormalEdgeDTO, FormalGraphWorkbookDTO, FormalNodeDTO
from .projection import ReviewViewProjection
from .semantic_style import relation_display_spec, relation_style_registry_payload

if TYPE_CHECKING:
    from ..geometry.recursive_layout import SceneGeometry
    from ..review_document import P2ReviewDocumentDTO
from ..utils.chapter_layout import (
    ChapterLayoutPlan,
    build_chapter_layout_plans,
    build_child_map,
    structural_order_key,
)
from ..utils.graph_semantics import is_container_node_type, normalize_node_type, normalize_relation_type
from ..utils.hierarchy import HierarchyProjection, project_formal_hierarchy
from ..utils.relation_layout import (
    RELATION_LAYOUT_REVISION,
    ChapterRelationLayout,
    build_relation_layouts,
    relation_directionality,
    relation_edge_id,
)
from ..utils.review_status import normalize_review_status


class GraphReviewPayloadBuilder:
    def build_from_projection(
        self,
        *,
        projection: ReviewViewProjection,
        geometry: SceneGeometry,
        view_config: dict[str, Any] | None = None,
        document: P2ReviewDocumentDTO | None = None,
    ) -> dict[str, Any]:
        """Adapt shared projection/geometry to G6 without inferring business facts."""

        if projection.structure_fingerprint != geometry.structure_fingerprint:
            raise ValueError("投影与几何指纹不一致，拒绝混用过期布局。")
        view_config = dict(view_config or {})
        sequence_item_by_node = {
            item.node_id: (sequence, item)
            for sequence in projection.reading_sequences
            for item in sequence.items
        }
        routing_only_relation_ids = {
            relation_id
            for sequence in projection.reading_sequences
            for relation_id in sequence.routing_only_relation_ids
        }
        scope_by_id = {scope.scope_id: scope for scope in projection.scopes}
        combo_view_id = {scope.scope_id: scope.view_id for scope in projection.scopes}
        anchor_points_by_view: dict[str, list[tuple[float, float]]] = {}
        for route in geometry.edge_routes.values():
            for view_id, point in (
                (route.source_view_id, route.points[0] if route.points else None),
                (route.target_view_id, route.points[-1] if route.points else None),
            ):
                rect = geometry.node_rects.get(view_id)
                if rect is None or point is None:
                    continue
                relative = (
                    round((point[0] - rect.x) / rect.width, 8),
                    round((point[1] - rect.y) / rect.height, 8),
                )
                if relative not in anchor_points_by_view.setdefault(view_id, []):
                    anchor_points_by_view[view_id].append(relative)
        for values in anchor_points_by_view.values():
            values.sort(key=lambda point: (point[1], point[0]))
        nodes: list[dict[str, Any]] = []
        for instance in projection.node_instances:
            rect = geometry.node_rects.get(instance.view_id)
            if rect is None:
                raise ValueError(f"视图实例缺少几何坐标：{instance.view_id}")
            center_x, center_y = rect.center
            sequence_pair = sequence_item_by_node.get(instance.node_id)
            node_payload = {
                    "id": instance.view_id,
                    "businessNodeId": instance.node_id,
                    "membershipId": instance.membership_id,
                    "label": instance.label,
                    "knowledgeType": instance.node_type,
                    "roleLabel": "引用" if instance.is_reference else "知识",
                    "role": "reference" if instance.is_reference else "knowledge",
                    "review_status": instance.review_status,
                    "membership_status": instance.membership_status,
                    "membershipIds": list(instance.membership_ids),
                    "contextScopeIds": list(instance.context_scope_ids),
                    "membershipCount": len(instance.membership_ids),
                    "evidenceAnchorIds": list(instance.evidence_anchor_ids),
                    "reasoningSummary": instance.reasoning_summary,
                    "sourceLocation": instance.source_location,
                    "comboId": combo_view_id.get(instance.scope_id, ""),
                    "x": center_x,
                    "y": center_y,
                    "size": [rect.width, rect.height],
                    "anchorPoints": [list(point) for point in anchor_points_by_view.get(instance.view_id, [(0.0, 0.5), (1.0, 0.5), (0.5, 0.0), (0.5, 1.0)])],
                }
            if sequence_pair is not None:
                sequence, sequence_item = sequence_pair
                node_payload.update(
                    {
                        "readingOrdinal": sequence_item.ordinal,
                        "readingOrdinalToken": sequence_item.original_token,
                        "readingSequenceId": sequence.sequence_id,
                        "readingVerificationState": sequence.verification_state,
                    }
                )
            nodes.append(node_payload)
        combos: list[dict[str, Any]] = []
        for scope in projection.scopes:
            rect = geometry.scope_rects.get(scope.view_id)
            if rect is None:
                raise ValueError(f"教材框缺少几何坐标：{scope.view_id}")
            center_x, center_y = rect.center
            combo = {
                "id": scope.view_id,
                "businessScopeId": scope.scope_id,
                "label": scope.label,
                "comboType": scope.structural_role,
                "structuralRole": scope.structural_role,
                "review_status": scope.review_status,
                "isVirtual": scope.is_virtual,
                "x": center_x,
                "y": center_y,
                "size": [rect.width, rect.height],
                "padding": [54, 28, 24, 28],
            }
            if scope.parent_scope_id in combo_view_id:
                combo["parentId"] = combo_view_id[scope.parent_scope_id]
            combos.append(combo)
        node_centers = {
            view_id: rect.center for view_id, rect in geometry.node_rects.items()
        }
        edges: list[dict[str, Any]] = []
        for edge in geometry.edge_instances:
            display_spec = relation_display_spec(edge.relation_family, edge.relation_type)
            route = geometry.edge_routes.get(edge.view_id)
            label = geometry.edge_labels.get(edge.view_id)
            payload = {
                "id": edge.view_id,
                "relationId": edge.relation_id,
                "source": edge.source_view_id,
                "target": edge.target_view_id,
                "relationType": normalize_relation_type(edge.relation_type) if edge.relation_type else "",
                "rawRelationType": edge.relation_type,
                "review_status": normalize_review_status(edge.review_status),
                "relationFamily": edge.relation_family,
                "displayClass": edge.display_class,
                "layoutScope": edge.layout_scope,
                "constraintStatus": edge.constraint_status,
                "directionality": relation_directionality(edge.relation_type),
                "isCycleEdge": edge.constraint_status == "semantic_cycle",
                "is_layout_edge": False,
                "semanticStyleKey": display_spec.key,
                "displayLabel": display_spec.label,
                "semanticsPending": display_spec.semantics_pending,
                "routeStatus": route.status if route else "automatic",
                "routePoints": [list(point) for point in route.points] if route else [],
                "sourceArrowPoints": [list(point) for point in route.source_arrow] if route else [],
                "targetArrowPoints": [list(point) for point in route.target_arrow] if route else [],
                "labelVisible": bool(label and label.visible),
                "labelText": label.text if label else display_spec.label,
                "labelRect": asdict(label.rect) if label and label.rect is not None else None,
                "labelSuppressionReason": label.suppression_reason if label else "",
                "layoutEdgeRole": (
                    "routing_only" if edge.relation_id in routing_only_relation_ids else "rank_or_neutral"
                ),
                "evidenceAnchorIds": list(edge.evidence_anchor_ids),
                "occurrenceIds": list(edge.occurrence_ids),
                "reasoningSummary": edge.reasoning_summary,
                "confidence": edge.confidence,
            }
            if route and route.points:
                source_anchor = self._route_anchor_index(
                    route.source_view_id,
                    route.points[0],
                    geometry.node_rects,
                    anchor_points_by_view,
                )
                target_anchor = self._route_anchor_index(
                    route.target_view_id,
                    route.points[-1],
                    geometry.node_rects,
                    anchor_points_by_view,
                )
                payload.update(
                    {
                        "routing": "shared_route",
                        "sourceAnchor": source_anchor,
                        "targetAnchor": target_anchor,
                        "controlPoints": [
                            {"x": point[0], "y": point[1]}
                            for point in route.points[1:-1]
                        ],
                        "layoutDirection": "shared_scene",
                    }
                )
            else:
                payload.update(
                    self._edge_routing_payload(
                        source_id=edge.source_view_id,
                        target_id=edge.target_view_id,
                        directionality=payload["directionality"],
                        is_cycle_edge=payload["isCycleEdge"],
                        positions=node_centers,
                    )
                )
            edges.append(payload)
        return {
            "graph_id": projection.document_id,
            "metadata": {
                "document_id": projection.document_id,
                "document_revision": projection.document_revision,
                "projection_schema_version": projection.projection_schema_version,
                "structure_fingerprint": projection.structure_fingerprint,
                "geometry_specification_version": geometry.specification_version,
                "layout_version": geometry.layout_version,
                "layout_engine": geometry.engine,
                "view_mode": projection.view_mode,
            },
            "nodes": nodes,
            "combos": combos,
            "visual_groups": [
                {
                    "id": group.group_id,
                    "kind": group.kind,
                    "title": group.title,
                    "memberViewIds": list(group.member_view_ids),
                    "groupingBasis": group.grouping_basis,
                    "x": group.frame_rect.center[0],
                    "y": group.frame_rect.center[1],
                    "size": [group.frame_rect.width, group.frame_rect.height],
                    "frameRect": asdict(group.frame_rect),
                    "contentRect": asdict(group.content_rect),
                }
                for group in geometry.visual_groups.values()
            ],
            "edges": edges,
            "layout_edges": [],
            "layout_plan": {},
            "relation_layout": {
                "edge_constraints": {
                    edge.relation_id: {
                        "layout_scope": edge.layout_scope,
                        "constraint_status": edge.constraint_status,
                    }
                    for edge in geometry.edge_instances
                },
                "reading_sequences": [asdict(item) for item in projection.reading_sequences],
            },
            "hierarchy": {
                "parent_by_child": {
                    scope.scope_id: scope.parent_scope_id
                    for scope in projection.scopes
                    if scope.parent_scope_id
                },
                "role_by_node": {
                    scope.scope_id: scope.structural_role for scope in projection.scopes
                },
                "root_node_ids": [
                    scope.scope_id for scope in projection.scopes if not scope.parent_scope_id
                ],
                "issues": [asdict(issue) for issue in projection.issues],
            },
            "representation_ledger": [asdict(item) for item in projection.representation_ledger],
            "geometry_issues": [asdict(item) for item in geometry.issues],
            "readability_report": asdict(geometry.readability),
            "relation_style_registry": relation_style_registry_payload(),
            "review_state": {
                "nodes": {
                    item.node_id: {"review_status": item.review_status}
                    for item in projection.node_instances
                },
                "edges": {
                    item.relation_id: {"review_status": item.review_status}
                    for item in geometry.edge_instances
                },
            },
            "evidence_anchors": (
                [asdict(anchor) for anchor in document.evidence]
                if document is not None
                else []
            ),
            "view_config": {
                "layout_revision": geometry.layout_version,
                "layout_mode": "knowledge_relations_v6" if projection.view_mode == "relations" else "textbook_recursive_v6",
                "view_mode": projection.view_mode,
                "structure_fingerprint": projection.structure_fingerprint,
                "storage_mode": view_config.get("storage_mode", "browser_local"),
                "focused_combo_id": view_config.get("focused_combo_id", ""),
                "show_auxiliary_edges": False,
                "show_relation_groups": view_config.get("show_relation_groups", True),
                "manual_positions": view_config.get("manual_positions", {}),
                "collapsed_combos": view_config.get("collapsed_combos", []),
            },
        }


    @staticmethod
    def _route_anchor_index(
        view_id: str,
        point: tuple[float, float],
        rects: dict[str, Any],
        anchors_by_view: dict[str, list[tuple[float, float]]],
    ) -> int | None:
        rect = rects.get(view_id)
        anchors = anchors_by_view.get(view_id)
        if rect is None or not anchors:
            return None
        relative = (
            round((point[0] - rect.x) / rect.width, 8),
            round((point[1] - rect.y) / rect.height, 8),
        )
        try:
            return anchors.index(relative)
        except ValueError:
            return None

    def build(
        self,
        *,
        drafts: list[DraftKnowledgeItemDTO],
        workbook: FormalGraphWorkbookDTO,
        layout_plans: dict[str, ChapterLayoutPlan] | None = None,
        view_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        layout_plans = layout_plans or build_chapter_layout_plans(workbook.nodes, workbook.edges)
        relation_layouts = build_relation_layouts(workbook.nodes, workbook.edges)
        view_config = view_config or {}
        hierarchy = project_formal_hierarchy(workbook.nodes, workbook.edges)
        child_map = hierarchy.children_by_parent
        combo_ids = {
            node.node_id
            for node in workbook.nodes
            if child_map.get(node.node_id)
            and is_container_node_type(node.node_type or node.knowledge_type)
        }
        roles = self._node_roles(workbook.nodes, layout_plans)
        positions = self._positions(
            nodes=workbook.nodes,
            child_map=child_map,
            combo_ids=combo_ids,
            hierarchy=hierarchy,
            layout_plans=layout_plans,
            roles=roles,
            relation_layouts=relation_layouts,
            view_config=view_config,
        )
        relation_layout_by_node = {
            node_id: layout
            for layout in relation_layouts.values()
            for node_id in layout.node_ranks
        }
        draft_by_node_id = {draft.candidate_node_id: draft for draft in drafts}
        combos = [
            self._combo_payload(
                node,
                positions,
                draft_by_node_id.get(node.node_id),
                hierarchy=hierarchy,
                combo_ids=combo_ids,
            )
            for node in workbook.nodes
            if node.node_id in combo_ids
        ]
        nodes = [
            self._node_payload(
                node=node,
                draft=draft_by_node_id.get(node.node_id),
                role=roles.get(node.node_id, "fallback"),
                position=positions.get(node.node_id),
                hierarchy=hierarchy,
                combo_ids=combo_ids,
                relation_layout=relation_layout_by_node.get(node.node_id),
            )
            for node in workbook.nodes
            if node.node_id not in combo_ids
        ]
        relation_pairs = self._formal_relation_pairs(workbook.edges)
        cycle_edge_ids = {
            edge_id
            for layout in relation_layouts.values()
            for edge_id in layout.cycle_edge_ids
        }
        edges = [
            self._edge_payload(
                edge,
                positions=positions,
                cycle_edge_ids=cycle_edge_ids,
            )
            for edge in workbook.edges
            if normalize_relation_type(edge.relation_type) != "contains"
        ]
        layout_edges = [
            self._layout_edge_payload(
                source_id,
                target_id,
                relation_type,
                positions=positions,
            )
            for source_id, target_id, relation_type in self._layout_guide_edges(
                layout_plans,
                suppressed_pairs=relation_pairs,
            )
        ]
        return {
            "graph_id": workbook.metadata.graph_id,
            "metadata": asdict(workbook.metadata),
            "nodes": nodes,
            "edges": edges,
            "layout_edges": layout_edges,
            "combos": combos,
            "layout_plan": {
                container_id: plan.as_dict()
                for container_id, plan in layout_plans.items()
            },
            "relation_layout": {
                container_id: layout.as_dict()
                for container_id, layout in relation_layouts.items()
            },
            "hierarchy": {
                "parent_by_child": dict(hierarchy.parent_by_child),
                "role_by_node": dict(hierarchy.role_by_node),
                "root_node_ids": list(hierarchy.root_node_ids),
                "issues": [asdict(issue) for issue in hierarchy.issues],
            },
            "suppressed_container_relations": [],
            "review_state": self._review_state(drafts, workbook.edges),
            "evidence_anchors": self._evidence_anchors(drafts),
            "view_config": {
                "layout_revision": RELATION_LAYOUT_REVISION,
                "layout_mode": "relation_layered_chapter_review",
                "storage_mode": view_config.get("storage_mode", "browser_local"),
                "focused_combo_id": view_config.get("focused_combo_id", ""),
                "show_auxiliary_edges": view_config.get("show_auxiliary_edges", False),
                "manual_positions": self._current_manual_positions(view_config),
                "collapsed_combos": view_config.get("collapsed_combos", []),
            },
        }

    @staticmethod
    def _node_payload(
        *,
        node: FormalNodeDTO,
        draft: DraftKnowledgeItemDTO | None,
        role: str,
        position: tuple[float, float] | None,
        hierarchy: HierarchyProjection,
        combo_ids: set[str],
        relation_layout: ChapterRelationLayout | None,
    ) -> dict[str, Any]:
        parent_id = hierarchy.parent_for(node.node_id)
        combo_id = GraphReviewPayloadBuilder._nearest_combo_ancestor(
            node.node_id,
            hierarchy,
            combo_ids,
        )
        payload = {
            "id": node.node_id,
            "label": node.display_name,
            "node_name": node.node_name,
            "knowledgeType": normalize_node_type(node.node_type or node.knowledge_type),
            "roleLabel": GraphReviewPayloadBuilder._role_label(role),
            "role": role,
            "structuralRole": hierarchy.role_by_node.get(node.node_id, "knowledge"),
            "structuralParentId": parent_id,
            "chapter": node.chapter,
            "subject": node.subject,
            "grade": node.grade,
            "term": node.term,
            "source_locations": list(node.source_locations),
            "review_status": normalize_review_status(draft.review_status if draft else "accepted"),
            "confidence": draft.confidence if draft else 1.0,
            "reasoning_summary": draft.reasoning_summary if draft else "",
        }
        if combo_id:
            payload["comboId"] = combo_id
        if position is not None:
            payload["x"], payload["y"] = position
        if relation_layout is not None:
            payload.update(
                {
                    "layoutRank": relation_layout.node_ranks.get(node.node_id, 0),
                    "layoutOrder": relation_layout.node_orders.get(node.node_id, 0),
                    "layoutComponent": relation_layout.component_by_node.get(node.node_id, ""),
                    "inLayoutCycle": node.node_id in relation_layout.cycle_node_ids,
                }
            )
        return payload

    @staticmethod
    def _combo_payload(
        node: FormalNodeDTO,
        positions: dict[str, tuple[float, float]],
        draft: DraftKnowledgeItemDTO | None,
        *,
        hierarchy: HierarchyProjection,
        combo_ids: set[str],
    ) -> dict[str, Any]:
        structural_role = hierarchy.role_by_node.get(node.node_id, "chapter")
        parent_combo_id = GraphReviewPayloadBuilder._nearest_combo_ancestor(
            node.node_id,
            hierarchy,
            combo_ids,
        )
        payload = {
            "id": node.node_id,
            "label": node.display_name,
            "comboType": structural_role,
            "structuralRole": structural_role,
            "structuralParentId": hierarchy.parent_for(node.node_id),
            "knowledgeType": normalize_node_type(node.node_type or node.knowledge_type),
            "review_status": normalize_review_status(draft.review_status if draft else "accepted"),
            "chapter": node.chapter,
            "subject": node.subject,
            "grade": node.grade,
            "term": node.term,
        }
        if parent_combo_id:
            payload["parentId"] = parent_combo_id
        if node.node_id in positions:
            payload["x"], payload["y"] = positions[node.node_id]
        return payload

    @staticmethod
    def _edge_payload(
        edge: FormalEdgeDTO,
        *,
        positions: dict[str, tuple[float, float]],
        cycle_edge_ids: set[str],
    ) -> dict[str, Any]:
        relation_type = normalize_relation_type(edge.relation_type)
        edge_id = relation_edge_id(edge.source_node_id, relation_type, edge.target_node_id)
        payload = {
            "id": edge_id,
            "source": edge.source_node_id,
            "target": edge.target_node_id,
            "relationType": relation_type,
            "confidence": edge.confidence,
            "relation_evidence": edge.relation_evidence,
            "relation_source": edge.relation_source,
            "review_status": normalize_review_status(edge.review_status),
            "is_layout_edge": False,
            "directionality": relation_directionality(relation_type),
            "isCycleEdge": edge_id in cycle_edge_ids,
        }
        payload.update(
            GraphReviewPayloadBuilder._edge_routing_payload(
                source_id=edge.source_node_id,
                target_id=edge.target_node_id,
                directionality=payload["directionality"],
                is_cycle_edge=payload["isCycleEdge"],
                positions=positions,
            )
        )
        return payload

    @staticmethod
    def _layout_edge_payload(
        source_id: str,
        target_id: str,
        relation_type: str,
        *,
        positions: dict[str, tuple[float, float]],
    ) -> dict[str, Any]:
        payload = {
            "id": f"{source_id}--{relation_type}--{target_id}",
            "source": source_id,
            "target": target_id,
            "relationType": relation_type,
            "confidence": 1.0,
            "relation_evidence": "由章节布局计划生成的审查引导边，不写入正式图谱。",
            "relation_source": "layout_plan",
            "review_status": "pending",
            "is_layout_edge": True,
            "directionality": "directional",
            "isCycleEdge": False,
        }
        payload.update(
            GraphReviewPayloadBuilder._edge_routing_payload(
                source_id=source_id,
                target_id=target_id,
                directionality="directional",
                is_cycle_edge=False,
                positions=positions,
            )
        )
        return payload

    @staticmethod
    def _edge_routing_payload(
        *,
        source_id: str,
        target_id: str,
        directionality: str,
        is_cycle_edge: bool,
        positions: dict[str, tuple[float, float]],
    ) -> dict[str, Any]:
        source_position = positions.get(source_id)
        target_position = positions.get(target_id)
        if source_position is None or target_position is None:
            return {"routing": "automatic"}
        source_x, source_y = source_position
        target_x, target_y = target_position
        if is_cycle_edge:
            return {
                "routing": "cycle_back",
                "sourceAnchor": 1,
                "targetAnchor": 1,
                "layoutDirection": "cycle_exception",
            }
        if directionality == "symmetric":
            if abs(target_x - source_x) < 1.0:
                return {
                    "routing": "vertical_neutral",
                    "sourceAnchor": 3 if target_y >= source_y else 2,
                    "targetAnchor": 2 if target_y >= source_y else 3,
                    "layoutDirection": "same_rank",
                }
            return {
                "routing": "horizontal_neutral",
                "sourceAnchor": 1 if target_x >= source_x else 0,
                "targetAnchor": 0 if target_x >= source_x else 1,
                "layoutDirection": "same_rank_override",
            }
        if target_x > source_x:
            return {
                "routing": "horizontal_forward",
                "sourceAnchor": 1,
                "targetAnchor": 0,
                "layoutDirection": "left_to_right",
            }
        if target_x < source_x:
            return {
                "routing": "horizontal_reverse",
                "sourceAnchor": 0,
                "targetAnchor": 1,
                "layoutDirection": "manual_or_cross_scope_reverse",
            }
        return {
            "routing": "vertical_cross_scope",
            "sourceAnchor": 3 if target_y >= source_y else 2,
            "targetAnchor": 2 if target_y >= source_y else 3,
            "layoutDirection": "cross_scope",
        }

    @staticmethod
    def _review_state(
        drafts: list[DraftKnowledgeItemDTO],
        edges: list[FormalEdgeDTO],
    ) -> dict[str, Any]:
        return {
            "nodes": {
                draft.candidate_node_id: {
                    "review_status": normalize_review_status(draft.review_status),
                    "confidence": draft.confidence,
                    "review_notes": draft.review_notes,
                }
                for draft in drafts
            },
            "edges": {
                f"{edge.source_node_id}--{normalize_relation_type(edge.relation_type)}--{edge.target_node_id}": {
                    "review_status": normalize_review_status(edge.review_status),
                    "confidence": edge.confidence,
                }
                for edge in edges
            },
        }

    @staticmethod
    def _evidence_anchors(drafts: list[DraftKnowledgeItemDTO]) -> list[dict[str, Any]]:
        anchors: list[dict[str, Any]] = []
        for draft in drafts:
            if draft.evidence_anchors:
                for anchor in draft.evidence_anchors:
                    anchors.append(
                        {
                            "anchor_id": anchor.anchor_id,
                            "target_ids": list(anchor.target_ids or [draft.candidate_node_id]),
                            "source_location": anchor.source_location or draft.source_location,
                            "anchor_text": anchor.anchor_text or draft.source_text,
                            "review_status": normalize_review_status(anchor.review_status or draft.review_status),
                        }
                    )
            else:
                anchors.append(
                    {
                        "anchor_id": f"{draft.draft_id}:anchor",
                        "target_ids": [draft.candidate_node_id],
                        "source_location": draft.source_location,
                        "anchor_text": draft.source_text,
                        "review_status": normalize_review_status(draft.review_status),
                    }
                )
        return anchors

    @staticmethod
    def _node_roles(
        nodes: list[FormalNodeDTO],
        layout_plans: dict[str, ChapterLayoutPlan],
    ) -> dict[str, str]:
        roles: dict[str, str] = {}
        for container_id, plan in layout_plans.items():
            roles[container_id] = "container"
            for child_id in plan.structural_children:
                roles[child_id] = "structural"
            for child_id in plan.main_path_nodes:
                roles[child_id] = "main_path"
            for branch_ids in plan.branch_groups.values():
                for child_id in branch_ids:
                    roles.setdefault(child_id, "branch")
            for attachment_ids in plan.auxiliary_attachments.values():
                for child_id in attachment_ids:
                    roles.setdefault(child_id, "auxiliary")
            for child_id in plan.unassigned_nodes:
                roles.setdefault(child_id, "fallback")
        for node in nodes:
            if node.node_id in roles:
                continue
            if is_container_node_type(node.node_type or node.knowledge_type):
                roles[node.node_id] = "container"
            elif node.parent_node_id:
                roles[node.node_id] = "fallback"
            else:
                roles[node.node_id] = "orphan"
        return roles

    def _positions(
        self,
        *,
        nodes: list[FormalNodeDTO],
        child_map: dict[str, list[str]],
        combo_ids: set[str],
        hierarchy: HierarchyProjection,
        layout_plans: dict[str, ChapterLayoutPlan],
        roles: dict[str, str],
        relation_layouts: dict[str, ChapterRelationLayout],
        view_config: dict[str, Any],
    ) -> dict[str, tuple[float, float]]:
        positions: dict[str, tuple[float, float]] = {}
        manual_positions = self._current_manual_positions(view_config)
        top_containers = [
            node
            for node in nodes
            if node.node_id in combo_ids
            and not hierarchy.parent_for(node.node_id)
        ]
        top_containers.sort(key=structural_order_key)
        y_cursor = 160.0
        for container in top_containers:
            positions[container.node_id] = (520.0, y_cursor)
            plan = layout_plans.get(container.node_id)
            child_ids = child_map.get(container.node_id, [])
            if plan:
                relation_layout = relation_layouts.get(container.node_id)
                self._place_plan_nodes(
                    positions=positions,
                    plan=plan,
                    child_ids=child_ids,
                    roles=roles,
                    start_y=y_cursor,
                    relation_layout=relation_layout,
                )
                if relation_layout is not None:
                    positions[container.node_id] = (
                        160.0 + relation_layout.max_rank * 190.0,
                        y_cursor + max(0, relation_layout.max_rank_size - 1) * 65.0,
                    )
                    y_cursor += max(300.0, 190.0 + relation_layout.max_rank_size * 130.0)
                else:
                    y_cursor += 380.0
            else:
                for index, child_id in enumerate(child_ids):
                    positions[child_id] = (160.0 + index * 280.0, y_cursor)
                y_cursor += 260.0
        orphan_nodes = [
            node
            for node in nodes
            if node.node_id not in positions
            and node.node_id not in combo_ids
        ]
        for index, node in enumerate(orphan_nodes):
            positions[node.node_id] = (160.0 + index * 280.0, y_cursor)
        for node_id, raw_position in manual_positions.items():
            if not isinstance(raw_position, dict):
                continue
            try:
                positions[node_id] = (float(raw_position["x"]), float(raw_position["y"]))
            except (KeyError, TypeError, ValueError):
                continue
        return positions

    @staticmethod
    def _current_manual_positions(view_config: dict[str, Any]) -> dict[str, Any]:
        try:
            view_layout_revision = int(view_config.get("layout_revision", 0))
        except (TypeError, ValueError):
            return {}
        manual_positions = view_config.get("manual_positions", {})
        if view_layout_revision < RELATION_LAYOUT_REVISION or not isinstance(manual_positions, dict):
            return {}
        return manual_positions

    @staticmethod
    def _place_plan_nodes(
        *,
        positions: dict[str, tuple[float, float]],
        plan: ChapterLayoutPlan,
        child_ids: list[str],
        roles: dict[str, str],
        start_y: float,
        relation_layout: ChapterRelationLayout | None,
    ) -> None:
        del roles
        if relation_layout is not None:
            for node_id in child_ids:
                rank = relation_layout.node_ranks.get(node_id, 0)
                order = relation_layout.node_orders.get(node_id, 0)
                positions[node_id] = (
                    160.0 + rank * 380.0,
                    start_y + 80.0 + order * 130.0,
                )
            return
        structural_children = list(plan.structural_children)
        main_path = list(plan.main_path_nodes)
        branch_groups = dict(plan.branch_groups)
        auxiliary_attachments = dict(plan.auxiliary_attachments)
        unassigned = list(plan.unassigned_nodes)
        for index, node_id in enumerate(structural_children):
            row, column = divmod(index, 3)
            positions[node_id] = (140.0 + column * 380.0, start_y + 80.0 + row * 140.0)
        if main_path:
            total_width = (len(main_path) - 1) * 380.0
            x_start = 520.0 - total_width / 2
            for index, node_id in enumerate(main_path):
                positions[node_id] = (x_start + index * 380.0, start_y)
        for parent_id, children in branch_groups.items():
            parent_x, parent_y = positions.get(parent_id, (520.0, start_y))
            total_width = (len(children) - 1) * 250.0
            for index, child_id in enumerate(children):
                positions[child_id] = (parent_x - total_width / 2 + index * 250.0, parent_y + 155.0)
        for parent_id, children in auxiliary_attachments.items():
            parent_x, parent_y = positions.get(parent_id, (520.0, start_y))
            total_width = (len(children) - 1) * 250.0
            for index, child_id in enumerate(children):
                positions[child_id] = (parent_x - total_width / 2 + index * 250.0, parent_y + 260.0)
        for index, node_id in enumerate(unassigned):
            positions.setdefault(node_id, (180.0 + index * 230.0, start_y + 300.0))

    @staticmethod
    def _nearest_combo_ancestor(
        node_id: str,
        hierarchy: HierarchyProjection,
        combo_ids: set[str],
    ) -> str:
        current = hierarchy.parent_for(node_id)
        seen: set[str] = set()
        while current and current not in seen:
            if current in combo_ids:
                return current
            seen.add(current)
            current = hierarchy.parent_for(current)
        return ""

    @staticmethod
    def _layout_guide_edges(
        layout_plans: dict[str, ChapterLayoutPlan],
        suppressed_pairs: set[frozenset[str]] | None = None,
    ) -> list[tuple[str, str, str]]:
        suppressed_pairs = suppressed_pairs or set()
        guide_edges: list[tuple[str, str, str]] = []
        for plan in layout_plans.values():
            main_path = list(plan.main_path_nodes)
            for index in range(len(main_path) - 1):
                source_id = main_path[index]
                target_id = main_path[index + 1]
                if frozenset({source_id, target_id}) not in suppressed_pairs:
                    guide_edges.append((source_id, target_id, "layout_main_path"))
            for parent_id, child_ids in plan.branch_groups.items():
                for child_id in child_ids:
                    if frozenset({parent_id, child_id}) not in suppressed_pairs:
                        guide_edges.append((parent_id, child_id, "layout_branch"))
            for parent_id, child_ids in plan.auxiliary_attachments.items():
                for child_id in child_ids:
                    if frozenset({parent_id, child_id}) not in suppressed_pairs:
                        guide_edges.append((parent_id, child_id, "layout_auxiliary"))
        return guide_edges

    @staticmethod
    def _formal_relation_pairs(edges: list[FormalEdgeDTO]) -> set[frozenset[str]]:
        return {
            frozenset({edge.source_node_id, edge.target_node_id})
            for edge in edges
            if normalize_relation_type(edge.relation_type) != "contains"
        }

    @staticmethod
    def _role_label(role: str) -> str:
        labels = {
            "main_path": "主路径",
            "branch": "分支",
            "auxiliary": "侧挂",
            "structural": "子结构",
            "fallback": "待确认",
            "orphan": "独立",
        }
        return labels.get(role, "节点")
