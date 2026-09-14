from __future__ import annotations

import json
from dataclasses import replace

from ..contracts import DraftKnowledgeItemDTO, SourceRecordDTO
from ..extractors import StructuredTextbookExtractor
from ..utils.graph_semantics import normalize_node_type, normalize_relation_type
from ..utils.relation_endpoints import normalize_draft_relation_endpoints
from .clients import ChatJsonClient


class LlmCandidatePayloadParser:
    def __init__(self, extractor: StructuredTextbookExtractor | None = None) -> None:
        self._extractor = extractor or StructuredTextbookExtractor()

    def parse(
        self,
        raw_payload: str | dict[str, object] | list[object],
        *,
        record: SourceRecordDTO,
    ) -> list[DraftKnowledgeItemDTO]:
        payload = self._load_payload(raw_payload)
        chapters = self._normalize_chapters(payload)
        llm_record = replace(
            record,
            raw_structure={"chapters": chapters},
            raw_text=record.raw_text,
            source_metadata={
                **record.source_metadata,
                "llm_payload_schema": "textbook_builder_candidates_v1",
            },
        )
        drafts = [
            replace(
                draft,
                extractor_source=draft.extractor_source or "llm_assisted_extractor",
            )
            for draft in self._extractor.extract([llm_record])
        ]
        normalize_draft_relation_endpoints(drafts)
        return drafts

    def _normalize_chapters(self, payload: object) -> list[dict[str, object]]:
        if isinstance(payload, list):
            return [
                {
                    "chapter": "llm_candidates",
                    "title": "LLM Candidates",
                    "knowledge_points": [self._normalize_point(item) for item in payload],
                }
            ]
        if not isinstance(payload, dict):
            raise ValueError("LLM payload must be a JSON object or array.")

        raw_chapters = payload.get("chapters")
        if isinstance(raw_chapters, list):
            chapters: list[dict[str, object]] = []
            for index, chapter in enumerate(raw_chapters, start=1):
                if not isinstance(chapter, dict):
                    continue
                points = (
                    chapter.get("knowledge_points")
                    or chapter.get("candidates")
                    or chapter.get("nodes")
                    or []
                )
                chapters.append(
                    {
                        "chapter": str(
                            chapter.get("chapter")
                            or chapter.get("chapter_code")
                            or f"llm_chapter_{index}"
                        ),
                        "title": str(chapter.get("title") or chapter.get("chapter_title") or ""),
                        "knowledge_points": [
                            self._normalize_point(item)
                            for item in points
                            if isinstance(item, dict)
                        ],
                    }
                )
            return chapters

        points = payload.get("knowledge_points") or payload.get("candidates") or payload.get("nodes")
        if isinstance(points, list):
            return [
                {
                    "chapter": str(payload.get("chapter") or "llm_candidates"),
                    "title": str(payload.get("title") or "LLM Candidates"),
                    "knowledge_points": [
                        self._normalize_point(item)
                        for item in points
                        if isinstance(item, dict)
                    ],
                }
            ]
        raise ValueError("LLM payload does not contain candidates or chapters.")

    @staticmethod
    def _normalize_point(item: object) -> dict[str, object]:
        if not isinstance(item, dict):
            return {"display_name": str(item)}
        relations = item.get("candidate_relations") or item.get("relations") or []
        normalized_relations: list[dict[str, object]] = []
        if isinstance(relations, list):
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                relation_type = normalize_relation_type(relation.get("relation_type", ""))
                if not relation_type:
                    continue
                normalized = dict(relation)
                normalized["relation_type"] = relation_type
                normalized_relations.append(normalized)
        node_type = normalize_node_type(
            item.get("node_type", item.get("knowledge_type", "concept"))
        )
        return {
            "display_name": item.get("candidate_display_name")
            or item.get("display_name")
            or item.get("name")
            or "",
            "node_name": item.get("candidate_node_name") or item.get("node_name") or "",
            "node_type": node_type,
            "knowledge_type": node_type,
            "cognitive_level": item.get("cognitive_level", ""),
            "relations": normalized_relations,
            "source_text": item.get("source_text", ""),
            "source_location": item.get("source_location", ""),
            "evidence_anchors": item.get("evidence_anchors", []),
            "confidence": item.get("confidence", 0.7),
            "reasoning_summary": item.get("reasoning_summary", ""),
            "extractor_source": item.get("extractor_source", "llm_assisted_extractor"),
            "review_status": item.get("review_status", "pending"),
            "review_notes": item.get("review_notes", ""),
        }

    @staticmethod
    def _load_payload(raw_payload: str | dict[str, object] | list[object]) -> object:
        if isinstance(raw_payload, (dict, list)):
            return raw_payload
        text = raw_payload.strip()
        if not text:
            raise ValueError("LLM payload is empty.")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            starts = [index for index in [text.find("{"), text.find("[")] if index >= 0]
            if not starts:
                raise
            start = min(starts)
            end = max(text.rfind("}"), text.rfind("]"))
            if start < 0 or end < start:
                raise
            return json.loads(text[start : end + 1])


class LlmAssistedExtractor:
    def __init__(
        self,
        client: ChatJsonClient,
        parser: LlmCandidatePayloadParser | None = None,
    ) -> None:
        self._client = client
        self._parser = parser or LlmCandidatePayloadParser()

    def extract(self, records: list[SourceRecordDTO]) -> list[DraftKnowledgeItemDTO]:
        drafts: list[DraftKnowledgeItemDTO] = []
        for record in records:
            payload = self._client.complete_json(
                system_prompt=self._system_prompt(),
                user_prompt=self._user_prompt(record),
            )
            drafts.extend(self._parser.parse(payload, record=record))
        return drafts

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是 K-12 教材知识图谱构建助手。"
            "你只能输出 JSON 对象，不要输出解释性自然语言。"
            "你的任务是根据教材原文、页面摘要或教师指定范围，生成待教师审查的知识点与关系候选。"
            "所有候选必须保留教材证据，review_status 默认必须为 pending。"
        )

    @staticmethod
    def _user_prompt(record: SourceRecordDTO) -> str:
        return json.dumps(
            {
                "task": "extract_textbook_knowledge_graph_candidates",
                "output_schema": {
                    "chapters": [
                        {
                            "chapter": "string",
                            "title": "string",
                            "knowledge_points": [
                                {
                                    "candidate_display_name": "string",
                                    "candidate_node_name": "string",
                                    "node_type": "container|concept|property|rule|method|representation|problem_type|application",
                                    "knowledge_type": "container|concept|property|rule|method|representation|problem_type|application",
                                    "cognitive_level": "string",
                                    "candidate_relations": [
                                        {
                                            "source_node_id": "string optional",
                                            "target_node_id": "string optional",
                                            "relation_type": "contains|prerequisite|progressive|derives_to|explains|equivalent|parallel|contrast|applies_to|represented_by",
                                            "confidence": 0.0,
                                            "relation_evidence": "string",
                                            "reasoning_summary": "string",
                                            "review_status": "pending",
                                        }
                                    ],
                                    "source_text": "string",
                                    "source_location": "string",
                                    "confidence": 0.0,
                                    "reasoning_summary": "string",
                                    "review_status": "pending",
                                }
                            ],
                        }
                    ]
                },
                "source": {
                    "source_id": record.source_id,
                    "source_type": record.source_type,
                    "source_format": record.source_format,
                    "source_document_type": record.source_document_type,
                    "subject": record.subject,
                    "grade": record.grade,
                    "term": record.term,
                    "raw_text": record.raw_text,
                    "raw_structure": record.raw_structure,
                    "source_metadata": record.source_metadata,
                },
            },
            ensure_ascii=False,
        )
