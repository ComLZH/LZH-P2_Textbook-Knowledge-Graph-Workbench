from __future__ import annotations

from ..contracts import DraftKnowledgeItemDTO, EvidenceAnchorDTO, SourceRecordDTO
from ..utils.naming import build_node_id, slugify_display_name
from ..utils.graph_semantics import normalize_node_type, normalize_relation_type
from ..utils.review_status import normalize_review_status


class StructuredTextbookExtractor:
    def extract(self, records: list[SourceRecordDTO]) -> list[DraftKnowledgeItemDTO]:
        drafts: list[DraftKnowledgeItemDTO] = []
        for record in records:
            drafts.extend(self._extract_record(record))
        return drafts

    def _extract_record(self, record: SourceRecordDTO) -> list[DraftKnowledgeItemDTO]:
        chapters = record.raw_structure.get("chapters", [])
        if not isinstance(chapters, list):
            return []
        drafts: list[DraftKnowledgeItemDTO] = []
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            chapter_code = str(chapter.get("chapter", chapter.get("code", ""))).strip()
            chapter_title = str(chapter.get("title", "")).strip()
            parent_node_name = slugify_display_name(chapter_title or chapter_code)
            parent_node_id = build_node_id(
                subject=record.subject,
                grade=record.grade,
                term=record.term,
                chapter=chapter_code,
                node_name=parent_node_name,
            )
            drafts.append(
                DraftKnowledgeItemDTO(
                    draft_id=f"{record.source_id}:{chapter_code}:chapter",
                    subject=record.subject,
                    grade=record.grade,
                    term=record.term,
                    chapter=chapter_code,
                    candidate_display_name=chapter_title or chapter_code,
                    candidate_node_name=parent_node_name,
                    candidate_node_id=parent_node_id,
                    knowledge_type=normalize_node_type("container"),
                    education_stage=record.education_stage,
                    grade_band=record.grade_band,
                    subject_tags=list(record.subject_tags),
                    source_id=record.source_id,
                    source_path=record.source_path,
                    source_format=record.source_format,
                    source_document_type=record.source_document_type,
                    source_text=record.raw_text,
                    source_location=f"{record.source_id}/{chapter_code}",
                    evidence_anchors=[
                        self._build_anchor(
                            record=record,
                            anchor_id=f"{record.source_id}:{chapter_code}:chapter:anchor",
                            target_id=parent_node_id,
                            source_location=f"{record.source_id}/{chapter_code}",
                            anchor_text=record.raw_text,
                            confidence=1.0,
                            created_by="structured_extractor",
                            review_status="accepted",
                        )
                    ],
                    confidence=1.0,
                    review_status="accepted",
                )
            )
            points = chapter.get("knowledge_points", [])
            if isinstance(points, list):
                for index, point in enumerate(points, start=1):
                    drafts.append(
                        self._extract_point(
                            record=record,
                            chapter_code=chapter_code,
                            parent_display_name=chapter_title or chapter_code,
                            parent_node_id=parent_node_id,
                            point=point,
                            index=index,
                        )
                    )
        return drafts

    def _extract_point(
        self,
        *,
        record: SourceRecordDTO,
        chapter_code: str,
        parent_display_name: str,
        parent_node_id: str,
        point: object,
        index: int,
    ) -> DraftKnowledgeItemDTO:
        if isinstance(point, dict):
            display_name = str(point.get("display_name", point.get("name", ""))).strip()
            raw_node_name = str(point.get("node_name", "")).strip()
            prerequisites = self._normalize_string_list(point.get("prerequisites", []))
            relations = self._normalize_relation_candidates(point.get("relations", []))
            raw_type = point.get("node_type", point.get("knowledge_type", "concept"))
            knowledge_type = normalize_node_type(raw_type)
            cognitive_level = str(point.get("cognitive_level", "")).strip()
            subject_tags = self._normalize_string_list(
                point.get("subject_tags", record.subject_tags)
            )
            confidence = float(point.get("confidence", 0.8))
            review_status = normalize_review_status(point.get("review_status", "pending"))
            source_text = str(point.get("source_text", record.raw_text))
            source_location = str(
                point.get("source_location", f"{record.source_id}/{chapter_code}/{index}")
            )
            reasoning_summary = str(point.get("reasoning_summary", "")).strip()
            extractor_source = str(
                point.get("extractor_source", "structured_extractor")
            ).strip() or "structured_extractor"
            evidence_anchors = self._normalize_evidence_anchors(
                point.get("evidence_anchors", []),
                record=record,
                target_id="",
                fallback_anchor_id=f"{record.source_id}:{chapter_code}:{index}:anchor",
                fallback_location=source_location,
                fallback_text=source_text,
                confidence=confidence,
                created_by=extractor_source,
                review_status=review_status,
            )
        else:
            display_name = str(point).strip()
            raw_node_name = ""
            prerequisites = []
            relations = []
            knowledge_type = normalize_node_type("concept")
            cognitive_level = ""
            subject_tags = list(record.subject_tags)
            confidence = 0.7
            review_status = "pending"
            source_text = record.raw_text
            source_location = f"{record.source_id}/{chapter_code}/{index}"
            reasoning_summary = ""
            extractor_source = "structured_extractor"
            evidence_anchors = []
        node_name = raw_node_name or slugify_display_name(display_name)
        node_id = build_node_id(
            subject=record.subject,
            grade=record.grade,
            term=record.term,
            chapter=chapter_code,
            node_name=node_name,
        )
        return DraftKnowledgeItemDTO(
            draft_id=f"{record.source_id}:{chapter_code}:{index}",
            subject=record.subject,
            grade=record.grade,
            term=record.term,
            chapter=chapter_code,
            candidate_display_name=display_name,
            candidate_node_name=node_name,
            candidate_node_id=node_id,
            candidate_parent_name=parent_display_name,
            candidate_parent_node_id=parent_node_id,
            candidate_prerequisites=prerequisites,
            candidate_relations=relations,
            knowledge_type=knowledge_type,
            cognitive_level=cognitive_level,
            education_stage=record.education_stage,
            grade_band=record.grade_band,
            subject_tags=subject_tags,
            source_id=record.source_id,
            source_path=record.source_path,
            source_format=record.source_format,
            source_document_type=record.source_document_type,
            source_text=source_text,
            source_location=source_location,
            evidence_anchors=self._ensure_anchor_targets(evidence_anchors, node_id)
            or [
                self._build_anchor(
                    record=record,
                    anchor_id=f"{record.source_id}:{chapter_code}:{index}:anchor",
                    target_id=node_id,
                    source_location=source_location,
                    anchor_text=source_text,
                    confidence=confidence,
                    created_by=extractor_source,
                    review_status=review_status,
                )
            ],
            confidence=confidence,
            reasoning_summary=reasoning_summary,
            extractor_source=extractor_source,
            review_status=review_status,
        )

    @staticmethod
    def _normalize_string_list(raw_value: object) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, (list, tuple, set)):
            return [str(item).strip() for item in raw_value if str(item).strip()]
        value = str(raw_value).strip()
        return [value] if value else []

    @staticmethod
    def _normalize_relation_candidates(raw_value: object) -> list[dict[str, object]]:
        if not isinstance(raw_value, list):
            return []
        relations: list[dict[str, object]] = []
        for item in raw_value:
            if not isinstance(item, dict):
                continue
            relation_type = normalize_relation_type(item.get("relation_type", ""))
            target_node_id = str(item.get("target_node_id", "")).strip()
            source_node_id = str(item.get("source_node_id", "")).strip()
            if not relation_type or not (source_node_id or target_node_id):
                continue
            relations.append(
                {
                    "source_node_id": source_node_id,
                    "target_node_id": target_node_id,
                    "relation_type": relation_type,
                    "confidence": float(item.get("confidence", 0.8)),
                    "relation_evidence": str(item.get("relation_evidence", "")),
                    "relation_source": str(item.get("relation_source", "")),
                    "reasoning_summary": str(item.get("reasoning_summary", "")),
                    "evidence_anchors": item.get("evidence_anchors", []),
                    "review_status": normalize_review_status(
                        item.get("review_status", "pending")
                    ),
                }
            )
        return relations

    @staticmethod
    def _build_anchor(
        *,
        record: SourceRecordDTO,
        anchor_id: str,
        target_id: str,
        source_location: str,
        anchor_text: str,
        confidence: float,
        created_by: str,
        review_status: str,
    ) -> EvidenceAnchorDTO:
        return EvidenceAnchorDTO(
            anchor_id=anchor_id,
            source_id=record.source_id,
            source_path=record.source_path,
            source_format=record.source_format,
            source_document_type=record.source_document_type,
            source_location=source_location,
            anchor_text=anchor_text,
            target_type="node",
            target_ids=[target_id] if target_id else [],
            confidence=confidence,
            created_by=created_by,
            review_status=review_status,
        )

    def _normalize_evidence_anchors(
        self,
        raw_value: object,
        *,
        record: SourceRecordDTO,
        target_id: str,
        fallback_anchor_id: str,
        fallback_location: str,
        fallback_text: str,
        confidence: float,
        created_by: str,
        review_status: str,
    ) -> list[EvidenceAnchorDTO]:
        if not isinstance(raw_value, list):
            raw_value = []
        anchors: list[EvidenceAnchorDTO] = []
        for index, item in enumerate(raw_value, start=1):
            if not isinstance(item, dict):
                continue
            anchors.append(
                EvidenceAnchorDTO(
                    anchor_id=str(
                        item.get("anchor_id", f"{fallback_anchor_id}:{index}")
                    ),
                    source_id=str(item.get("source_id", record.source_id)),
                    source_path=str(item.get("source_path", record.source_path)),
                    source_format=str(item.get("source_format", record.source_format)),
                    source_document_type=str(
                        item.get(
                            "source_document_type",
                            record.source_document_type,
                        )
                    ),
                    source_location=str(item.get("source_location", fallback_location)),
                    page_index=self._optional_int(item.get("page_index")),
                    image_index=self._optional_int(item.get("image_index")),
                    block_id=str(item.get("block_id", "")),
                    text_span=dict(item.get("text_span", {}))
                    if isinstance(item.get("text_span", {}), dict)
                    else {},
                    bbox=dict(item.get("bbox", {}))
                    if isinstance(item.get("bbox", {}), dict)
                    else {},
                    anchor_text=str(item.get("anchor_text", fallback_text)),
                    target_type=str(item.get("target_type", "node")),
                    target_ids=self._normalize_string_list(
                        item.get("target_ids", [target_id] if target_id else [])
                    ),
                    confidence=float(item.get("confidence", confidence)),
                    created_by=str(item.get("created_by", created_by)),
                    review_status=normalize_review_status(
                        item.get("review_status", review_status),
                        default=review_status,
                    ),
                )
            )
        return anchors

    @staticmethod
    def _ensure_anchor_targets(
        anchors: list[EvidenceAnchorDTO],
        target_id: str,
    ) -> list[EvidenceAnchorDTO]:
        for anchor in anchors:
            if not anchor.target_ids:
                anchor.target_ids.append(target_id)
        return anchors

    @staticmethod
    def _optional_int(raw_value: object) -> int | None:
        if raw_value in (None, ""):
            return None
        return int(raw_value)
