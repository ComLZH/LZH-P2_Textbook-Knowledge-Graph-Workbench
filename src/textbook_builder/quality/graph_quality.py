from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from ..contracts import DraftKnowledgeItemDTO
from ..utils.graph_semantics import is_container_node_type, normalize_relation_type
from ..utils.hierarchy import project_draft_hierarchy
from ..utils.relation_endpoints import build_node_reference_index, resolve_node_reference
from ..utils.review_status import normalize_review_status

try:  # pragma: no cover - optional local graph engine
    import networkx as nx
except Exception:  # pragma: no cover - keep the desktop tool usable without NetworkX
    nx = None


@dataclass(slots=True)
class GraphQualityIssue:
    severity: str
    code: str
    message: str
    node_id: str = ""
    edge_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def severity_label(self) -> str:
        labels = {"error": "错误", "warning": "警告", "info": "提示"}
        return labels.get(self.severity, self.severity)


@dataclass(slots=True)
class GraphQualityReport:
    issues: list[GraphQualityIssue]
    node_count: int
    edge_count: int
    accepted_node_count: int
    accepted_edge_count: int
    engine: str = "builtin"

    @property
    def error_count(self) -> int:
        return len([issue for issue in self.issues if issue.severity == "error"])

    @property
    def warning_count(self) -> int:
        return len([issue for issue in self.issues if issue.severity == "warning"])

    @property
    def info_count(self) -> int:
        return len([issue for issue in self.issues if issue.severity == "info"])

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    def summary_text(self) -> str:
        return (
            f"质量检查：{self.error_count} 个错误、{self.warning_count} 个警告、"
            f"{self.info_count} 个提示。"
        )

    def to_markdown(self) -> str:
        lines = [
            "# 图谱质量检查报告",
            "",
            "## 汇总",
            "",
            f"- 检查引擎：`{self.engine}`",
            f"- 底稿节点数：{self.node_count}",
            f"- 底稿关系数：{self.edge_count}",
            f"- 已通过节点数：{self.accepted_node_count}",
            f"- 已通过关系数：{self.accepted_edge_count}",
            f"- 错误：{self.error_count}",
            f"- 警告：{self.warning_count}",
            f"- 提示：{self.info_count}",
            "",
        ]
        if not self.issues:
            lines.extend(["## 检查结果", "", "未发现需要处理的问题。"])
            return "\n".join(lines) + "\n"
        grouped: dict[str, list[GraphQualityIssue]] = defaultdict(list)
        for issue in self.issues:
            grouped[issue.severity].append(issue)
        for severity, title in [("error", "错误"), ("warning", "警告"), ("info", "提示")]:
            if not grouped.get(severity):
                continue
            lines.extend([f"## {title}", ""])
            for index, issue in enumerate(grouped[severity], start=1):
                target = issue.node_id or issue.edge_id or "-"
                lines.append(f"{index}. **{issue.code}** `{target}`：{issue.message}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


class GraphQualityChecker:
    MAIN_PATH_RELATIONS = {"prerequisite", "progressive", "derives_to"}

    def check_drafts(self, drafts: list[DraftKnowledgeItemDTO]) -> GraphQualityReport:
        node_by_id = {draft.candidate_node_id: draft for draft in drafts}
        accepted_node_ids = {
            draft.candidate_node_id
            for draft in drafts
            if normalize_review_status(draft.review_status) == "accepted"
        }
        candidate_edges = self._candidate_edges(drafts)
        candidate_hierarchy = project_draft_hierarchy(drafts)
        accepted_edges = [
            edge
            for edge in candidate_edges
            if edge["status"] == "accepted"
            and edge["source"] in accepted_node_ids
            and edge["target"] in accepted_node_ids
        ]
        issues: list[GraphQualityIssue] = []
        issues.extend(self._check_hierarchy_projection(candidate_hierarchy.issues))
        issues.extend(self._check_review_statuses(drafts, candidate_edges))
        issues.extend(self._check_nodes(drafts, accepted_node_ids))
        issues.extend(self._check_edges(candidate_edges, accepted_node_ids, node_by_id))
        issues.extend(
            self._check_structure_dependencies(
                candidate_hierarchy.parent_by_child,
                candidate_edges,
                accepted_node_ids,
            )
        )
        issues.extend(self._check_duplicate_node_names(drafts, accepted_node_ids))
        issues.extend(self._check_isolated_nodes(drafts, accepted_node_ids, accepted_edges))
        issues.extend(self._check_chapter_main_paths(drafts, accepted_node_ids, accepted_edges))
        engine = "networkx" if nx is not None else "builtin"
        issues.extend(self._check_graph_cycles(accepted_edges, engine=engine))
        if not accepted_node_ids:
            issues.append(
                GraphQualityIssue(
                    severity="error",
                    code="no_accepted_nodes",
                    message="当前没有已通过节点，无法生成正式图谱。",
                )
            )
        issues.sort(key=lambda item: {"error": 0, "warning": 1, "info": 2}.get(item.severity, 9))
        return GraphQualityReport(
            issues=issues,
            node_count=len(drafts),
            edge_count=len(candidate_edges),
            accepted_node_count=len(accepted_node_ids),
            accepted_edge_count=len(accepted_edges),
            engine=engine,
        )

    def _candidate_edges(self, drafts: list[DraftKnowledgeItemDTO]) -> list[dict[str, str]]:
        edges: list[dict[str, str]] = []
        reference_index = build_node_reference_index(drafts)
        explicit_contains_children = {
            resolve_node_reference(relation.get("target_node_id") or "", reference_index)
            for draft in drafts
            for relation in draft.candidate_relations
            if normalize_relation_type(relation.get("relation_type")) == "contains"
        }
        for draft in drafts:
            if (
                draft.candidate_parent_node_id
                and draft.candidate_node_id not in explicit_contains_children
            ):
                edges.append(
                    self._edge(
                        source=draft.candidate_parent_node_id,
                        target=draft.candidate_node_id,
                        relation_type="contains",
                        status=draft.review_status,
                        source_kind="parent_node_id",
                    )
                )
            for prerequisite in draft.candidate_prerequisites:
                edges.append(
                    self._edge(
                        source=resolve_node_reference(prerequisite, reference_index),
                        target=draft.candidate_node_id,
                        relation_type="prerequisite",
                        status=draft.review_status,
                        source_kind="candidate_prerequisites",
                    )
                )
            for relation in draft.candidate_relations:
                edges.append(
                    self._edge(
                        source=resolve_node_reference(
                            relation.get("source_node_id") or draft.candidate_node_id,
                            reference_index,
                            default=draft.candidate_node_id,
                        ),
                        target=resolve_node_reference(
                            relation.get("target_node_id") or "",
                            reference_index,
                        ),
                        relation_type=str(relation.get("relation_type") or "").strip(),
                        status=str(relation.get("review_status") or draft.review_status),
                        evidence=str(relation.get("relation_evidence") or ""),
                        source_kind=str(relation.get("relation_source") or "candidate_relations"),
                    )
                )
        return edges

    @staticmethod
    def _check_hierarchy_projection(issues: list[object]) -> list[GraphQualityIssue]:
        return [
            GraphQualityIssue(
                severity="error",
                code=str(getattr(issue, "code", "hierarchy_invalid")),
                node_id=str(getattr(issue, "node_id", "")),
                message=str(getattr(issue, "message", "目录层级存在冲突。")),
                details={"parent_ids": list(getattr(issue, "parent_ids", []))},
            )
            for issue in issues
        ]

    @staticmethod
    def _edge(
        *,
        source: str,
        target: str,
        relation_type: str,
        status: str,
        source_kind: str,
        evidence: str = "",
    ) -> dict[str, str]:
        normalized_relation = normalize_relation_type(relation_type)
        return {
            "source": source,
            "target": target,
            "relation_type": normalized_relation,
            "status": normalize_review_status(status),
            "source_kind": source_kind,
            "evidence": evidence,
            "edge_id": f"{source}--{normalized_relation}--{target}",
        }

    def _check_review_statuses(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        edges: list[dict[str, str]],
    ) -> list[GraphQualityIssue]:
        issues: list[GraphQualityIssue] = []
        for draft in drafts:
            status = normalize_review_status(draft.review_status)
            if status == "pending":
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="node_pending_review",
                        node_id=draft.candidate_node_id,
                        message=f"节点“{draft.candidate_display_name}”仍处于待审查状态，不会进入正式图谱。",
                    )
                )
            elif status == "needs_revision":
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="node_needs_revision",
                        node_id=draft.candidate_node_id,
                        message=f"节点“{draft.candidate_display_name}”仍需修改，不会进入正式图谱。",
                    )
                )
        for edge in edges:
            if edge["status"] == "pending":
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="edge_pending_review",
                        edge_id=edge["edge_id"],
                        message="关系仍处于待审查状态，不会进入正式图谱。",
                    )
                )
            elif edge["status"] == "needs_revision":
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="edge_needs_revision",
                        edge_id=edge["edge_id"],
                        message="关系仍需修改，不会进入正式图谱。",
                    )
                )
        return issues

    def _check_nodes(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        accepted_node_ids: set[str],
    ) -> list[GraphQualityIssue]:
        issues: list[GraphQualityIssue] = []
        for draft in drafts:
            if draft.candidate_node_id not in accepted_node_ids:
                continue
            if not draft.candidate_display_name.strip():
                issues.append(
                    GraphQualityIssue(
                        severity="error",
                        code="node_missing_name",
                        node_id=draft.candidate_node_id,
                        message="已通过节点缺少显示名称。",
                    )
                )
            has_evidence = bool(draft.source_text.strip() or draft.evidence_anchors)
            if not has_evidence:
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="node_missing_evidence",
                        node_id=draft.candidate_node_id,
                        message=f"节点“{draft.candidate_display_name}”缺少来源证据。",
                    )
                )
        return issues

    def _check_edges(
        self,
        edges: list[dict[str, str]],
        accepted_node_ids: set[str],
        node_by_id: dict[str, DraftKnowledgeItemDTO],
    ) -> list[GraphQualityIssue]:
        issues: list[GraphQualityIssue] = []
        edge_counter = Counter(edge["edge_id"] for edge in edges if edge["status"] == "accepted")
        for edge_id, count in edge_counter.items():
            if count > 1:
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="duplicate_edge",
                        edge_id=edge_id,
                        message=f"存在 {count} 条重复关系，正式导出时会合并为一条。",
                    )
                )
        for edge in edges:
            if edge["status"] != "accepted":
                continue
            if not edge["source"] or not edge["target"] or not edge["relation_type"]:
                issues.append(
                    GraphQualityIssue(
                        severity="error",
                        code="edge_incomplete",
                        edge_id=edge["edge_id"],
                        message="已通过关系缺少起点、终点或关系类型。",
                    )
                )
                continue
            if edge["source"] == edge["target"]:
                issues.append(
                    GraphQualityIssue(
                        severity="error",
                        code="edge_self_loop",
                        edge_id=edge["edge_id"],
                        node_id=edge["source"],
                        message="已通过关系存在自环，需要删除或改向。",
                    )
                )
            if edge["source"] not in node_by_id or edge["target"] not in node_by_id:
                issues.append(
                    GraphQualityIssue(
                        severity="error",
                        code="edge_endpoint_missing",
                        edge_id=edge["edge_id"],
                        message="已通过关系端点不存在。",
                    )
                )
            elif edge["source"] not in accepted_node_ids or edge["target"] not in accepted_node_ids:
                issues.append(
                    GraphQualityIssue(
                        severity="error" if edge["relation_type"] == "contains" else "warning",
                        code="edge_endpoint_not_accepted",
                        edge_id=edge["edge_id"],
                        message=(
                            "已通过的包含关系连接了未通过结构节点，必须先补齐父子节点审查状态。"
                            if edge["relation_type"] == "contains"
                            else "已通过关系连接了未通过节点，正式导出时该关系会被丢弃。"
                        ),
                    )
                )
            if (
                edge["relation_type"] != "contains"
                and edge["source_kind"] == "candidate_relations"
                and not edge["evidence"].strip()
            ):
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="edge_missing_evidence",
                        edge_id=edge["edge_id"],
                        message="已通过知识关系缺少证据说明。",
                    )
                )
        return issues

    @staticmethod
    def _check_structure_dependencies(
        parent_by_child: dict[str, str],
        edges: list[dict[str, str]],
        accepted_node_ids: set[str],
    ) -> list[GraphQualityIssue]:
        accepted_contains = {
            (edge["source"], edge["target"])
            for edge in edges
            if edge["relation_type"] == "contains" and edge["status"] == "accepted"
        }
        issues: list[GraphQualityIssue] = []
        for child_id, parent_id in parent_by_child.items():
            if child_id not in accepted_node_ids:
                continue
            edge_id = f"{parent_id}--contains--{child_id}"
            if parent_id not in accepted_node_ids:
                issues.append(
                    GraphQualityIssue(
                        severity="error",
                        code="structure_parent_not_accepted",
                        node_id=child_id,
                        edge_id=edge_id,
                        message="已通过的小节或子结构，其父级结构节点尚未通过。",
                    )
                )
            elif (parent_id, child_id) not in accepted_contains:
                issues.append(
                    GraphQualityIssue(
                        severity="error",
                        code="structure_relation_not_accepted",
                        node_id=child_id,
                        edge_id=edge_id,
                        message="父子结构节点均已通过，但对应的包含关系尚未通过。",
                    )
                )
        return issues

    def _check_duplicate_node_names(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        accepted_node_ids: set[str],
    ) -> list[GraphQualityIssue]:
        names: dict[str, list[DraftKnowledgeItemDTO]] = defaultdict(list)
        for draft in drafts:
            if draft.candidate_node_id in accepted_node_ids:
                names[draft.candidate_display_name.strip()].append(draft)
        issues: list[GraphQualityIssue] = []
        for name, same_name_drafts in names.items():
            if name and len(same_name_drafts) > 1:
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="duplicate_node_name",
                        node_id=same_name_drafts[0].candidate_node_id,
                        message=f"存在 {len(same_name_drafts)} 个同名已通过节点“{name}”。",
                        details={"node_ids": [draft.candidate_node_id for draft in same_name_drafts]},
                    )
                )
        return issues

    def _check_isolated_nodes(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        accepted_node_ids: set[str],
        accepted_edges: list[dict[str, str]],
    ) -> list[GraphQualityIssue]:
        connected: set[str] = set()
        for edge in accepted_edges:
            connected.add(edge["source"])
            connected.add(edge["target"])
        issues: list[GraphQualityIssue] = []
        for draft in drafts:
            if draft.candidate_node_id not in accepted_node_ids:
                continue
            if draft.candidate_node_id not in connected:
                label = "结构节点" if is_container_node_type(draft.knowledge_type) else "知识节点"
                issues.append(
                    GraphQualityIssue(
                        severity="warning",
                        code="isolated_node",
                        node_id=draft.candidate_node_id,
                        message=f"{label}“{draft.candidate_display_name}”未连接任何已通过关系。",
                    )
                )
        return issues

    def _check_chapter_main_paths(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        accepted_node_ids: set[str],
        accepted_edges: list[dict[str, str]],
    ) -> list[GraphQualityIssue]:
        children_by_parent: dict[str, list[DraftKnowledgeItemDTO]] = defaultdict(list)
        for draft in drafts:
            if draft.candidate_node_id in accepted_node_ids and draft.candidate_parent_node_id:
                children_by_parent[draft.candidate_parent_node_id].append(draft)
        main_edges_by_parent: dict[str, int] = defaultdict(int)
        parent_by_child = {draft.candidate_node_id: draft.candidate_parent_node_id for draft in drafts}
        for edge in accepted_edges:
            if edge["relation_type"] not in self.MAIN_PATH_RELATIONS:
                continue
            source_parent = parent_by_child.get(edge["source"], "")
            target_parent = parent_by_child.get(edge["target"], "")
            if source_parent and source_parent == target_parent:
                main_edges_by_parent[source_parent] += 1
        issues: list[GraphQualityIssue] = []
        for parent_id, children in children_by_parent.items():
            knowledge_children = [
                child for child in children if not is_container_node_type(child.knowledge_type)
            ]
            if len(knowledge_children) >= 3 and main_edges_by_parent[parent_id] == 0:
                issues.append(
                    GraphQualityIssue(
                        severity="info",
                        code="chapter_main_path_missing",
                        node_id=parent_id,
                        message="该章节下已通过知识节点较多，但缺少前置、递进或推导关系构成的主路径。",
                    )
                )
        return issues

    def _check_graph_cycles(self, accepted_edges: list[dict[str, str]], *, engine: str) -> list[GraphQualityIssue]:
        learning_edges = [
            edge
            for edge in accepted_edges
            if edge["relation_type"] in self.MAIN_PATH_RELATIONS
        ]
        if not learning_edges:
            return []
        if nx is not None:
            graph = nx.DiGraph()
            graph.add_edges_from((edge["source"], edge["target"]) for edge in learning_edges)
            cycles = list(nx.simple_cycles(graph))
        else:
            cycles = self._simple_cycles_builtin(learning_edges)
        issues: list[GraphQualityIssue] = []
        for cycle in cycles[:10]:
            if len(cycle) < 2:
                continue
            issues.append(
                GraphQualityIssue(
                    severity="warning",
                    code="learning_path_cycle",
                    node_id=str(cycle[0]),
                    message="前置/递进/推导关系中存在环，可能影响学习路径推断。",
                    details={"cycle": cycle, "engine": engine},
                )
            )
        return issues

    @staticmethod
    def _simple_cycles_builtin(edges: list[dict[str, str]]) -> list[list[str]]:
        adjacency: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            adjacency[edge["source"]].add(edge["target"])
        cycles: list[list[str]] = []
        for start in list(adjacency):
            stack: deque[tuple[str, list[str]]] = deque([(start, [start])])
            while stack:
                node, path = stack.pop()
                for neighbor in adjacency.get(node, set()):
                    if neighbor == start and len(path) > 1:
                        cycle = path[:]
                        if not GraphQualityChecker._cycle_seen(cycles, cycle):
                            cycles.append(cycle)
                    elif neighbor not in path and len(path) < 12:
                        stack.append((neighbor, path + [neighbor]))
        return cycles

    @staticmethod
    def _cycle_seen(cycles: list[list[str]], cycle: list[str]) -> bool:
        cycle_set = set(cycle)
        return any(set(existing) == cycle_set for existing in cycles)
