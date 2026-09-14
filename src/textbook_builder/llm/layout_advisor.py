from __future__ import annotations

import json

from ..contracts import FormalGraphWorkbookDTO, SourceRecordDTO
from ..utils.chapter_layout import ChapterLayoutPlan, should_request_model_advice
from ..utils.graph_semantics import normalize_relation_type
from .clients import ChatJsonClient


class LlmChapterStructureAdvisor:
    def __init__(self, client: ChatJsonClient) -> None:
        self._client = client

    def advise(
        self,
        *,
        record: SourceRecordDTO,
        workbook: FormalGraphWorkbookDTO,
        local_plans: dict[str, ChapterLayoutPlan],
    ) -> dict[str, dict[str, object]]:
        target_plans = {
            container_id: plan
            for container_id, plan in local_plans.items()
            if should_request_model_advice(plan)
        }
        if not target_plans:
            return {}
        raw_payload = self._client.complete_json(
            system_prompt=self._system_prompt(),
            user_prompt=self._user_prompt(
                record=record,
                workbook=workbook,
                local_plans=target_plans,
            ),
        )
        return self._parse_payload(raw_payload, allowed_container_ids=set(target_plans))

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是教材知识图谱章节内部结构顾问。"
            "你不能扩展新的节点类型或关系类型，也不能新增教材中不存在的知识点。"
            "你的任务是在给定节点、关系和本地判断结果的基础上，对章节内部布局提出结构化建议。"
            "你必须优先依据 prerequisite、progressive、derives_to 判断主路径。"
            "representation、problem_type、application 默认应作为侧挂节点。"
            "你只能输出 JSON 对象。"
        )

    @staticmethod
    def _user_prompt(
        *,
        record: SourceRecordDTO,
        workbook: FormalGraphWorkbookDTO,
        local_plans: dict[str, ChapterLayoutPlan],
    ) -> str:
        node_by_id = {node.node_id: node for node in workbook.nodes}
        chapters: list[dict[str, object]] = []
        for container_id, plan in local_plans.items():
            child_ids = [
                node.node_id for node in workbook.nodes if node.parent_node_id == container_id
            ]
            relevant_edges = [
                {
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "relation_type": normalize_relation_type(edge.relation_type),
                    "confidence": edge.confidence,
                    "relation_evidence": edge.relation_evidence,
                }
                for edge in workbook.edges
                if edge.source_node_id in child_ids and edge.target_node_id in child_ids
            ]
            chapters.append(
                {
                    "container_node_id": container_id,
                    "container_name": node_by_id[container_id].display_name if container_id in node_by_id else container_id,
                    "nodes": [
                        {
                            "node_id": child_id,
                            "display_name": node_by_id[child_id].display_name,
                            "node_type": node_by_id[child_id].node_type or node_by_id[child_id].knowledge_type,
                            "chapter": node_by_id[child_id].chapter,
                        }
                        for child_id in child_ids
                        if child_id in node_by_id
                    ],
                    "edges": relevant_edges,
                    "local_plan": plan.as_dict(),
                }
            )
        return json.dumps(
            {
                "task": "advise_textbook_chapter_internal_layout",
                "guidance": {
                    "backbone_relations": [
                        "prerequisite",
                        "progressive",
                        "derives_to",
                    ],
                    "auxiliary_node_types": [
                        "representation",
                        "problem_type",
                        "application",
                    ],
                    "allowed_layout_modes": [
                        "single_path",
                        "main_path_with_branches",
                        "multi_core_clusters",
                    ],
                },
                "output_schema": {
                    "chapter_layout_recommendations": [
                        {
                            "container_node_id": "string",
                            "recommended_layout_mode": "single_path|main_path_with_branches|multi_core_clusters",
                            "main_path": ["node_id"],
                            "branch_groups": {"parent_node_id": ["child_node_id"]},
                            "auxiliary_attachments": {"parent_node_id": ["aux_node_id"]},
                            "weak_edges": [
                                {
                                    "source_node_id": "string",
                                    "target_node_id": "string",
                                    "relation_type": "parallel|contrast|explains|applies_to|represented_by|equivalent",
                                }
                            ],
                            "confidence": 0.0,
                            "reasoning_summary": "string",
                        }
                    ]
                },
                "source_context": {
                    "source_id": record.source_id,
                    "source_format": record.source_format,
                    "subject": record.subject,
                    "grade": record.grade,
                    "term": record.term,
                },
                "chapters": chapters,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _parse_payload(
        raw_payload: str | dict[str, object],
        *,
        allowed_container_ids: set[str],
    ) -> dict[str, dict[str, object]]:
        payload = _load_json_payload(raw_payload)
        recommendations = payload.get("chapter_layout_recommendations", [])
        if not isinstance(recommendations, list):
            return {}
        normalized: dict[str, dict[str, object]] = {}
        for item in recommendations:
            if not isinstance(item, dict):
                continue
            container_id = str(item.get("container_node_id") or "").strip()
            if container_id and container_id in allowed_container_ids:
                normalized[container_id] = item
        return normalized


def _load_json_payload(raw_payload: str | dict[str, object]) -> dict[str, object]:
    if isinstance(raw_payload, dict):
        return raw_payload
    text = str(raw_payload or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            return {}
        loaded = json.loads(text[start : end + 1])
        return loaded if isinstance(loaded, dict) else {}
