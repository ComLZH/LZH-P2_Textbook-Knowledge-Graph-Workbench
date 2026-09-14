from __future__ import annotations

import json
import sys
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import SourceRecordDTO
from textbook_builder.normalizers.node_canonicalizer import NodeCanonicalizer
from textbook_builder.pipeline_contracts import (
    LocalNodeCandidateDTO,
    LocalRelationClaimDTO,
    PageEvidenceBundleDTO,
)
from textbook_builder.services.page_continuity import PageContinuityChecker
from textbook_builder.services.book_manifest import BookManifestBuilder
from textbook_builder.services.page_evidence import PageEvidenceCache, PaddleOcrPageAnalyzer
from textbook_builder.services.relation_consolidation import RelationClaimConsolidator
from textbook_builder.services.staged_extraction import StagedTextbookExtractionService


def test_page_evidence_ocr_cache_reuses_page_without_second_prediction() -> None:
    temp_dir = _temp_dir("ocr_cache")
    try:
        image_path = temp_dir / "page.bin"
        image_path.write_bytes(b"stable-page-content")
        det_dir = temp_dir / "det"
        rec_dir = temp_dir / "rec"
        det_dir.mkdir()
        rec_dir.mkdir()
        predictor = _FakePredictor()
        analyzer = PaddleOcrPageAnalyzer(
            cache=PageEvidenceCache(temp_dir / "cache"),
            shared_model_root=temp_dir,
            predictor_factory=lambda _det, _rec: predictor,
        )
        import os

        previous_det = os.environ.get("TEXTBOOK_BUILDER_OCR_DET_MODEL_DIR")
        previous_rec = os.environ.get("TEXTBOOK_BUILDER_OCR_REC_MODEL_DIR")
        os.environ["TEXTBOOK_BUILDER_OCR_DET_MODEL_DIR"] = str(det_dir)
        os.environ["TEXTBOOK_BUILDER_OCR_REC_MODEL_DIR"] = str(rec_dir)
        try:
            first = analyzer.analyze(
                image_path,
                source_id="BOOK",
                page_index=3,
                source_path=image_path,
            )
            second = analyzer.analyze(
                image_path,
                source_id="BOOK",
                page_index=3,
                source_path=image_path,
            )
        finally:
            _restore_env("TEXTBOOK_BUILDER_OCR_DET_MODEL_DIR", previous_det)
            _restore_env("TEXTBOOK_BUILDER_OCR_REC_MODEL_DIR", previous_rec)

        assert first.source_text == "一次函数\n正比例函数"
        assert first.cache_hit is False
        assert second.cache_hit is True
        assert predictor.call_count == 1
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_page_continuity_flags_duplicate_page_but_does_not_reorder() -> None:
    pages = [
        _page(1, "一次函数的定义与表示方法", sha="same"),
        _page(2, "一次函数的定义与表示方法", sha="same"),
    ]
    result = PageContinuityChecker().check(pages)[0]

    assert result.previous_page_index == 1
    assert result.next_page_index == 2
    assert result.needs_model_review is True
    assert result.suggested_action == "manual_review"
    assert "duplicate_page_image" in result.signals


def test_book_manifest_detects_chapter_boundaries_for_full_book_batches() -> None:
    manifest = BookManifestBuilder().build(
        [
            _page(4, "目录\n第十三章 三角形 1", sha="toc"),
            _page(7, "第十三章 三角形\n13.1 三角形的概念", sha="p7"),
            _page(34, "第十四章 全等三角形\n14.1 全等三角形及其性质", sha="p34"),
        ],
        book_id="BOOK",
        source_path="book.pdf",
        subject="math",
        grade="g8",
        term="term1",
    )

    assert len(manifest.chapters) == 2
    assert manifest.chapters[0].start_page == 7
    assert manifest.chapters[0].end_page == 33
    assert manifest.chapters[1].start_page == 34


def test_node_canonicalizer_merges_exact_mentions_and_preserves_alias_mapping() -> None:
    nodes, mappings = NodeCanonicalizer().canonicalize(
        [
            LocalNodeCandidateDTO(
                temporary_id="n1",
                display_name="一次函数",
                node_type="concept",
                chapter="第十四章",
                evidence_refs=["BOOK:page:1#ocr-1"],
                confidence=0.9,
            ),
            LocalNodeCandidateDTO(
                temporary_id="n2",
                display_name=" 一次函数 ",
                node_type="concept",
                chapter="第十四章",
                aliases=["线性函数"],
                evidence_refs=["BOOK:page:2#ocr-2"],
                confidence=0.8,
            ),
        ],
        subject="math",
        grade="g8",
        term="term1",
    )

    assert len(nodes) == 1
    assert nodes[0].source_temporary_ids == ["n1", "n2"]
    assert "线性函数" in nodes[0].aliases
    assert {item.temporary_id for item in mappings} == {"n1", "n2"}


def test_relation_consolidator_maps_endpoints_and_rejects_self_loop() -> None:
    nodes, mappings = NodeCanonicalizer().canonicalize(
        [
            LocalNodeCandidateDTO("n1", "一次函数", chapter="ch1", confidence=0.9),
            LocalNodeCandidateDTO("n2", "正比例函数", chapter="ch1", confidence=0.9),
        ],
        subject="math",
        grade="g8",
        term="term1",
    )
    result = RelationClaimConsolidator().consolidate(
        [
            LocalRelationClaimDTO(
                claim_id="r1",
                source_mention="一次函数",
                target_mention="正比例函数",
                source_temporary_id="n1",
                target_temporary_id="n2",
                relation_type_candidate="contains",
                evidence_refs=["BOOK:page:1#ocr-2"],
                confidence=0.8,
            ),
            LocalRelationClaimDTO(
                claim_id="r2",
                source_mention="一次函数",
                target_mention="一次函数",
                source_temporary_id="n1",
                target_temporary_id="n1",
                relation_type_candidate="prerequisite",
                confidence=0.7,
            ),
        ],
        canonical_nodes=nodes,
        alias_mappings=mappings,
    )

    assert len(result.relations) == 1
    assert result.relations[0].relation_type == "contains"
    assert len(result.unresolved_claims) == 1
    assert "自环" in result.warnings[-1]


def test_staged_service_runs_first_pass_then_text_only_relation_completion() -> None:
    client = _FakeStagedClient()
    record = SourceRecordDTO(
        source_id="BOOK",
        source_type="textbook",
        source_path="book.pdf",
        subject="math",
        grade="g8",
        term="term1",
        source_format="pdf",
    )
    outcome = StagedTextbookExtractionService(client).extract(
        record=record,
        pages=[
            _page(1, "一次函数的一般形式是y=kx+b。正比例函数是b=0的特殊情况。", sha="p1"),
            _page(2, "正比例函数的图象经过原点。", sha="p2"),
        ],
    )

    assert len(outcome.canonical_nodes) == 2
    assert len(outcome.relation_candidates) == 1
    assert len(outcome.drafts) == 2
    assert outcome.text_model_calls == 2
    assert outcome.image_model_calls == 0
    assert outcome.model_input_characters > 0
    assert client.call_count == 2
    assert outcome.drafts[0].extractor_source == "staged_ocr_llm_v1"
    assert any("无效证据引用" in warning for warning in outcome.warnings)
    assert outcome.relation_candidates[0].evidence_refs == ["BOOK:page:1"]
    assert outcome.relation_candidates[0].relation_source == "llm_relation_completion"
    assert outcome.relation_completion_diagnostics == [
        {
            "chapter": "第十四章",
            "node_count": 2,
            "page_indices": [1],
            "status": "completed",
            "raw_relation_count": 1,
            "seed_relation_count": 1,
            "invalid_item_count": 0,
            "invalid_endpoint_or_type_count": 0,
            "filtered_evidence_ref_count": 1,
            "raw_relation_types": {"contains": 1},
            "final_relation_count": 1,
            "final_relation_types": {"contains": 1},
        }
    ]


class _FakePredictor:
    def __init__(self) -> None:
        self.call_count = 0

    def predict(self, _path: str):
        self.call_count += 1
        return [
            {
                "res": {
                    "rec_texts": ["一次函数", "正比例函数"],
                    "rec_scores": [0.98, 0.96],
                    "dt_polys": [
                        [[1, 2], [20, 2], [20, 12], [1, 12]],
                        [[1, 20], [24, 20], [24, 30], [1, 30]],
                    ],
                }
            }
        ]


class _FakeStagedClient:
    def __init__(self) -> None:
        self.call_count = 0

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        payload = json.loads(user_prompt)
        if payload["task"] == "extract_independent_nodes_and_local_relation_claims":
            return json.dumps(
                {
                    "nodes": [
                        {
                            "temporary_id": "n1",
                            "display_name": "一次函数",
                            "chapter": "第十四章",
                            "evidence_refs": ["BOOK:page:1", "BOOK:page:999#invented"],
                            "confidence": 0.9,
                        },
                        {
                            "temporary_id": "n2",
                            "display_name": "正比例函数",
                            "chapter": "第十四章",
                            "evidence_refs": ["BOOK:page:1"],
                            "confidence": 0.9,
                        },
                    ],
                    "local_relation_claims": [
                        {
                            "claim_id": "r1",
                            "source_temporary_id": "n1",
                            "target_temporary_id": "n2",
                            "source_mention": "一次函数",
                            "target_mention": "正比例函数",
                            "relation_type_candidate": "contains",
                            "evidence_refs": ["BOOK:page:1"],
                            "evidence_text": "正比例函数是特殊情况",
                            "confidence": 0.85,
                        }
                    ],
                },
                ensure_ascii=False,
            )
        node_ids = [item["node_id"] for item in payload["canonical_nodes"]]
        return json.dumps(
            {
                "relations": [
                    {
                        "source_node_id": node_ids[0],
                        "target_node_id": node_ids[1],
                        "relation_type": "contains",
                        "evidence_type": "explicit",
                        "inference_scope": "same_page",
                        "evidence_refs": ["BOOK:page:1", "BOOK:page:999#invented"],
                        "evidence_text": "正比例函数是一次函数的特殊情况",
                        "confidence": 0.9,
                        "review_status": "pending",
                    }
                ]
            },
            ensure_ascii=False,
        )


def _page(index: int, text: str, *, sha: str) -> PageEvidenceBundleDTO:
    return PageEvidenceBundleDTO(
        page_id=f"BOOK:page:{index}",
        page_index=index,
        source_id="BOOK",
        source_path="book.pdf",
        image_path=f"page-{index}.png",
        image_sha256=sha,
        source_text=text,
        ocr_status="ok",
        requires_visual_review=False,
    )


def _temp_dir(label: str) -> Path:
    path = PROJECT_ROOT / "storage" / "test_runs" / f"staged_{label}_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _restore_env(name: str, value: str | None) -> None:
    import os

    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
