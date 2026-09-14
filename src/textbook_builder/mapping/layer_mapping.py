from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..contracts import FormalEdgeDTO, FormalGraphWorkbookDTO, FormalNodeDTO
from ..utils.graph_semantics import is_container_node_type, normalize_relation_type
from ..utils.hierarchy import HierarchyProjection, project_formal_hierarchy


@dataclass(slots=True)
class LayerMappingNode:
    node_id: str
    display_name: str
    layer: str
    depth: int
    path_node_ids: list[str]
    parent_node_id: str = ""
    node_type: str = ""
    knowledge_type: str = ""
    chapter: str = ""
    subject: str = ""
    grade: str = ""
    term: str = ""
    order_index: int = 0
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LayerMappingEdge:
    source_node_id: str
    target_node_id: str
    relation_type: str
    scope: str
    confidence: float = 1.0
    relation_evidence: str = ""
    relation_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LayerMappingResult:
    graph_id: str
    version: str
    nodes: list[LayerMappingNode]
    edges: list[LayerMappingEdge]
    warnings: list[str] = field(default_factory=list)

    @property
    def chapter_nodes(self) -> list[LayerMappingNode]:
        return [node for node in self.nodes if node.layer == "chapter"]

    @property
    def knowledge_nodes(self) -> list[LayerMappingNode]:
        return [node for node in self.nodes if node.layer == "knowledge_point"]

    @property
    def hierarchy_edges(self) -> list[LayerMappingEdge]:
        return [edge for edge in self.edges if edge.scope == "hierarchy"]

    @property
    def learning_path_edges(self) -> list[LayerMappingEdge]:
        return [edge for edge in self.edges if edge.scope == "learning_path"]

    @property
    def semantic_edges(self) -> list[LayerMappingEdge]:
        return [edge for edge in self.edges if edge.scope == "semantic"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "version": self.version,
            "summary": {
                "chapter_node_count": len(self.chapter_nodes),
                "knowledge_node_count": len(self.knowledge_nodes),
                "hierarchy_edge_count": len(self.hierarchy_edges),
                "learning_path_edge_count": len(self.learning_path_edges),
                "semantic_edge_count": len(self.semantic_edges),
                "warning_count": len(self.warnings),
            },
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "warnings": list(self.warnings),
        }


class LayerMappingBuilder:
    LEARNING_PATH_RELATIONS = {"prerequisite", "progressive", "derives_to"}

    def build(self, workbook: FormalGraphWorkbookDTO) -> LayerMappingResult:
        node_by_id = {node.node_id: node for node in workbook.nodes}
        warnings: list[str] = []
        hierarchy = project_formal_hierarchy(workbook.nodes, workbook.edges)
        warnings.extend(issue.message for issue in hierarchy.issues)
        mapped_nodes = [
            self._map_node(
                node,
                node_by_id=node_by_id,
                order_index=index,
                warnings=warnings,
                hierarchy=hierarchy,
            )
            for index, node in enumerate(workbook.nodes)
        ]
        mapped_edges = [
            self._map_edge(edge)
            for edge in workbook.edges
            if edge.source_node_id in node_by_id and edge.target_node_id in node_by_id
        ]
        missing_edge_count = len(workbook.edges) - len(mapped_edges)
        if missing_edge_count:
            warnings.append(f"有 {missing_edge_count} 条关系因端点不存在未进入分层映射结果。")
        return LayerMappingResult(
            graph_id=workbook.metadata.graph_id,
            version=workbook.metadata.version,
            nodes=mapped_nodes,
            edges=mapped_edges,
            warnings=warnings,
        )

    def _map_node(
        self,
        node: FormalNodeDTO,
        *,
        node_by_id: dict[str, FormalNodeDTO],
        order_index: int,
        warnings: list[str],
        hierarchy: HierarchyProjection,
    ) -> LayerMappingNode:
        path_node_ids = self._path_for_node(
            node,
            node_by_id=node_by_id,
            parent_by_child=hierarchy.parent_by_child,
            warnings=warnings,
        )
        structural_role = hierarchy.role_by_node.get(node.node_id, "knowledge")
        layer = "chapter" if structural_role == "chapter" else "knowledge_point"
        return LayerMappingNode(
            node_id=node.node_id,
            display_name=node.display_name,
            layer=layer,
            depth=max(0, len(path_node_ids) - 1),
            path_node_ids=path_node_ids,
            parent_node_id=hierarchy.parent_for(node.node_id),
            node_type=node.node_type,
            knowledge_type=node.knowledge_type,
            chapter=node.chapter,
            subject=node.subject,
            grade=node.grade,
            term=node.term,
            order_index=order_index,
            attributes={
                "cognitive_level": node.cognitive_level,
                "education_stage": node.education_stage,
                "grade_band": node.grade_band,
                "subject_tags": list(node.subject_tags),
                "source_locations": list(node.source_locations),
                "structural_role": structural_role,
            },
        )

    def _path_for_node(
        self,
        node: FormalNodeDTO,
        *,
        node_by_id: dict[str, FormalNodeDTO],
        parent_by_child: dict[str, str],
        warnings: list[str],
    ) -> list[str]:
        reversed_path = [node.node_id]
        seen = {node.node_id}
        parent_id = parent_by_child.get(node.node_id, "")
        while parent_id:
            if parent_id in seen:
                warnings.append(f"节点 {node.node_id} 的父子层级存在环：{parent_id}。")
                break
            parent = node_by_id.get(parent_id)
            if parent is None:
                warnings.append(f"节点 {node.node_id} 的父节点 {parent_id} 不存在。")
                break
            reversed_path.append(parent_id)
            seen.add(parent_id)
            parent_id = parent_by_child.get(parent.node_id, "")
        return list(reversed(reversed_path))

    def _map_edge(self, edge: FormalEdgeDTO) -> LayerMappingEdge:
        relation_type = normalize_relation_type(edge.relation_type)
        return LayerMappingEdge(
            source_node_id=edge.source_node_id,
            target_node_id=edge.target_node_id,
            relation_type=relation_type,
            scope=self._edge_scope(relation_type),
            confidence=edge.confidence,
            relation_evidence=edge.relation_evidence,
            relation_source=edge.relation_source,
        )

    def _edge_scope(self, relation_type: str) -> str:
        if relation_type == "contains":
            return "hierarchy"
        if relation_type in self.LEARNING_PATH_RELATIONS:
            return "learning_path"
        return "semantic"
