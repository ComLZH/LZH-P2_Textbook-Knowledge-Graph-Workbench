from __future__ import annotations

import json
import sys
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
P4_ROOT = PROJECT_ROOT.parent / "P4_知识图谱与自适应引擎项目"
if not P4_ROOT.exists() and len(PROJECT_ROOT.parents) > 1:
    candidate = PROJECT_ROOT.parents[1] / "P4_知识图谱与自适应引擎项目"
    if candidate.exists():
        P4_ROOT = candidate

SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))
if P4_ROOT.exists():
    sys.path.insert(0, str(P4_ROOT))

from p2_engine.adapters.importers import TextbookGraphImportPipeline
from p2_engine.adapters.persistence import SqliteGraphRepository, SqliteStore
from textbook_builder.workflows import TextbookDefinitionBuildWorkflow


def test_workflow_exports_formal_workbook_that_p4_can_import() -> None:
    temp_dir = PROJECT_ROOT / "storage" / "test_runs" / f"workflow_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        source_path = temp_dir / "source.json"
        draft_path = temp_dir / "draft.xlsx"
        formal_path = temp_dir / "formal.xlsx"
        review_path = temp_dir / "review.html"
        db_path = temp_dir / "p2.sqlite3"
        source_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "source_id": "TEXTBOOK_G8_TERM1",
                            "source_type": "structured_textbook",
                            "source_path": "demo",
                            "subject": "math",
                            "grade": "g8",
                            "term": "term1",
                            "source_document_type": "electronic_textbook",
                            "education_stage": "junior_middle_school",
                            "grade_band": "g7_g9",
                            "subject_tags": ["math", "geometry"],
                            "raw_text": "Chapter 13 triangle",
                            "raw_structure": {
                                "chapters": [
                                    {
                                        "chapter": "ch13",
                                        "title": "Triangle",
                                        "knowledge_points": [
                                            {
                                                "display_name": "Triangle Concept",
                                                "node_name": "triangle_concept",
                                                "knowledge_type": "concept",
                                                "cognitive_level": "understand",
                                                "review_status": "accepted",
                                            }
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        formal = TextbookDefinitionBuildWorkflow().export_all(
            source_path,
            draft_path=draft_path,
            formal_path=formal_path,
            graph_id="GRAPH_MATH_G8_TERM1_V1",
            review_path=review_path,
        )
        store = SqliteStore(str(db_path))
        store.initialize_schema()
        TextbookGraphImportPipeline().import_file(store, formal_path)
        graph = SqliteGraphRepository(store).get_graph("GRAPH_MATH_G8_TERM1_V1")

        assert draft_path.exists()
        assert formal_path.exists()
        assert review_path.exists()
        assert "chapter-container" in review_path.read_text(encoding="utf-8")
        assert len(formal.nodes) == 2
        assert formal.nodes[1].knowledge_type == "concept"
        assert formal.nodes[1].grade_band == "g7_g9"
        assert formal.nodes[1].subject_tags == ["math", "geometry"]
        assert len(graph.nodes) == 2
        assert graph.chapter_index["ch13"] == [
            "math_g8_term1_ch13_triangle",
            "math_g8_term1_ch13_triangle_concept",
        ]
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_workflow_can_rebuild_formal_output_from_reviewed_draft() -> None:
    temp_dir = PROJECT_ROOT / "storage" / "test_runs" / f"reviewed_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        source_path = temp_dir / "source.json"
        draft_path = temp_dir / "draft.xlsx"
        formal_path = temp_dir / "formal.xlsx"
        reviewed_formal_path = temp_dir / "reviewed_formal.xlsx"
        reviewed_review_path = temp_dir / "reviewed_review.html"
        db_path = temp_dir / "p2.sqlite3"
        source_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "source_id": "TEXTBOOK_G8_TERM1",
                            "source_type": "structured_textbook",
                            "source_path": "demo",
                            "subject": "math",
                            "grade": "g8",
                            "term": "term1",
                            "raw_text": "Chapter 13 triangle",
                            "raw_structure": {
                                "chapters": [
                                    {
                                        "chapter": "ch13",
                                        "title": "Triangle",
                                        "knowledge_points": [
                                            {
                                                "display_name": "Triangle Concept",
                                                "node_name": "triangle_concept",
                                                "review_status": "accepted",
                                            }
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        workflow = TextbookDefinitionBuildWorkflow()
        first_formal = workflow.export_all(
            source_path,
            draft_path=draft_path,
            formal_path=formal_path,
            graph_id="GRAPH_MATH_G8_TERM1_V1",
        )
        reviewed_formal = workflow.export_reviewed_draft(
            draft_path,
            formal_path=reviewed_formal_path,
            graph_id="GRAPH_MATH_G8_TERM1_V1",
            review_path=reviewed_review_path,
        )
        store = SqliteStore(str(db_path))
        store.initialize_schema()
        TextbookGraphImportPipeline().import_file(store, reviewed_formal_path)
        graph = SqliteGraphRepository(store).get_graph("GRAPH_MATH_G8_TERM1_V1")

        assert reviewed_formal_path.exists()
        assert reviewed_review_path.exists()
        assert len(reviewed_formal.nodes) == len(first_formal.nodes)
        assert len(graph.nodes) == len(first_formal.nodes)
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_workflow_preserves_accepted_knowledge_relations() -> None:
    temp_dir = PROJECT_ROOT / "storage" / "test_runs" / f"relations_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        source_path = temp_dir / "source.json"
        draft_path = temp_dir / "draft.xlsx"
        formal_path = temp_dir / "formal.xlsx"
        review_path = temp_dir / "review.html"
        db_path = temp_dir / "p2.sqlite3"
        concept_id = "math_g8_term1_ch13_triangle_concept"
        relation_id = "math_g8_term1_ch13_triangle_side_relation"
        source_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "source_id": "TEXTBOOK_G8_TERM1",
                            "source_type": "structured_textbook",
                            "source_path": "demo",
                            "subject": "math",
                            "grade": "g8",
                            "term": "term1",
                            "raw_text": "Chapter 13 triangle",
                            "raw_structure": {
                                "chapters": [
                                    {
                                        "chapter": "ch13",
                                        "title": "Triangle",
                                        "knowledge_points": [
                                            {
                                                "display_name": "Triangle Concept",
                                                "node_name": "triangle_concept",
                                                "review_status": "accepted",
                                                "relations": [
                                                    {
                                                        "target_node_id": relation_id,
                                                        "relation_type": "progressive",
                                                        "confidence": 0.85,
                                                        "relation_evidence": "Concept comes before side relation.",
                                                        "review_status": "accepted",
                                                    }
                                                ],
                                            },
                                            {
                                                "display_name": "Triangle Side Relation",
                                                "node_name": "triangle_side_relation",
                                                "review_status": "accepted",
                                            },
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        formal = TextbookDefinitionBuildWorkflow().export_all(
            source_path,
            draft_path=draft_path,
            formal_path=formal_path,
            graph_id="GRAPH_MATH_G8_TERM1_V1",
            review_path=review_path,
        )
        store = SqliteStore(str(db_path))
        store.initialize_schema()
        TextbookGraphImportPipeline().import_file(store, formal_path)
        graph = SqliteGraphRepository(store).get_graph("GRAPH_MATH_G8_TERM1_V1")
        edge_keys = {
            (edge.source_node_id, edge.target_node_id, edge.relation_type)
            for edge in formal.edges
        }
        imported_edge_keys = {
            (edge.source_node_id, edge.target_node_id, edge.relation_type)
            for edge in graph.edges
        }

        assert (concept_id, relation_id, "progressive") in edge_keys
        assert (concept_id, relation_id, "progressive") in imported_edge_keys
        assert "progressive" in review_path.read_text(encoding="utf-8")
    finally:
        rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_workflow_exports_formal_workbook_that_p4_can_import()
    test_workflow_can_rebuild_formal_output_from_reviewed_draft()
    test_workflow_preserves_accepted_knowledge_relations()
    print("PASS builder workflow tests")
