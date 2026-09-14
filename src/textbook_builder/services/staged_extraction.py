from __future__ import annotations

from dataclasses import dataclass, replace

from textbook_builder.contracts import (
    REVIEW_STATUS_NEEDS_EXPERT_REVIEW,
    DraftKnowledgeItemDTO,
    EvidenceAnchorDTO,
    SourceRecordDTO,
)
from textbook_builder.llm.clients import ChatJsonClient
from textbook_builder.llm.staged_extractor import (
    LlmRelationCompletionService,
    LlmStagedPageExtractor,
    StagedCandidateBatch,
    split_page_batches,
)
from textbook_builder.normalizers.node_canonicalizer import NodeCanonicalizer
from textbook_builder.pipeline_contracts import (
    CanonicalNodeDTO,
    BookManifestDTO,
    LocalNodeCandidateDTO,
    LocalRelationClaimDTO,
    NodeAliasMappingDTO,
    PageEvidenceBundleDTO,
    RelationCandidateDTO,
)
from textbook_builder.services.relation_consolidation import RelationClaimConsolidator
from textbook_builder.services.book_manifest import BookManifestBuilder
from textbook_builder.normalizers.node_canonicalizer import normalized_mention
from textbook_builder.utils.hierarchy import synchronize_draft_hierarchy


@dataclass(slots=True)
class StagedExtractionOutcome:
    drafts: list[DraftKnowledgeItemDTO]
    local_nodes: list[LocalNodeCandidateDTO]
    local_relation_claims: list[LocalRelationClaimDTO]
    canonical_nodes: list[CanonicalNodeDTO]
    alias_mappings: list[NodeAliasMappingDTO]
    relation_candidates: list[RelationCandidateDTO]
    unresolved_relation_claims: list[LocalRelationClaimDTO]
    relation_completion_diagnostics: list[dict[str, object]]
    warnings: list[str]
    image_model_calls: int
    text_model_calls: int
    model_input_characters: int
    book_manifest: BookManifestDTO


class StagedTextbookExtractionService:
    def __init__(
        self,
        client: ChatJsonClient,
        *,
        first_pass_extractor: LlmStagedPageExtractor | None = None,
        node_canonicalizer: NodeCanonicalizer | None = None,
        relation_consolidator: RelationClaimConsolidator | None = None,
        relation_completion: LlmRelationCompletionService | None = None,
    ) -> None:
        self._client = client
        self._first_pass_extractor = first_pass_extractor or LlmStagedPageExtractor(client)
        self._node_canonicalizer = node_canonicalizer or NodeCanonicalizer()
        self._relation_consolidator = relation_consolidator or RelationClaimConsolidator()
        self._relation_completion = relation_completion or LlmRelationCompletionService(client)

    def extract(
        self,
        *,
        record: SourceRecordDTO,
        pages: list[PageEvidenceBundleDTO],
        run_relation_completion: bool = True,
    ) -> StagedExtractionOutcome:
        if not pages:
            raise ValueError("分阶段抽取至少需要一个页面证据包。")
        local_nodes: list[LocalNodeCandidateDTO] = []
        local_claims: list[LocalRelationClaimDTO] = []
        image_model_calls = 0
        text_model_calls = 0
        model_input_characters = 0
        relation_completion_diagnostics: list[dict[str, object]] = []
        evidence_warnings: list[str] = []
        book_manifest = BookManifestBuilder().build(
            pages,
            book_id=record.source_id,
            source_path=record.source_path,
            subject=record.subject,
            grade=record.grade,
            term=record.term,
        )
        for batch_index, batch in enumerate(split_page_batches(pages), start=1):
            extracted, image_calls = self._first_pass_extractor.extract(record=record, pages=batch)
            model_input_characters += self._first_pass_extractor.last_prompt_characters
            extracted, batch_warnings = _sanitize_batch_evidence(extracted, batch)
            evidence_warnings.extend(batch_warnings)
            prefix = f"batch-{batch_index}:"
            local_nodes.extend(
                replace(node, temporary_id=f"{prefix}{node.temporary_id}")
                for node in extracted.nodes
            )
            local_claims.extend(
                replace(
                    claim,
                    claim_id=f"{prefix}{claim.claim_id}",
                    source_temporary_id=(
                        f"{prefix}{claim.source_temporary_id}"
                        if claim.source_temporary_id
                        else ""
                    ),
                    target_temporary_id=(
                        f"{prefix}{claim.target_temporary_id}"
                        if claim.target_temporary_id
                        else ""
                    ),
                )
                for claim in extracted.relation_claims
            )
            image_model_calls += image_calls
            text_model_calls += 0 if image_calls else 1
        if not local_nodes:
            raise ValueError("首轮模型响应未生成有效节点候选。")

        canonical_nodes, alias_mappings = self._node_canonicalizer.canonicalize(
            local_nodes,
            subject=record.subject,
            grade=record.grade,
            term=record.term,
        )
        consolidated = self._relation_consolidator.consolidate(
            local_claims,
            canonical_nodes=canonical_nodes,
            alias_mappings=alias_mappings,
        )
        relations = consolidated.relations
        warnings = [*evidence_warnings, *consolidated.warnings]
        if run_relation_completion and len(canonical_nodes) >= 2:
            relation_index = {
                (item.source_node_id, item.relation_type, item.target_node_id): item
                for item in relations
            }
            for group_nodes, group_pages in _relation_completion_groups(
                canonical_nodes,
                pages,
                book_manifest,
            ):
                group_node_ids = {node.node_id for node in group_nodes}
                seed_relations = [
                    relation
                    for relation in relations
                    if relation.source_node_id in group_node_ids
                    and relation.target_node_id in group_node_ids
                ]
                try:
                    completed = self._relation_completion.complete(
                        record=record,
                        pages=group_pages,
                        nodes=group_nodes,
                        seed_relations=seed_relations,
                    )
                    text_model_calls += 1
                    model_input_characters += self._relation_completion.last_prompt_characters
                    relation_completion_diagnostics.append(
                        {
                            "chapter": group_nodes[0].chapter or "选定范围",
                            "node_count": len(group_nodes),
                            "page_indices": [page.page_index for page in group_pages],
                            "status": "completed",
                            **self._relation_completion.last_parse_diagnostics,
                        }
                    )
                    for relation in completed:
                        relation_index[
                            (
                                relation.source_node_id,
                                relation.relation_type,
                                relation.target_node_id,
                            )
                        ] = relation
                except Exception as exc:
                    chapter_label = group_nodes[0].chapter or "选定范围"
                    relation_completion_diagnostics.append(
                        {
                            "chapter": chapter_label,
                            "node_count": len(group_nodes),
                            "page_indices": [page.page_index for page in group_pages],
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error_message": str(exc),
                        }
                    )
                    warnings.append(
                        f"{chapter_label} 的关系整合失败，已保留局部关系线索结果："
                        f"{type(exc).__name__}: {exc}"
                    )
            relations = sorted(
                relation_index.values(),
                key=lambda item: (item.source_node_id, item.relation_type, item.target_node_id),
            )
        drafts = _to_drafts(
            record=record,
            pages=pages,
            nodes=canonical_nodes,
            relations=relations,
        )
        synchronize_draft_hierarchy(drafts)
        return StagedExtractionOutcome(
            drafts=drafts,
            local_nodes=local_nodes,
            local_relation_claims=local_claims,
            canonical_nodes=canonical_nodes,
            alias_mappings=alias_mappings,
            relation_candidates=relations,
            unresolved_relation_claims=consolidated.unresolved_claims,
            relation_completion_diagnostics=relation_completion_diagnostics,
            warnings=warnings,
            image_model_calls=image_model_calls,
            text_model_calls=text_model_calls,
            model_input_characters=model_input_characters,
            book_manifest=book_manifest,
        )


def _to_drafts(
    *,
    record: SourceRecordDTO,
    pages: list[PageEvidenceBundleDTO],
    nodes: list[CanonicalNodeDTO],
    relations: list[RelationCandidateDTO],
) -> list[DraftKnowledgeItemDTO]:
    page_by_id = {page.page_id: page for page in pages}
    relations_by_source: dict[str, list[dict[str, object]]] = {}
    for relation in relations:
        relations_by_source.setdefault(relation.source_node_id, []).append(
            {
                "source_node_id": relation.source_node_id,
                "target_node_id": relation.target_node_id,
                "relation_type": relation.relation_type,
                "confidence": relation.confidence,
                "relation_evidence": relation.evidence_text,
                "reasoning_summary": relation.reasoning_summary,
                "relation_source": relation.relation_source,
                "review_status": relation.review_status,
                "evidence_type": relation.evidence_type,
                "inference_scope": relation.inference_scope,
                "evidence_refs": list(relation.evidence_refs),
            }
        )

    drafts: list[DraftKnowledgeItemDTO] = []
    for node in nodes:
        anchors = _evidence_anchors(record=record, node=node, page_by_id=page_by_id)
        source_locations = sorted(
            {
                anchor.source_location
                for anchor in anchors
                if anchor.source_location
            }
        )
        source_text = node.definition or "\n".join(
            anchor.anchor_text for anchor in anchors if anchor.anchor_text
        )
        drafts.append(
            DraftKnowledgeItemDTO(
                draft_id=f"draft_{node.node_id}",
                subject=record.subject,
                grade=record.grade,
                term=record.term,
                chapter=node.chapter or "manual_scope",
                section=node.section,
                candidate_display_name=node.display_name,
                candidate_node_name=node.node_name,
                candidate_node_id=node.node_id,
                candidate_relations=relations_by_source.get(node.node_id, []),
                knowledge_type=node.node_type,
                education_stage=record.education_stage,
                grade_band=record.grade_band,
                subject_tags=list(record.subject_tags),
                source_id=record.source_id,
                source_path=record.source_path,
                source_format=record.source_format,
                source_document_type=record.source_document_type,
                source_text=source_text,
                source_location=",".join(source_locations),
                evidence_anchors=anchors,
                confidence=node.confidence,
                reasoning_summary="节点已完成保守归一化；关系仍需教师审查。",
                extractor_source="staged_ocr_llm_v1",
                review_status=node.review_status,
            )
        )
    return drafts


def _evidence_anchors(
    *,
    record: SourceRecordDTO,
    node: CanonicalNodeDTO,
    page_by_id: dict[str, PageEvidenceBundleDTO],
) -> list[EvidenceAnchorDTO]:
    anchors: list[EvidenceAnchorDTO] = []
    for index, evidence_ref in enumerate(node.evidence_refs, start=1):
        page_id, separator, block_id = evidence_ref.partition("#")
        page = page_by_id.get(page_id)
        if page is None:
            continue
        block = next((item for item in page.ocr_blocks if item.block_id == block_id), None)
        anchor_text = block.text if block is not None else page.combined_text()[:600]
        anchors.append(
            EvidenceAnchorDTO(
                anchor_id=f"anchor-{node.node_id}-{index}",
                source_id=record.source_id,
                source_path=record.source_path,
                source_format=record.source_format,
                source_document_type=record.source_document_type,
                source_location=f"page={page.page_index}",
                page_index=page.page_index,
                block_id=block_id if separator else "",
                bbox=dict(block.bbox) if block is not None else {},
                anchor_text=anchor_text,
                target_type="node",
                target_ids=[node.node_id],
                confidence=block.confidence if block is not None else node.confidence,
                created_by="staged_ocr_llm_v1",
            )
        )
    if anchors:
        return anchors
    fallback_page = next((page for page in page_by_id.values() if page.combined_text()), None)
    if fallback_page is None:
        return []
    return [
        EvidenceAnchorDTO(
            anchor_id=f"anchor-{node.node_id}-fallback",
            source_id=record.source_id,
            source_path=record.source_path,
            source_format=record.source_format,
            source_document_type=record.source_document_type,
            source_location=f"page={fallback_page.page_index}",
            page_index=fallback_page.page_index,
            anchor_text=fallback_page.combined_text()[:600],
            target_type="node",
            target_ids=[node.node_id],
            confidence=min(node.confidence, 0.5),
            created_by="staged_ocr_llm_v1_fallback",
        )
    ]


def _relation_completion_groups(
    nodes: list[CanonicalNodeDTO],
    pages: list[PageEvidenceBundleDTO],
    book_manifest: BookManifestDTO,
    *,
    max_nodes: int = 80,
) -> list[tuple[list[CanonicalNodeDTO], list[PageEvidenceBundleDTO]]]:
    nodes_by_chapter: dict[str, list[CanonicalNodeDTO]] = {}
    for node in nodes:
        key = normalized_mention(node.chapter) or "selected_scope"
        nodes_by_chapter.setdefault(key, []).append(node)

    page_by_id = {page.page_id: page for page in pages}
    groups: list[tuple[list[CanonicalNodeDTO], list[PageEvidenceBundleDTO]]] = []
    for chapter_key in sorted(nodes_by_chapter):
        chapter_nodes = nodes_by_chapter[chapter_key]
        for start in range(0, len(chapter_nodes), max_nodes):
            chunk = chapter_nodes[start : start + max_nodes]
            referenced_page_ids = {
                evidence_ref.partition("#")[0]
                for node in chunk
                for evidence_ref in node.evidence_refs
            }
            chunk_pages = [
                page_by_id[page_id]
                for page_id in sorted(
                    referenced_page_ids,
                    key=lambda page_id: page_by_id[page_id].page_index
                    if page_id in page_by_id
                    else 10**9,
                )
                if page_id in page_by_id
            ]
            if not chunk_pages:
                manifest_chapter = next(
                    (
                        chapter
                        for chapter in book_manifest.chapters
                        if chapter_key in normalized_mention(chapter.title)
                        or normalized_mention(chapter.title) in chapter_key
                    ),
                    None,
                )
                if manifest_chapter is not None:
                    chunk_pages = [
                        page
                        for page in pages
                        if manifest_chapter.start_page <= page.page_index <= manifest_chapter.end_page
                    ]
            if not chunk_pages and len(nodes_by_chapter) == 1:
                chunk_pages = list(pages)
            if len(chunk) >= 2:
                groups.append((chunk, chunk_pages))
    return groups


def _sanitize_batch_evidence(
    extracted: StagedCandidateBatch,
    pages: list[PageEvidenceBundleDTO],
) -> tuple[StagedCandidateBatch, list[str]]:
    allowed_refs: set[str] = set()
    for page in pages:
        allowed_refs.add(page.page_id)
        allowed_refs.update(f"{page.page_id}#{block.block_id}" for block in page.ocr_blocks)
    warnings: list[str] = []
    nodes: list[LocalNodeCandidateDTO] = []
    for node in extracted.nodes:
        valid_refs = [ref for ref in node.evidence_refs if ref in allowed_refs]
        if len(valid_refs) != len(node.evidence_refs):
            warnings.append(f"节点 {node.temporary_id} 含无效证据引用，已移除。")
        if not valid_refs:
            warnings.append(f"节点 {node.temporary_id} 没有可验证的页面证据，需要人工复核。")
        nodes.append(
            replace(
                node,
                evidence_refs=valid_refs,
                confidence=min(node.confidence, 0.5) if not valid_refs else node.confidence,
            )
        )
    claims: list[LocalRelationClaimDTO] = []
    for claim in extracted.relation_claims:
        valid_refs = [ref for ref in claim.evidence_refs if ref in allowed_refs]
        if len(valid_refs) != len(claim.evidence_refs):
            warnings.append(f"关系线索 {claim.claim_id} 含无效证据引用，已移除。")
        claims.append(
            replace(
                claim,
                evidence_refs=valid_refs,
                review_status=(
                    REVIEW_STATUS_NEEDS_EXPERT_REVIEW
                    if not valid_refs
                    else claim.review_status
                ),
            )
        )
    return StagedCandidateBatch(nodes=nodes, relation_claims=claims), warnings
