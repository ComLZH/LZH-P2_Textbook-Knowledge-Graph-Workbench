from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .contracts import NODE_TYPE_CONCEPT, REVIEW_STATUS_PENDING


EVIDENCE_TYPE_EXPLICIT = "explicit"
EVIDENCE_TYPE_STRUCTURAL = "structural"
EVIDENCE_TYPE_DERIVED = "derived"
EVIDENCE_TYPE_MODEL_INFERRED = "model_inferred"

ALLOWED_EVIDENCE_TYPES = {
    EVIDENCE_TYPE_EXPLICIT,
    EVIDENCE_TYPE_STRUCTURAL,
    EVIDENCE_TYPE_DERIVED,
    EVIDENCE_TYPE_MODEL_INFERRED,
}

INFERENCE_SCOPE_SAME_PAGE = "same_page"
INFERENCE_SCOPE_ADJACENT_PAGES = "adjacent_pages"
INFERENCE_SCOPE_SAME_SECTION = "same_section"
INFERENCE_SCOPE_CROSS_SECTION = "cross_section"
INFERENCE_SCOPE_CROSS_CHAPTER = "cross_chapter"

ALLOWED_INFERENCE_SCOPES = {
    INFERENCE_SCOPE_SAME_PAGE,
    INFERENCE_SCOPE_ADJACENT_PAGES,
    INFERENCE_SCOPE_SAME_SECTION,
    INFERENCE_SCOPE_CROSS_SECTION,
    INFERENCE_SCOPE_CROSS_CHAPTER,
}


@dataclass(slots=True)
class OcrTextBlockDTO:
    block_id: str
    text: str
    bbox: dict[str, float] = field(default_factory=dict)
    polygon: list[list[float]] = field(default_factory=list)
    confidence: float = 0.0
    block_type: str = "paragraph"
    reading_order: int = 0


@dataclass(slots=True)
class FormulaEvidenceDTO:
    formula_id: str
    latex: str = ""
    bbox: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    image_path: str = ""


@dataclass(slots=True)
class PageEvidenceBundleDTO:
    page_id: str
    page_index: int
    source_id: str
    source_path: str
    image_path: str
    image_sha256: str
    printed_page_number: str = ""
    ocr_status: str = "unavailable"
    ocr_model: str = ""
    ocr_blocks: list[OcrTextBlockDTO] = field(default_factory=list)
    formulas: list[FormulaEvidenceDTO] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    visual_regions: list[dict[str, object]] = field(default_factory=list)
    source_text: str = ""
    cache_hit: bool = False
    requires_visual_review: bool = False
    review_reason: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def combined_text(self) -> str:
        texts = [block.text.strip() for block in self.ocr_blocks if block.text.strip()]
        return "\n".join(texts) or self.source_text.strip()

    def to_state(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class PageContinuityResultDTO:
    previous_page_id: str
    next_page_id: str
    previous_page_index: int
    next_page_index: int
    continuity_score: float
    signals: list[str] = field(default_factory=list)
    needs_model_review: bool = False
    model_review_status: str = "not_requested"
    model_reasoning: str = ""
    suggested_action: str = "keep_order"
    user_confirmation: str = "pending"


@dataclass(slots=True)
class LocalNodeCandidateDTO:
    temporary_id: str
    display_name: str
    node_name: str = ""
    node_type: str = NODE_TYPE_CONCEPT
    chapter: str = ""
    section: str = ""
    definition: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reasoning_summary: str = ""


@dataclass(slots=True)
class LocalRelationClaimDTO:
    claim_id: str
    source_mention: str
    target_mention: str
    relation_type_candidate: str
    source_temporary_id: str = ""
    target_temporary_id: str = ""
    evidence_type: str = EVIDENCE_TYPE_EXPLICIT
    inference_scope: str = INFERENCE_SCOPE_SAME_PAGE
    evidence_refs: list[str] = field(default_factory=list)
    evidence_text: str = ""
    confidence: float = 0.0
    reasoning_summary: str = ""
    review_status: str = REVIEW_STATUS_PENDING


@dataclass(slots=True)
class CanonicalNodeDTO:
    node_id: str
    display_name: str
    node_name: str
    node_type: str = NODE_TYPE_CONCEPT
    chapter: str = ""
    section: str = ""
    definition: str = ""
    aliases: list[str] = field(default_factory=list)
    source_temporary_ids: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    occurrences: list[dict[str, object]] = field(default_factory=list)
    confidence: float = 0.0
    review_status: str = REVIEW_STATUS_PENDING


@dataclass(slots=True)
class NodeAliasMappingDTO:
    temporary_id: str
    source_mention: str
    canonical_node_id: str
    mapping_method: str
    confidence: float
    needs_review: bool = False


@dataclass(slots=True)
class RelationCandidateDTO:
    source_node_id: str
    target_node_id: str
    relation_type: str
    evidence_type: str = EVIDENCE_TYPE_EXPLICIT
    inference_scope: str = INFERENCE_SCOPE_SAME_PAGE
    evidence_refs: list[str] = field(default_factory=list)
    evidence_text: str = ""
    confidence: float = 0.0
    reasoning_summary: str = ""
    relation_source: str = "staged_relation_consolidator"
    review_status: str = REVIEW_STATUS_PENDING


@dataclass(slots=True)
class ChapterManifestDTO:
    chapter_id: str
    title: str
    start_page: int
    end_page: int
    section_titles: list[str] = field(default_factory=list)
    status: str = "pending"


@dataclass(slots=True)
class BookManifestDTO:
    book_id: str
    source_path: str
    title: str = ""
    subject: str = ""
    grade: str = ""
    term: str = ""
    page_count: int = 0
    chapters: list[ChapterManifestDTO] = field(default_factory=list)
    non_content_pages: list[int] = field(default_factory=list)
    printed_page_map: dict[str, str] = field(default_factory=dict)
    status: str = "draft"


@dataclass(slots=True)
class StagedChapterExtractionDTO:
    chapter_id: str
    page_bundles: list[PageEvidenceBundleDTO] = field(default_factory=list)
    continuity_results: list[PageContinuityResultDTO] = field(default_factory=list)
    local_nodes: list[LocalNodeCandidateDTO] = field(default_factory=list)
    local_relation_claims: list[LocalRelationClaimDTO] = field(default_factory=list)
    canonical_nodes: list[CanonicalNodeDTO] = field(default_factory=list)
    alias_mappings: list[NodeAliasMappingDTO] = field(default_factory=list)
    relation_candidates: list[RelationCandidateDTO] = field(default_factory=list)


@dataclass(slots=True)
class ExtractionRunManifestDTO:
    run_id: str
    source_path: str
    source_sha256: str
    pipeline_version: str
    ocr_enabled: bool
    page_order_check_enabled: bool
    model_mode: str
    page_count: int = 0
    ocr_cache_hits: int = 0
    image_model_calls: int = 0
    text_model_calls: int = 0
    model_input_characters: int = 0
    status: str = "running"
    notes: list[str] = field(default_factory=list)
