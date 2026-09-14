from __future__ import annotations

import sys
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.contracts import (
    DraftKnowledgeItemDTO,
    FormalEdgeDTO,
    FormalGraphMetadataDTO,
    FormalGraphWorkbookDTO,
    FormalNodeDTO,
)
from textbook_builder.exporters import ReviewHtmlExporter


def test_review_html_exporter_contains_v2_visual_structure_hints() -> None:
    temp_dir = PROJECT_ROOT / "storage" / "test_runs" / f"review_html_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        workbook = FormalGraphWorkbookDTO(
            metadata=FormalGraphMetadataDTO("graph_v2", "math", ["g8"], ["term1"]),
            nodes=[
                FormalNodeDTO("chapter_1", "math", "g8", "term1", "轴对称", "轴对称", "axis", node_type="container", knowledge_type="container"),
                FormalNodeDTO("k1", "math", "g8", "term1", "轴对称", "图形的轴对称", "axis_concept", parent_node_id="chapter_1", node_type="concept", knowledge_type="concept"),
                FormalNodeDTO("k2", "math", "g8", "term1", "轴对称", "画轴对称的图形", "axis_method", parent_node_id="chapter_1", node_type="method", knowledge_type="method"),
                FormalNodeDTO("a1", "math", "g8", "term1", "轴对称", "最短路径问题", "shortest_path", parent_node_id="chapter_1", node_type="problem_type", knowledge_type="problem_type"),
            ],
            edges=[
                FormalEdgeDTO("chapter_1", "k1", "contains"),
                FormalEdgeDTO("chapter_1", "k2", "contains"),
                FormalEdgeDTO("chapter_1", "a1", "contains"),
                FormalEdgeDTO("k1", "k2", "progressive"),
                FormalEdgeDTO("k2", "a1", "applies_to"),
            ],
        )
        drafts = [
            DraftKnowledgeItemDTO(
                draft_id="d1",
                subject="math",
                grade="g8",
                term="term1",
                chapter="轴对称",
                candidate_display_name="图形的轴对称",
                candidate_node_name="axis_concept",
                candidate_node_id="k1",
                knowledge_type="concept",
            ),
            DraftKnowledgeItemDTO(
                draft_id="d2",
                subject="math",
                grade="g8",
                term="term1",
                chapter="轴对称",
                candidate_display_name="画轴对称的图形",
                candidate_node_name="axis_method",
                candidate_node_id="k2",
                knowledge_type="method",
            ),
            DraftKnowledgeItemDTO(
                draft_id="d3",
                subject="math",
                grade="g8",
                term="term1",
                chapter="轴对称",
                candidate_display_name="最短路径问题",
                candidate_node_name="shortest_path",
                candidate_node_id="a1",
                knowledge_type="problem_type",
            ),
        ]
        output_path = temp_dir / "review.html"
        ReviewHtmlExporter().export_review(drafts=drafts, workbook=workbook, file_path=output_path)
        html = output_path.read_text(encoding="utf-8")

        assert "主线 2" in html
        assert "单主线" in html
        assert "node concept main-path" in html
        assert "node problem_type auxiliary" in html
        assert "layout_main_path" in html
        assert "arrow-green" in html
        assert "主路径" in html
        assert "递进" in html
        assert "pointerdown" in html
        assert "refreshConnectedEdges" in html
        assert 'data-source-id="k1"' in html
        assert 'data-x="' in html
    finally:
        rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    test_review_html_exporter_contains_v2_visual_structure_hints()
    print("PASS review html exporter tests")
