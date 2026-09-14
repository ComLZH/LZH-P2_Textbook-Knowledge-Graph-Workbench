from __future__ import annotations

from ..contracts import (
    ALLOWED_NODE_TYPES,
    ALLOWED_RELATION_TYPES,
    NODE_TYPE_APPLICATION,
    NODE_TYPE_CONCEPT,
    NODE_TYPE_CONTAINER,
    NODE_TYPE_METHOD,
    NODE_TYPE_PROBLEM_TYPE,
    NODE_TYPE_PROPERTY,
    NODE_TYPE_REPRESENTATION,
    NODE_TYPE_RULE,
    RELATION_TYPE_APPLIES_TO,
    RELATION_TYPE_CONTAINS,
    RELATION_TYPE_CONTRAST,
    RELATION_TYPE_DERIVES_TO,
    RELATION_TYPE_EQUIVALENT,
    RELATION_TYPE_EXPLAINS,
    RELATION_TYPE_PARALLEL,
    RELATION_TYPE_PREREQUISITE,
    RELATION_TYPE_PROGRESSIVE,
    RELATION_TYPE_REPRESENTED_BY,
)


LEGACY_NODE_TYPE_MAP = {
    "chapter": NODE_TYPE_CONTAINER,
    "section": NODE_TYPE_CONTAINER,
    "unit": NODE_TYPE_CONTAINER,
    "topic": NODE_TYPE_CONTAINER,
    "knowledge_point": NODE_TYPE_CONCEPT,
    "knowledge_node": NODE_TYPE_CONCEPT,
    "principle": NODE_TYPE_PROPERTY,
    "property": NODE_TYPE_PROPERTY,
    "conclusion": NODE_TYPE_PROPERTY,
    "theorem": NODE_TYPE_RULE,
    "criterion": NODE_TYPE_RULE,
    "law": NODE_TYPE_RULE,
    "procedure": NODE_TYPE_METHOD,
    "skill": NODE_TYPE_METHOD,
    "strategy": NODE_TYPE_METHOD,
    "representation": NODE_TYPE_REPRESENTATION,
    "model": NODE_TYPE_REPRESENTATION,
    "problem": NODE_TYPE_PROBLEM_TYPE,
    "problem_type": NODE_TYPE_PROBLEM_TYPE,
    "task": NODE_TYPE_PROBLEM_TYPE,
    "application": NODE_TYPE_APPLICATION,
    "example": NODE_TYPE_APPLICATION,
    "scenario": NODE_TYPE_APPLICATION,
    "common_error": NODE_TYPE_APPLICATION,
    "error": NODE_TYPE_APPLICATION,
    "other": NODE_TYPE_CONCEPT,
}

LEGACY_RELATION_TYPE_MAP = {
    "contains": RELATION_TYPE_CONTAINS,
    "prerequisite": RELATION_TYPE_PREREQUISITE,
    "sequence": RELATION_TYPE_PROGRESSIVE,
    "progressive": RELATION_TYPE_PROGRESSIVE,
    "derives_to": RELATION_TYPE_DERIVES_TO,
    "derive": RELATION_TYPE_DERIVES_TO,
    "derived_from": RELATION_TYPE_DERIVES_TO,
    "explains": RELATION_TYPE_EXPLAINS,
    "supports": RELATION_TYPE_EXPLAINS,
    "support": RELATION_TYPE_EXPLAINS,
    "equivalent": RELATION_TYPE_EQUIVALENT,
    "parallel": RELATION_TYPE_PARALLEL,
    "contrast": RELATION_TYPE_CONTRAST,
    "application": RELATION_TYPE_APPLIES_TO,
    "applies_to": RELATION_TYPE_APPLIES_TO,
    "represented_by": RELATION_TYPE_REPRESENTED_BY,
    "representation": RELATION_TYPE_REPRESENTED_BY,
    "related": RELATION_TYPE_EXPLAINS,
}

PRIMARY_RELATION_PRIORITY = {
    RELATION_TYPE_CONTAINS: 100,
    RELATION_TYPE_PREREQUISITE: 90,
    RELATION_TYPE_PROGRESSIVE: 80,
    RELATION_TYPE_DERIVES_TO: 70,
    RELATION_TYPE_CONTRAST: 60,
    RELATION_TYPE_PARALLEL: 50,
    RELATION_TYPE_EQUIVALENT: 40,
    RELATION_TYPE_APPLIES_TO: 30,
    RELATION_TYPE_REPRESENTED_BY: 20,
    RELATION_TYPE_EXPLAINS: 10,
}

MAIN_BACKBONE_RELATIONS = {
    RELATION_TYPE_PREREQUISITE,
    RELATION_TYPE_PROGRESSIVE,
    RELATION_TYPE_DERIVES_TO,
}

AUXILIARY_NODE_TYPES = {
    NODE_TYPE_REPRESENTATION,
    NODE_TYPE_PROBLEM_TYPE,
    NODE_TYPE_APPLICATION,
}

MAIN_KNOWLEDGE_NODE_TYPES = {
    NODE_TYPE_CONCEPT,
    NODE_TYPE_PROPERTY,
    NODE_TYPE_RULE,
    NODE_TYPE_METHOD,
}


def normalize_node_type(raw_value: object, default: str = NODE_TYPE_CONCEPT) -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        return default
    if value in ALLOWED_NODE_TYPES:
        return value
    return LEGACY_NODE_TYPE_MAP.get(value, default)


def normalize_relation_type(raw_value: object, default: str = "") -> str:
    value = str(raw_value or "").strip().lower()
    if not value:
        return default
    if value in ALLOWED_RELATION_TYPES:
        return value
    return LEGACY_RELATION_TYPE_MAP.get(value, default)


def is_container_node_type(raw_value: object) -> bool:
    return normalize_node_type(raw_value, default="") == NODE_TYPE_CONTAINER


def is_auxiliary_node_type(raw_value: object) -> bool:
    return normalize_node_type(raw_value, default="") in AUXILIARY_NODE_TYPES


def is_main_knowledge_node_type(raw_value: object) -> bool:
    return normalize_node_type(raw_value, default="") in MAIN_KNOWLEDGE_NODE_TYPES


def relation_priority(raw_value: object) -> int:
    relation_type = normalize_relation_type(raw_value, default="")
    return PRIMARY_RELATION_PRIORITY.get(relation_type, 0)
