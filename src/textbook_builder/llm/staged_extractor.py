from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from textbook_builder.contracts import (
    REVIEW_STATUS_NEEDS_EXPERT_REVIEW,
    REVIEW_STATUS_PENDING,
    SourceRecordDTO,
)
from textbook_builder.llm.clients import ChatJsonClient
from textbook_builder.pipeline_contracts import (
    ALLOWED_EVIDENCE_TYPES,
    ALLOWED_INFERENCE_SCOPES,
    EVIDENCE_TYPE_EXPLICIT,
    EVIDENCE_TYPE_MODEL_INFERRED,
    INFERENCE_SCOPE_SAME_PAGE,
    CanonicalNodeDTO,
    LocalNodeCandidateDTO,
    LocalRelationClaimDTO,
    PageEvidenceBundleDTO,
    RelationCandidateDTO,
)
from textbook_builder.utils.graph_semantics import normalize_node_type, normalize_relation_type


@dataclass(slots=True)
class StagedCandidateBatch:
    nodes: list[LocalNodeCandidateDTO]
    relation_claims: list[LocalRelationClaimDTO]


class StagedCandidatePayloadParser:
    def parse(self, raw_payload: str | dict[str, object]) -> StagedCandidateBatch:
        payload = _load_json_object(raw_payload)
        raw_nodes = payload.get("nodes") or payload.get("node_candidates") or []
        raw_claims = (
            payload.get("local_relation_claims")
            or payload.get("relation_claims")
            or payload.get("relations")
            or []
        )
        if not isinstance(raw_nodes, list) or not isinstance(raw_claims, list):
            raise ValueError("分阶段抽取响应中的nodes和local_relation_claims必须是数组。")

        nodes: list[LocalNodeCandidateDTO] = []
        for index, item in enumerate(raw_nodes, start=1):
            if not isinstance(item, dict):
                continue
            display_name = str(item.get("display_name") or item.get("name") or "").strip()
            if not display_name:
                continue
            nodes.append(
                LocalNodeCandidateDTO(
                    temporary_id=str(item.get("temporary_id") or f"local-node-{index}"),
                    display_name=display_name,
                    node_name=str(item.get("node_name") or "").strip(),
                    node_type=normalize_node_type(item.get("node_type")),
                    chapter=str(item.get("chapter") or "").strip(),
                    section=str(item.get("section") or "").strip(),
                    definition=str(item.get("definition") or item.get("source_text") or "").strip(),
                    evidence_refs=_string_list(item.get("evidence_refs")),
                    aliases=_string_list(item.get("aliases")),
                    confidence=_confidence(item.get("confidence")),
                    reasoning_summary=str(item.get("reasoning_summary") or "").strip(),
                )
            )

        claims: list[LocalRelationClaimDTO] = []
        for index, item in enumerate(raw_claims, start=1):
            if not isinstance(item, dict):
                continue
            relation_type = str(
                item.get("relation_type_candidate") or item.get("relation_type") or ""
            ).strip()
            source_mention = str(item.get("source_mention") or item.get("source") or "").strip()
            target_mention = str(item.get("target_mention") or item.get("target") or "").strip()
            source_temporary_id = str(
                item.get("source_temporary_id") or item.get("source_node_id") or ""
            ).strip()
            target_temporary_id = str(
                item.get("target_temporary_id") or item.get("target_node_id") or ""
            ).strip()
            if not relation_type or not (source_mention or source_temporary_id) or not (
                target_mention or target_temporary_id
            ):
                continue
            evidence_type = str(item.get("evidence_type") or EVIDENCE_TYPE_EXPLICIT)
            inference_scope = str(item.get("inference_scope") or INFERENCE_SCOPE_SAME_PAGE)
            claims.append(
                LocalRelationClaimDTO(
                    claim_id=str(item.get("claim_id") or f"local-claim-{index}"),
                    source_mention=source_mention,
                    target_mention=target_mention,
                    relation_type_candidate=relation_type,
                    source_temporary_id=source_temporary_id,
                    target_temporary_id=target_temporary_id,
                    evidence_type=(
                        evidence_type
                        if evidence_type in ALLOWED_EVIDENCE_TYPES
                        else EVIDENCE_TYPE_MODEL_INFERRED
                    ),
                    inference_scope=(
                        inference_scope
                        if inference_scope in ALLOWED_INFERENCE_SCOPES
                        else INFERENCE_SCOPE_SAME_PAGE
                    ),
                    evidence_refs=_string_list(item.get("evidence_refs")),
                    evidence_text=str(
                        item.get("evidence_text") or item.get("relation_evidence") or ""
                    ).strip(),
                    confidence=_confidence(item.get("confidence")),
                    reasoning_summary=str(item.get("reasoning_summary") or "").strip(),
                    review_status=str(item.get("review_status") or REVIEW_STATUS_PENDING),
                )
            )
        return StagedCandidateBatch(nodes=nodes, relation_claims=claims)


class LlmStagedPageExtractor:
    def __init__(
        self,
        client: ChatJsonClient,
        parser: StagedCandidatePayloadParser | None = None,
    ) -> None:
        self._client = client
        self._parser = parser or StagedCandidatePayloadParser()
        self.last_prompt_characters = 0

    def extract(
        self,
        *,
        record: SourceRecordDTO,
        pages: list[PageEvidenceBundleDTO],
        include_visual_review_images: bool = True,
    ) -> tuple[StagedCandidateBatch, int]:
        image_paths = [
            Path(page.image_path)
            for page in pages
            if include_visual_review_images
            and page.requires_visual_review
            and page.image_path
            and Path(page.image_path).is_file()
        ]
        prompt = self._user_prompt(record, pages)
        self.last_prompt_characters = len(prompt)
        image_call_count = 0
        image_method = getattr(self._client, "complete_json_with_images", None)
        if image_paths and callable(image_method):
            raw = image_method(
                system_prompt=self._system_prompt(),
                user_prompt=prompt,
                image_paths=image_paths,
            )
            image_call_count = 1
        else:
            raw = self._client.complete_json(
                system_prompt=self._system_prompt(),
                user_prompt=prompt,
            )
        return self._parser.parse(raw), image_call_count

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是K-12教材知识图谱的首轮证据抽取器。只输出JSON对象。"
            "一次阅读中同时输出节点候选与局部关系线索，但二者必须放在两个独立数组。"
            "节点候选不得因为关系需要而虚构；关系线索不能被当作已审核事实。"
            "每个对象都必须引用页面或文本块证据，置信度不足时如实降低confidence。"
            "目录页中的章、节、子节均按container节点抽取，并用contains明确表达版面父子层级。"
            "目录中的相邻排列只表示书本编排顺序，严禁仅凭先后顺序生成progressive、prerequisite或derives_to。"
            "不要在本阶段做跨整本书的最终节点合并或关系定稿。"
        )

    @staticmethod
    def _user_prompt(record: SourceRecordDTO, pages: list[PageEvidenceBundleDTO]) -> str:
        return json.dumps(
            {
                "task": "extract_independent_nodes_and_local_relation_claims",
                "allowed_node_types": [
                    "container",
                    "concept",
                    "property",
                    "rule",
                    "method",
                    "representation",
                    "problem_type",
                    "application",
                ],
                "allowed_relation_types": [
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
                ],
                "allowed_evidence_types": sorted(ALLOWED_EVIDENCE_TYPES),
                "allowed_inference_scopes": sorted(ALLOWED_INFERENCE_SCOPES),
                "catalog_rules": {
                    "structural_titles_are_container_nodes": True,
                    "emit_explicit_contains_for_parent_child": True,
                    "display_order_is_not_semantic_progression": True,
                },
                "output_schema": {
                    "nodes": [
                        {
                            "temporary_id": "string",
                            "display_name": "string",
                            "node_name": "string",
                            "node_type": "allowed node type",
                            "chapter": "string",
                            "section": "string",
                            "definition": "string",
                            "aliases": ["string"],
                            "evidence_refs": ["page_id or page_id#block_id"],
                            "confidence": 0.0,
                            "reasoning_summary": "string",
                        }
                    ],
                    "local_relation_claims": [
                        {
                            "claim_id": "string",
                            "source_temporary_id": "string",
                            "target_temporary_id": "string",
                            "source_mention": "string",
                            "target_mention": "string",
                            "relation_type_candidate": "allowed relation type",
                            "evidence_type": "allowed evidence type",
                            "inference_scope": "allowed inference scope",
                            "evidence_refs": ["page_id or page_id#block_id"],
                            "evidence_text": "string",
                            "confidence": 0.0,
                            "reasoning_summary": "string",
                            "review_status": "pending",
                        }
                    ],
                },
                "source_context": {
                    "source_id": record.source_id,
                    "subject": record.subject,
                    "grade": record.grade,
                    "term": record.term,
                    "source_type": record.source_type,
                },
                "pages": [_page_prompt_payload(page) for page in pages],
            },
            ensure_ascii=False,
        )


class LlmRelationCompletionService:
    """Performs the final text-only relation pass over canonical node IDs."""

    def __init__(self, client: ChatJsonClient) -> None:
        self._client = client
        self.last_prompt_characters = 0
        self.last_parse_diagnostics: dict[str, object] = {}

    def complete(
        self,
        *,
        record: SourceRecordDTO,
        pages: list[PageEvidenceBundleDTO],
        nodes: list[CanonicalNodeDTO],
        seed_relations: list[RelationCandidateDTO],
    ) -> list[RelationCandidateDTO]:
        user_prompt = json.dumps(
            {
                "task": "consolidate_relations_without_creating_nodes",
                "output_schema": {
                    "relations": [
                        {
                            "source_node_id": "existing node_id",
                            "target_node_id": "existing node_id",
                            "relation_type": "allowed relation type",
                            "evidence_type": "allowed evidence type",
                            "inference_scope": "allowed inference scope",
                            "evidence_refs": ["page_id or page_id#block_id"],
                            "evidence_text": "string",
                            "confidence": 0.0,
                            "reasoning_summary": "string",
                            "review_status": "pending|needs_expert_review",
                        }
                    ]
                },
                "source_context": {
                    "source_id": record.source_id,
                    "subject": record.subject,
                    "grade": record.grade,
                    "term": record.term,
                },
                "canonical_nodes": [
                    {
                        "node_id": node.node_id,
                        "display_name": node.display_name,
                        "node_type": node.node_type,
                        "chapter": node.chapter,
                        "section": node.section,
                        "definition": node.definition,
                        "aliases": node.aliases,
                        "evidence_refs": node.evidence_refs,
                    }
                    for node in nodes
                ],
                "seed_relations": [
                    {
                        "source_node_id": relation.source_node_id,
                        "target_node_id": relation.target_node_id,
                        "relation_type": relation.relation_type,
                        "evidence_type": relation.evidence_type,
                        "inference_scope": relation.inference_scope,
                        "evidence_refs": relation.evidence_refs,
                        "evidence_text": relation.evidence_text,
                    }
                    for relation in seed_relations
                ],
                "semantic_candidate_policy": {
                    "run_independent_semantic_audit_after_contains": True,
                    "allowed_semantic_types": [
                        "prerequisite",
                        "progressive",
                        "derives_to",
                        "explains",
                        "applies_to",
                    ],
                    "basis": (
                        "仅当节点标题的数学含义、定义/方法依赖或学科逻辑能独立支持时提出候选；"
                        "不得把目录相邻或编号先后本身当作语义证据。"
                    ),
                    "review_status": "needs_expert_review",
                    "evidence_type": "model_inferred",
                    "evidence_refs_when_only_semantic_inference": [],
                    "evidence_text_requirement": (
                        "说明具体的概念、定义、性质或方法依赖，并明确标注为待教师审查的模型推断。"
                    ),
                    "do_not_force_relation_for_every_adjacent_pair": True,
                },
                "page_evidence": [_page_relation_payload(page) for page in pages],
            },
            ensure_ascii=False,
        )
        self.last_prompt_characters = len(user_prompt)
        raw = self._client.complete_json(
            system_prompt=(
                "你是K-12教材知识图谱关系整合器。只输出JSON对象。"
                "节点已经归一化，严禁新增、重命名或合并节点；关系端点只能使用给定node_id。"
                "优先保留有教材证据的局部线索，并补充页内、相邻页和同节范围的明确关系。"
                "若材料是目录页，必须保留章/节/子节的contains结构；目录排列先后本身不构成progressive、prerequisite或derives_to。"
                "保留contains后，必须再独立审计同章兄弟节点的标题数学含义和概念/方法依赖："
                "如果学科逻辑能支持前置、递进、推导、解释或应用关系，应输出教师待审候选；"
                "不得为了数量强行连接每个相邻节点。"
                "这类模型推断关系必须标为model_inferred和needs_expert_review，"
                "并在evidence_text中写明语义依据与待教师确认。"
            ),
            user_prompt=user_prompt,
        )
        payload = _load_json_object(raw)
        raw_relations = payload.get("relations") or []
        if not isinstance(raw_relations, list):
            raise ValueError("关系整合响应中的relations必须是数组。")
        allowed_node_ids = {node.node_id for node in nodes}
        allowed_evidence_refs = _allowed_evidence_refs(pages)
        diagnostics: dict[str, object] = {
            "raw_relation_count": len(raw_relations),
            "seed_relation_count": len(seed_relations),
            "invalid_item_count": 0,
            "invalid_endpoint_or_type_count": 0,
            "filtered_evidence_ref_count": 0,
            "raw_relation_types": {},
        }
        deduplicated: dict[tuple[str, str, str], RelationCandidateDTO] = {
            (item.source_node_id, item.relation_type, item.target_node_id): item
            for item in seed_relations
        }
        for item in raw_relations:
            if not isinstance(item, dict):
                diagnostics["invalid_item_count"] = int(
                    diagnostics["invalid_item_count"]
                ) + 1
                continue
            source_id = str(item.get("source_node_id") or "").strip()
            target_id = str(item.get("target_node_id") or "").strip()
            relation_type = normalize_relation_type(item.get("relation_type"))
            raw_relation_type = str(item.get("relation_type") or "unknown")
            raw_type_counts = diagnostics["raw_relation_types"]
            if isinstance(raw_type_counts, dict):
                raw_type_counts[raw_relation_type] = int(
                    raw_type_counts.get(raw_relation_type, 0)
                ) + 1
            if (
                not source_id
                or not target_id
                or source_id == target_id
                or source_id not in allowed_node_ids
                or target_id not in allowed_node_ids
                or not relation_type
            ):
                diagnostics["invalid_endpoint_or_type_count"] = int(
                    diagnostics["invalid_endpoint_or_type_count"]
                ) + 1
                continue
            evidence_type = str(item.get("evidence_type") or EVIDENCE_TYPE_MODEL_INFERRED)
            if evidence_type not in ALLOWED_EVIDENCE_TYPES:
                evidence_type = EVIDENCE_TYPE_MODEL_INFERRED
            inference_scope = str(item.get("inference_scope") or "same_section")
            if inference_scope not in ALLOWED_INFERENCE_SCOPES:
                inference_scope = "same_section"
            raw_evidence_refs = _string_list(item.get("evidence_refs"))
            evidence_refs = [
                ref
                for ref in raw_evidence_refs
                if ref in allowed_evidence_refs
            ]
            diagnostics["filtered_evidence_ref_count"] = int(
                diagnostics["filtered_evidence_ref_count"]
            ) + (len(raw_evidence_refs) - len(evidence_refs))
            review_status = str(item.get("review_status") or REVIEW_STATUS_PENDING)
            if not evidence_refs:
                review_status = REVIEW_STATUS_NEEDS_EXPERT_REVIEW
            relation = RelationCandidateDTO(
                source_node_id=source_id,
                target_node_id=target_id,
                relation_type=relation_type,
                evidence_type=evidence_type,
                inference_scope=inference_scope,
                evidence_refs=evidence_refs,
                evidence_text=str(item.get("evidence_text") or "").strip(),
                confidence=_confidence(item.get("confidence")),
                reasoning_summary=str(item.get("reasoning_summary") or "").strip(),
                relation_source="llm_relation_completion",
                review_status=review_status,
            )
            deduplicated[(source_id, relation_type, target_id)] = relation
        completed = sorted(
            deduplicated.values(),
            key=lambda item: (item.source_node_id, item.relation_type, item.target_node_id),
        )
        final_type_counts: dict[str, int] = {}
        for relation in completed:
            final_type_counts[relation.relation_type] = (
                final_type_counts.get(relation.relation_type, 0) + 1
            )
        diagnostics.update(
            {
                "final_relation_count": len(completed),
                "final_relation_types": final_type_counts,
            }
        )
        self.last_parse_diagnostics = diagnostics
        return completed


def split_page_batches(
    pages: list[PageEvidenceBundleDTO],
    *,
    max_pages: int = 4,
    max_chars: int = 12_000,
) -> list[list[PageEvidenceBundleDTO]]:
    batches: list[list[PageEvidenceBundleDTO]] = []
    current: list[PageEvidenceBundleDTO] = []
    current_chars = 0
    for page in pages:
        page_chars = len(page.combined_text())
        if current and (len(current) >= max_pages or current_chars + page_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(page)
        current_chars += page_chars
    if current:
        batches.append(current)
    return batches


def _page_prompt_payload(page: PageEvidenceBundleDTO) -> dict[str, object]:
    return {
        "page_id": page.page_id,
        "page_index": page.page_index,
        "printed_page_number": page.printed_page_number,
        "ocr_status": page.ocr_status,
        "requires_visual_review": page.requires_visual_review,
        "text": page.source_text if not page.ocr_blocks else "",
        "blocks": [
            {
                "evidence_ref": f"{page.page_id}#{block.block_id}",
                "text": block.text,
                "confidence": block.confidence,
                "bbox": block.bbox,
            }
            for block in page.ocr_blocks
        ],
    }


def _page_relation_payload(page: PageEvidenceBundleDTO) -> dict[str, object]:
    return {
        "page_id": page.page_id,
        "page_index": page.page_index,
        "printed_page_number": page.printed_page_number,
        "text": page.combined_text(),
    }


def _allowed_evidence_refs(pages: list[PageEvidenceBundleDTO]) -> set[str]:
    allowed: set[str] = set()
    for page in pages:
        allowed.add(page.page_id)
        allowed.update(f"{page.page_id}#{block.block_id}" for block in page.ocr_blocks)
    return allowed


def _load_json_object(raw_payload: str | dict[str, object]) -> dict[str, object]:
    if isinstance(raw_payload, dict):
        return raw_payload
    text = raw_payload.strip()
    if not text:
        raise ValueError("模型响应为空。")
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型响应必须是JSON对象。")
    return payload


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _confidence(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
