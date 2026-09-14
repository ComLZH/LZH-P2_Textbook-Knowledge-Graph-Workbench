from __future__ import annotations

from dataclasses import dataclass, field


NODE_TYPE_CONTAINER = "container"
NODE_TYPE_CONCEPT = "concept"
NODE_TYPE_PROPERTY = "property"
NODE_TYPE_RULE = "rule"
NODE_TYPE_METHOD = "method"
NODE_TYPE_REPRESENTATION = "representation"
NODE_TYPE_PROBLEM_TYPE = "problem_type"
NODE_TYPE_APPLICATION = "application"

ALLOWED_NODE_TYPES = {
    NODE_TYPE_CONTAINER,
    NODE_TYPE_CONCEPT,
    NODE_TYPE_PROPERTY,
    NODE_TYPE_RULE,
    NODE_TYPE_METHOD,
    NODE_TYPE_REPRESENTATION,
    NODE_TYPE_PROBLEM_TYPE,
    NODE_TYPE_APPLICATION,
}

RELATION_TYPE_CONTAINS = "contains"
RELATION_TYPE_PREREQUISITE = "prerequisite"
RELATION_TYPE_PROGRESSIVE = "progressive"
RELATION_TYPE_DERIVES_TO = "derives_to"
RELATION_TYPE_EXPLAINS = "explains"
RELATION_TYPE_EQUIVALENT = "equivalent"
RELATION_TYPE_PARALLEL = "parallel"
RELATION_TYPE_CONTRAST = "contrast"
RELATION_TYPE_APPLIES_TO = "applies_to"
RELATION_TYPE_REPRESENTED_BY = "represented_by"

ALLOWED_RELATION_TYPES = {
    RELATION_TYPE_CONTAINS,
    RELATION_TYPE_PREREQUISITE,
    RELATION_TYPE_PROGRESSIVE,
    RELATION_TYPE_DERIVES_TO,
    RELATION_TYPE_EXPLAINS,
    RELATION_TYPE_EQUIVALENT,
    RELATION_TYPE_PARALLEL,
    RELATION_TYPE_CONTRAST,
    RELATION_TYPE_APPLIES_TO,
    RELATION_TYPE_REPRESENTED_BY,
}


REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_ACCEPTED = "accepted"
REVIEW_STATUS_REJECTED = "rejected"
REVIEW_STATUS_NEEDS_REVISION = "needs_revision"
REVIEW_STATUS_NEEDS_EXPERT_REVIEW = "needs_expert_review"

ALLOWED_REVIEW_STATUSES = {
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_NEEDS_REVISION,
    REVIEW_STATUS_NEEDS_EXPERT_REVIEW,
}


@dataclass(slots=True)
class EvidenceAnchorDTO:
    anchor_id: str = ""
    source_id: str = ""
    source_path: str = ""
    source_format: str = ""
    source_document_type: str = ""
    source_location: str = ""
    page_index: int | None = None
    image_index: int | None = None
    block_id: str = ""
    text_span: dict[str, int] = field(default_factory=dict)
    bbox: dict[str, float] = field(default_factory=dict)
    anchor_text: str = ""
    target_type: str = "node"
    target_ids: list[str] = field(default_factory=list)
    confidence: float = 1.0
    created_by: str = "manual_or_structured_input"
    review_status: str = REVIEW_STATUS_PENDING


@dataclass(slots=True)
class SourceRecordDTO:
    source_id: str
    source_type: str
    source_path: str
    subject: str
    grade: str
    term: str
    raw_text: str = ""
    raw_structure: dict[str, object] = field(default_factory=dict)
    source_format: str = "structured_json"
    source_document_type: str = "textbook"
    education_stage: str = ""
    grade_band: str = ""
    subject_tags: list[str] = field(default_factory=list)
    source_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class DraftKnowledgeItemDTO:
    draft_id: str
    subject: str
    grade: str
    term: str
    chapter: str
    candidate_display_name: str
    candidate_node_name: str
    candidate_node_id: str
    section: str = ""
    candidate_parent_name: str = ""
    candidate_parent_node_id: str = ""
    candidate_prerequisites: list[str] = field(default_factory=list)
    candidate_relations: list[dict[str, object]] = field(default_factory=list)
    knowledge_type: str = NODE_TYPE_CONCEPT
    cognitive_level: str = ""
    education_stage: str = ""
    grade_band: str = ""
    subject_tags: list[str] = field(default_factory=list)
    source_id: str = ""
    source_path: str = ""
    source_format: str = ""
    source_document_type: str = ""
    source_text: str = ""
    source_location: str = ""
    evidence_anchors: list[EvidenceAnchorDTO] = field(default_factory=list)
    confidence: float = 1.0
    reasoning_summary: str = ""
    extractor_source: str = "structured_extractor"
    review_status: str = REVIEW_STATUS_PENDING
    review_notes: str = ""


@dataclass(slots=True)
class FormalGraphMetadataDTO:
    graph_id: str
    subject: str
    grade_scope: list[str]
    term_scope: list[str]
    version: str = "v1"


@dataclass(slots=True)
class FormalNodeDTO:
    node_id: str
    subject: str
    grade: str
    term: str
    chapter: str
    display_name: str
    node_name: str
    parent_node_id: str = ""
    prerequisite_nodes: list[str] = field(default_factory=list)
    node_type: str = NODE_TYPE_CONCEPT
    knowledge_type: str = NODE_TYPE_CONCEPT
    cognitive_level: str = ""
    education_stage: str = ""
    grade_band: str = ""
    subject_tags: list[str] = field(default_factory=list)
    textbook_version: str = "default_mvp_v1"
    chapter_aliases: list[str] = field(default_factory=list)
    source_locations: list[str] = field(default_factory=list)
    extraction_tags: list[str] = field(default_factory=list)
    version: str = "v1"


@dataclass(slots=True)
class FormalEdgeDTO:
    source_node_id: str
    target_node_id: str
    relation_type: str
    confidence: float = 1.0
    relation_evidence: str = ""
    relation_source: str = ""
    review_status: str = REVIEW_STATUS_ACCEPTED


@dataclass(slots=True)
class FormalGraphWorkbookDTO:
    metadata: FormalGraphMetadataDTO
    nodes: list[FormalNodeDTO]
    edges: list[FormalEdgeDTO] = field(default_factory=list)
