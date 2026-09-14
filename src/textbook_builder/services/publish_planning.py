from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..contracts import FormalEdgeDTO, FormalGraphMetadataDTO, FormalGraphWorkbookDTO, FormalNodeDTO
from ..exporters.xlsx_exporter import XlsxWorkbookExporter
from ..mapping import LayerMappingBuilder
from ..review_document import (
    ExportProfileDTO,
    P2ReviewDocumentDTO,
    RELATION_FAMILY_MEMBERSHIP,
    RELATION_FAMILY_SEMANTIC,
    RELATION_FAMILY_STRUCTURE,
    RELATION_FAMILY_UNRESOLVED,
)
from ..utils.graph_semantics import normalize_relation_type
from ..utils.review_status import normalize_review_status


PUBLISH_FINGERPRINT_VERSION = 1
TARGET_CAPABILITY_VERSION = "p4-v1-capabilities-1"


@dataclass(frozen=True, slots=True)
class TargetCapability:
    target_protocol: str
    capability_version: str
    supported_relation_types: frozenset[str]
    single_membership: bool = True


P4_V1_CAPABILITY = TargetCapability(
    target_protocol="p4_v1_single_membership",
    capability_version=TARGET_CAPABILITY_VERSION,
    supported_relation_types=frozenset(
        {
            "contains",
            "prerequisite",
            "progressive",
            "derives_to",
            "explains",
            "equivalent",
            "parallel",
            "contrast",
            "applies_to",
            "represented_by",
        }
    ),
)


@dataclass(frozen=True, slots=True)
class PublishIssue:
    code: str
    message: str
    object_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class PublishPlan:
    plan_id: str
    status: str
    document_id: str
    document_revision: int
    business_fingerprint: str
    fingerprint_version: int
    profile_id: str
    profile_fingerprint: str
    target_protocol: str
    capability_version: str
    selected_node_ids: list[str]
    selected_relation_ids: list[str]
    primary_membership_by_node: dict[str, str]
    suggested_primary_membership_by_node: dict[str, str]
    ancestor_node_ids: list[str]
    excluded_counts: dict[str, int]
    blockers: list[PublishIssue] = field(default_factory=list)
    warnings: list[PublishIssue] = field(default_factory=list)
    omitted_information: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.status == "ready" and not self.blockers


@dataclass(slots=True)
class FormalProjection:
    plan_id: str
    document_id: str
    document_revision: int
    business_fingerprint: str
    workbook: FormalGraphWorkbookDTO
    selected_membership_by_node: dict[str, str]
    source_relation_ids: list[str]


@dataclass(slots=True)
class PublishResult:
    status: str
    run_id: str
    output_dir: Path | None
    formal_path: Path | None
    layer_mapping_path: Path | None
    manifest_path: Path | None
    message: str = ""


class PublishPlanningService:
    """Pure preflight/formal projection plus an atomic, version-checked publisher."""

    def __init__(
        self,
        *,
        capability: TargetCapability = P4_V1_CAPABILITY,
        exporter: XlsxWorkbookExporter | None = None,
        layer_mapping_builder: LayerMappingBuilder | None = None,
    ) -> None:
        self._capability = capability
        self._exporter = exporter or XlsxWorkbookExporter()
        self._layer_mapping_builder = layer_mapping_builder or LayerMappingBuilder()
        self._publishing = False

    def preflight(
        self,
        document: P2ReviewDocumentDTO,
        export_profile: ExportProfileDTO | None = None,
    ) -> PublishPlan:
        profile = export_profile or ExportProfileDTO(profile_id="default")
        blockers: list[PublishIssue] = []
        warnings: list[PublishIssue] = []
        document_errors = document.validate()
        if document_errors:
            blockers.append(
                PublishIssue(
                    "document_integrity_error",
                    "建设态文档存在引用或结构错误。",
                    tuple(document_errors),
                )
            )
        if profile.target_protocol != self._capability.target_protocol:
            blockers.append(
                PublishIssue(
                    "target_protocol_unsupported",
                    f"当前发布器不支持目标协议 {profile.target_protocol}。",
                )
            )
        nodes = {
            node.node_id: node
            for node in document.nodes
            if node.lifecycle_state == "active"
        }
        scopes = {
            scope.scope_id: scope
            for scope in document.scopes
            if scope.lifecycle_state == "active" and scope.scope_id in nodes
        }
        active_relations = [
            relation
            for relation in document.relations
            if relation.lifecycle_state == "active"
        ]
        accepted_relations = [
            relation
            for relation in active_relations
            if normalize_review_status(relation.review_status) == "accepted"
        ]
        accepted_node_ids = {
            node_id
            for node_id, node in nodes.items()
            if normalize_review_status(node.review_status) == "accepted"
        }
        knowledge_ids = accepted_node_ids - set(scopes)
        explicitly_selected = set(profile.selected_node_ids)
        selected_knowledge = (
            knowledge_ids & explicitly_selected if explicitly_selected else set(knowledge_ids)
        )
        invalid_selected = explicitly_selected - set(nodes)
        if invalid_selected:
            blockers.append(
                PublishIssue(
                    "selected_node_missing",
                    "发布范围包含不存在或已归档的节点。",
                    tuple(sorted(invalid_selected)),
                )
            )
        not_accepted_selected = (explicitly_selected & set(nodes)) - accepted_node_ids
        if not_accepted_selected:
            blockers.append(
                PublishIssue(
                    "selected_node_not_accepted",
                    "发布范围包含未通过节点。",
                    tuple(sorted(not_accepted_selected)),
                )
            )
        selected_relation_filter = set(profile.selected_relation_ids)
        invalid_relation_ids = selected_relation_filter - {item.relation_id for item in active_relations}
        if invalid_relation_ids:
            blockers.append(
                PublishIssue(
                    "selected_relation_missing",
                    "发布范围包含不存在或已归档的关系。",
                    tuple(sorted(invalid_relation_ids)),
                )
            )
        selected_accepted_relations = [
            relation
            for relation in accepted_relations
            if not selected_relation_filter or relation.relation_id in selected_relation_filter
        ]
        # Explicit accepted semantic relations bring their accepted endpoints into the closure.
        for relation in selected_accepted_relations:
            if relation.relation_family not in {RELATION_FAMILY_SEMANTIC, RELATION_FAMILY_UNRESOLVED}:
                continue
            for node_id in (relation.source_node_id, relation.target_node_id):
                if node_id in scopes:
                    continue
                if node_id not in accepted_node_ids:
                    blockers.append(
                        PublishIssue(
                            "accepted_relation_endpoint_not_accepted",
                            "已通过关系的端点未通过，不能生成悬空正式边。",
                            (relation.relation_id, node_id),
                        )
                    )
                elif not explicitly_selected or relation.relation_id in selected_relation_filter:
                    selected_knowledge.add(node_id)
        if not selected_knowledge:
            blockers.append(PublishIssue("no_accepted_nodes", "没有可发布的已通过知识节点。"))

        memberships_by_node: dict[str, list[object]] = {}
        for relation in accepted_relations:
            if relation.relation_family == RELATION_FAMILY_MEMBERSHIP:
                memberships_by_node.setdefault(relation.target_node_id, []).append(relation)
        selected_memberships: dict[str, str] = {}
        suggested: dict[str, str] = {}
        selected_membership_relations: list[object] = []
        for node_id in sorted(selected_knowledge):
            candidates = [
                relation
                for relation in memberships_by_node.get(node_id, [])
                if relation.source_node_id in scopes
            ]
            chosen_id = profile.primary_membership_by_node.get(node_id, "")
            chosen = next((item for item in candidates if item.relation_id == chosen_id), None)
            if chosen_id and chosen is None:
                blockers.append(
                    PublishIssue(
                        "primary_membership_invalid",
                        "已保存的主归属已失效，需要重新裁决。",
                        (node_id, chosen_id),
                    )
                )
            elif chosen is not None:
                selected_memberships[node_id] = chosen.relation_id
                selected_membership_relations.append(chosen)
            elif len(candidates) == 1:
                suggested[node_id] = candidates[0].relation_id
                blockers.append(
                    PublishIssue(
                        "primary_membership_confirmation_required",
                        "唯一有效归属已形成建议，仍需显式确认后发布。",
                        (node_id, candidates[0].relation_id),
                    )
                )
            elif len(candidates) > 1:
                blockers.append(
                    PublishIssue(
                        "primary_membership_required",
                        "目标协议只支持单归属，请为节点选择主归属。",
                        (node_id, *(item.relation_id for item in candidates)),
                    )
                )
            else:
                blockers.append(
                    PublishIssue(
                        "accepted_membership_missing",
                        "知识节点没有已通过且有效的教材归属。",
                        (node_id,),
                    )
                )

        structure_by_child = {
            relation.target_node_id: relation
            for relation in accepted_relations
            if relation.relation_family == RELATION_FAMILY_STRUCTURE
        }
        ancestor_ids: set[str] = set()
        for membership in selected_membership_relations:
            current = membership.source_node_id
            seen: set[str] = set()
            while current and current not in seen:
                seen.add(current)
                ancestor_ids.add(current)
                node = nodes.get(current)
                if node is None or normalize_review_status(node.review_status) != "accepted":
                    blockers.append(
                        PublishIssue(
                            "required_scope_not_accepted",
                            "主归属所需教材结构节点未通过。",
                            (current,),
                        )
                    )
                parent_relation = structure_by_child.get(current)
                if parent_relation is None:
                    break
                current = parent_relation.source_node_id

        selected_semantic_relations: list[object] = []
        for relation in selected_accepted_relations:
            if relation.relation_family == RELATION_FAMILY_UNRESOLVED:
                blockers.append(
                    PublishIssue(
                        "unresolved_relation_not_publishable",
                        "未决旧关系不能直接发布。",
                        (relation.relation_id,),
                    )
                )
                continue
            if relation.relation_family != RELATION_FAMILY_SEMANTIC:
                continue
            if relation.source_node_id not in selected_knowledge or relation.target_node_id not in selected_knowledge:
                continue
            relation_type = normalize_relation_type(relation.relation_type)
            if relation_type not in self._capability.supported_relation_types:
                blockers.append(
                    PublishIssue(
                        "relation_type_unsupported",
                        f"目标协议不支持关系类型 {relation.relation_type}。",
                        (relation.relation_id,),
                    )
                )
                continue
            selected_semantic_relations.append(relation)

        secondary_count = sum(
            max(0, len(memberships_by_node.get(node_id, [])) - 1)
            for node_id in selected_knowledge
        )
        omitted = []
        if secondary_count:
            omitted.append(f"P4 v1 不携带 {secondary_count} 条次归属。")
        if document.occurrences:
            omitted.append(f"P4 v1 不携带 {len(document.occurrences)} 条出现位置记录。")
        excluded_counts = {
            "nodes_not_accepted": sum(
                normalize_review_status(node.review_status) != "accepted"
                for node in nodes.values()
            ),
            "relations_not_accepted": sum(
                normalize_review_status(relation.review_status) != "accepted"
                for relation in active_relations
            ),
            "selected_knowledge_nodes": len(selected_knowledge),
            "selected_semantic_relations": len(selected_semantic_relations),
        }
        profile_fingerprint = _fingerprint(asdict(profile))
        business_fingerprint = document.business_fingerprint()
        plan_core = {
            "document_id": document.metadata.document_id,
            "revision": document.metadata.revision,
            "business_fingerprint": business_fingerprint,
            "profile_fingerprint": profile_fingerprint,
            "capability_version": self._capability.capability_version,
            "selected_nodes": sorted(selected_knowledge),
            "selected_relations": sorted(
                item.relation_id for item in selected_semantic_relations
            ),
            "selected_memberships": selected_memberships,
        }
        plan_id = f"plan_{_fingerprint(plan_core)[:24]}"
        status = "ready" if not blockers else (
            "needs_decision"
            if all(
                issue.code
                in {"primary_membership_required", "primary_membership_confirmation_required"}
                for issue in blockers
            )
            else "blocked"
        )
        return PublishPlan(
            plan_id=plan_id,
            status=status,
            document_id=document.metadata.document_id,
            document_revision=document.metadata.revision,
            business_fingerprint=business_fingerprint,
            fingerprint_version=PUBLISH_FINGERPRINT_VERSION,
            profile_id=profile.profile_id,
            profile_fingerprint=profile_fingerprint,
            target_protocol=profile.target_protocol,
            capability_version=self._capability.capability_version,
            selected_node_ids=sorted(selected_knowledge),
            selected_relation_ids=sorted(
                item.relation_id for item in selected_semantic_relations
            ),
            primary_membership_by_node=dict(sorted(selected_memberships.items())),
            suggested_primary_membership_by_node=dict(sorted(suggested.items())),
            ancestor_node_ids=sorted(ancestor_ids),
            excluded_counts=excluded_counts,
            blockers=blockers,
            warnings=warnings,
            omitted_information=omitted,
        )

    def build_formal_projection(
        self,
        document: P2ReviewDocumentDTO,
        ready_plan: PublishPlan,
    ) -> FormalProjection:
        self._validate_plan(document, ready_plan)
        if not ready_plan.ready:
            raise ValueError("发布计划尚未 ready，不能构造正式投影。")
        nodes = {node.node_id: node for node in document.nodes}
        relations = {relation.relation_id: relation for relation in document.relations}
        selected_node_ids = set(ready_plan.selected_node_ids)
        included_ids = selected_node_ids | set(ready_plan.ancestor_node_ids)
        structure_parent: dict[str, str] = {}
        source_relation_ids: list[str] = []
        for relation in document.relations:
            if (
                relation.relation_family == RELATION_FAMILY_STRUCTURE
                and normalize_review_status(relation.review_status) == "accepted"
                and relation.target_node_id in included_ids
                and relation.source_node_id in included_ids
            ):
                structure_parent[relation.target_node_id] = relation.source_node_id
                source_relation_ids.append(relation.relation_id)
        membership_relation_by_node = {
            node_id: relations[relation_id]
            for node_id, relation_id in ready_plan.primary_membership_by_node.items()
        }
        parent_by_node = dict(structure_parent)
        for node_id, relation in membership_relation_by_node.items():
            parent_by_node[node_id] = relation.source_node_id
            source_relation_ids.append(relation.relation_id)
        prerequisite_by_target: dict[str, list[str]] = {}
        semantic_relations = [relations[relation_id] for relation_id in ready_plan.selected_relation_ids]
        for relation in semantic_relations:
            if normalize_relation_type(relation.relation_type) == "prerequisite":
                prerequisite_by_target.setdefault(relation.target_node_id, []).append(
                    relation.source_node_id
                )
        formal_nodes: list[FormalNodeDTO] = []
        for node_id in sorted(included_ids):
            node = nodes[node_id]
            formal_nodes.append(
                FormalNodeDTO(
                    node_id=node.node_id,
                    subject=node.subject,
                    grade=node.grade,
                    term=node.term,
                    chapter=node.chapter,
                    display_name=node.display_name,
                    node_name=node.node_name,
                    parent_node_id=parent_by_node.get(node_id, ""),
                    prerequisite_nodes=sorted(prerequisite_by_target.get(node_id, [])),
                    node_type=node.node_type,
                    knowledge_type=node.node_type,
                    cognitive_level=node.cognitive_level,
                    education_stage=node.education_stage,
                    grade_band=node.grade_band,
                    subject_tags=list(node.subject_tags),
                    textbook_version=ready_plan.target_protocol,
                    source_locations=[node.source_location] if node.source_location else [],
                    extraction_tags=[node.extractor_source] if node.extractor_source else [],
                )
            )
        formal_edges: list[FormalEdgeDTO] = []
        for node_id, parent_id in sorted(parent_by_node.items()):
            relation_id = (
                ready_plan.primary_membership_by_node.get(node_id)
                or next(
                    (
                        relation.relation_id
                        for relation in document.relations
                        if relation.relation_family == RELATION_FAMILY_STRUCTURE
                        and relation.source_node_id == parent_id
                        and relation.target_node_id == node_id
                        and normalize_review_status(relation.review_status) == "accepted"
                    ),
                    "",
                )
            )
            relation = relations.get(relation_id)
            formal_edges.append(
                FormalEdgeDTO(
                    source_node_id=parent_id,
                    target_node_id=node_id,
                    relation_type="contains",
                    confidence=relation.confidence if relation else 1.0,
                    relation_source=f"review_relation:{relation_id}" if relation_id else "review_relation",
                    review_status="accepted",
                )
            )
        for relation in semantic_relations:
            formal_edges.append(
                FormalEdgeDTO(
                    source_node_id=relation.source_node_id,
                    target_node_id=relation.target_node_id,
                    relation_type=normalize_relation_type(relation.relation_type),
                    confidence=relation.confidence,
                    relation_evidence=relation.reasoning_summary,
                    relation_source=f"review_relation:{relation.relation_id}",
                    review_status="accepted",
                )
            )
            source_relation_ids.append(relation.relation_id)
        first = next(iter(nodes.values()), None)
        workbook = FormalGraphWorkbookDTO(
            metadata=FormalGraphMetadataDTO(
                graph_id=f"FORMAL_{document.metadata.source_identity or document.metadata.document_id}",
                subject=first.subject if first else "",
                grade_scope=sorted({nodes[node_id].grade for node_id in included_ids if nodes[node_id].grade}),
                term_scope=sorted({nodes[node_id].term for node_id in included_ids if nodes[node_id].term}),
                version="v1",
            ),
            nodes=formal_nodes,
            edges=formal_edges,
        )
        return FormalProjection(
            plan_id=ready_plan.plan_id,
            document_id=document.metadata.document_id,
            document_revision=document.metadata.revision,
            business_fingerprint=ready_plan.business_fingerprint,
            workbook=workbook,
            selected_membership_by_node=dict(ready_plan.primary_membership_by_node),
            source_relation_ids=sorted(set(source_relation_ids)),
        )

    def publish(
        self,
        document: P2ReviewDocumentDTO,
        ready_plan: PublishPlan,
        *,
        expected_revision: int,
        output_root: str | Path,
    ) -> PublishResult:
        if self._publishing:
            raise RuntimeError("已有发布正在进行。")
        if expected_revision != document.metadata.revision:
            raise ValueError("文档 revision 已变化，发布计划已过期。")
        projection = self.build_formal_projection(document, ready_plan)
        self._publishing = True
        root = Path(output_root)
        root.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        staging = root / f".staging-{run_id}"
        final_dir = root / run_id
        try:
            staging.mkdir(parents=False, exist_ok=False)
            formal_path = staging / "formal_graph.xlsx"
            mapping_path = staging / "layer_mapping.json"
            manifest_path = staging / "publish_manifest.json"
            self._exporter.export_formal(projection.workbook, formal_path)
            mapping = self._layer_mapping_builder.build(projection.workbook)
            mapping_path.write_text(
                json.dumps(mapping.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            manifest = {
                "schema_version": 1,
                "status": "completed",
                "run_id": run_id,
                "plan": asdict(ready_plan),
                "projection": {
                    "document_id": projection.document_id,
                    "document_revision": projection.document_revision,
                    "business_fingerprint": projection.business_fingerprint,
                    "node_count": len(projection.workbook.nodes),
                    "edge_count": len(projection.workbook.edges),
                    "source_relation_ids": projection.source_relation_ids,
                },
                "files": {
                    formal_path.name: _file_sha256(formal_path),
                    mapping_path.name: _file_sha256(mapping_path),
                },
                "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(staging, final_dir)
            pointer_tmp = root / f".current-{uuid4().hex}.json"
            pointer = root / "current.json"
            pointer_tmp.write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "path": final_dir.name,
                        "manifest_sha256": _file_sha256(final_dir / manifest_path.name),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(pointer_tmp, pointer)
            return PublishResult(
                status="completed",
                run_id=run_id,
                output_dir=final_dir,
                formal_path=final_dir / formal_path.name,
                layer_mapping_path=final_dir / mapping_path.name,
                manifest_path=final_dir / manifest_path.name,
            )
        except Exception as exc:
            failed_dir = root / f".failed-{run_id}"
            if staging.exists():
                try:
                    os.replace(staging, failed_dir)
                except OSError:
                    pass
            return PublishResult(
                status="failed",
                run_id=run_id,
                output_dir=failed_dir if failed_dir.exists() else None,
                formal_path=None,
                layer_mapping_path=None,
                manifest_path=None,
                message=str(exc),
            )
        finally:
            self._publishing = False

    def _validate_plan(self, document: P2ReviewDocumentDTO, plan: PublishPlan) -> None:
        if document.metadata.document_id != plan.document_id:
            raise ValueError("发布计划不属于当前文档。")
        if document.metadata.revision != plan.document_revision:
            raise ValueError("文档 revision 已变化，发布计划已过期。")
        if document.business_fingerprint() != plan.business_fingerprint:
            raise ValueError("文档业务内容已变化，发布计划已过期。")
        profile = document.profile_by_id(plan.profile_id)
        if profile is None and plan.profile_id != "default":
            raise ValueError("发布配置已不存在，发布计划已过期。")
        current_profile = profile or ExportProfileDTO(profile_id="default")
        if _fingerprint(asdict(current_profile)) != plan.profile_fingerprint:
            raise ValueError("发布配置已变化，发布计划已过期。")
        if plan.capability_version != self._capability.capability_version:
            raise ValueError("目标能力表已变化，发布计划已过期。")


def _fingerprint(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "FormalProjection",
    "P4_V1_CAPABILITY",
    "PublishIssue",
    "PublishPlan",
    "PublishPlanningService",
    "PublishResult",
    "TargetCapability",
]
