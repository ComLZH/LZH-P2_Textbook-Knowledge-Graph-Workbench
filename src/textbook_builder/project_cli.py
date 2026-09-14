from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from shutil import copytree, rmtree
from uuid import uuid4

from .exporters import XlsxWorkbookExporter
from .llm import LlmCandidatePayloadParser
from .readers import DocumentReadOptions, PdfPageRange, SourceDocumentReader
from .utils.subject_metadata import derive_subject_metadata, missing_required_metadata
from .review_views import G6ReviewHtmlRenderer, GraphReviewPayloadBuilder
from .review_document import ExportProfileDTO
from .workflows import TextbookDefinitionBuildWorkflow
from .services import (
    MODEL_MODE_REMOTE,
    PageContinuityChecker,
    PageEvidenceCache,
    PageEvidenceService,
    PaddleOcrPageAnalyzer,
    WorkbenchAnalysisRequest,
    WorkbenchAnalysisService,
    WorkbenchReviewService,
)
from .utils.env_config import env_value, load_project_env


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEXTBOOK_PATH = PROJECT_ROOT / "textbook" / "2025秋人教版八上数学电子课本.pdf"


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command = args.command or "all"
    if command != "tests":
        missing = missing_required_metadata(args.subject, args.grade, args.term)
        if missing:
            parser.error("以下教材元数据必须显式提供：" + "、".join(missing))
    if command == "all":
        run_all(args)
    elif command == "page-select":
        run_page_select(args)
    elif command == "llm-review":
        run_llm_review(args)
    elif command == "document-readers":
        run_document_readers(args)
    elif command == "builder":
        run_builder(args)
    elif command == "tests":
        run_tests(args)
    elif command == "staged-check":
        run_staged_check(args)
    else:
        parser.print_help()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_project.py",
        description="教材知识点定义表构建工具统一入口",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "all",
            "page-select",
            "llm-review",
            "document-readers",
            "builder",
            "tests",
            "staged-check",
        ],
        help="默认 all：运行核心项目演示链路。",
    )
    parser.add_argument(
        "--pdf",
        default=str(DEFAULT_TEXTBOOK_PATH),
        help="标准电子书 PDF 路径。",
    )
    parser.add_argument(
        "--pages",
        default="1-8",
        help="手动指定 PDF 页码范围，例如 1-8 或 12-20。页码为 1-based PDF 页码。",
    )
    parser.add_argument("--label", default="manual_scope", help="页码范围标签。")
    parser.add_argument("--subject", default="", help="必填；例如 math、art。")
    parser.add_argument("--grade", default="", help="必填；例如 g8。")
    parser.add_argument("--term", default="", help="必填；例如 term1。")
    parser.add_argument("--source-id", default="")
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "storage" / "project_run"),
        help="统一运行输出目录。",
    )
    parser.add_argument(
        "--split-pdf",
        action="store_true",
        help="尝试导出选中页码范围的局部 PDF；需要 pypdf/PyPDF2/可用 PyMuPDF。",
    )
    parser.add_argument(
        "--remote",
        action="store_true",
        help="staged-check 使用 .env 中的第三方模型完成节点和关系抽取；默认只建立本地页面证据。",
    )
    parser.add_argument(
        "--check-page-order",
        action="store_true",
        help="检查当前页面顺序；只标记疑点，不自动重排。",
    )
    parser.add_argument(
        "--disable-ocr",
        action="store_true",
        help="关闭扫描页本地OCR；PDF原生文本仍会使用。",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="即使PDF包含原生文本，也强制使用本地OCR。",
    )
    parser.add_argument(
        "--skip-relation-completion",
        action="store_true",
        help="远程测试中跳过最终文本关系整合，只保留首轮局部关系线索。",
    )
    return parser


def run_all(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print("== Textbook Builder Project: all ==")
    page_snapshot = run_page_select(args)
    llm_snapshot = run_llm_review(args)
    builder_snapshot = run_builder(args)
    summary = {
        "page_select": page_snapshot,
        "llm_review": llm_snapshot,
        "builder": builder_snapshot,
    }
    summary_path = output_dir / "project_run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary={summary_path}")


def run_page_select(args: argparse.Namespace) -> dict[str, object]:
    output_dir = Path(args.output_dir) / "pdf_page_selection"
    split_dir = output_dir / "splits"
    output_dir.mkdir(parents=True, exist_ok=True)
    records = _read_selected_pdf(
        pdf_path=Path(args.pdf),
        args=args,
        split_output_dir=split_dir,
    )
    snapshot = [
        {
            "source_id": record.source_id,
            "source_path": record.source_path,
            "source_format": record.source_format,
            "raw_text_preview": record.raw_text[:500],
            "raw_structure": record.raw_structure,
            "source_metadata": record.source_metadata,
        }
        for record in records
    ]
    snapshot_path = output_dir / "selected_pdf_source_records.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "snapshot_path": str(snapshot_path),
        "record_count": len(records),
        "selected_pages": records[0].source_metadata.get("selected_pages") if records else [],
        "split_pdf_path": records[0].source_metadata.get("split_pdf_path") if records else "",
        "split_pdf_status": records[0].source_metadata.get("split_pdf_status") if records else "",
    }
    print("== page-select ==")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_llm_review(args: argparse.Namespace) -> dict[str, object]:
    output_dir = Path(args.output_dir) / "llm_review"
    split_dir = output_dir / "splits"
    output_dir.mkdir(parents=True, exist_ok=True)
    record = _read_selected_pdf(
        pdf_path=Path(args.pdf),
        args=args,
        split_output_dir=split_dir,
    )[0]
    drafts = LlmCandidatePayloadParser().parse(
        json.dumps(_offline_llm_payload(), ensure_ascii=False),
        record=record,
    )
    draft_path = output_dir / "standard_textbook_llm_draft.xlsx"
    review_path = output_dir / "standard_textbook_llm_review.html"
    XlsxWorkbookExporter().export_draft(drafts, draft_path)
    review_workbook = TextbookDefinitionBuildWorkflow().export_review_only(
        drafts,
        review_path=review_path,
        graph_id="REVIEW_RJ_MATH_G8_2025_AUTUMN",
        textbook_version="rj_math_g8_2025_autumn",
    )
    result = {
        "draft_path": str(draft_path),
        "review_path": str(review_path),
        "review_node_count": len(review_workbook.nodes),
        "review_edge_count": len(review_workbook.edges),
    }
    print("== llm-review ==")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_document_readers(args: argparse.Namespace) -> dict[str, object]:
    output_dir = Path(args.output_dir) / "document_readers"
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / "sample_textbook_page.pdf"
    png_path = output_dir / "sample_scan.png"
    jpg_path = output_dir / "sample_photo.jpg"
    _write_minimal_pdf(pdf_path)
    _write_minimal_png(png_path)
    _write_minimal_jpg(jpg_path)
    options = _document_options(args)
    reader = SourceDocumentReader()
    records = []
    for path in [pdf_path, png_path, jpg_path]:
        records.extend(reader.read(path, options))
    snapshot = [
        {
            "source_id": record.source_id,
            "source_format": record.source_format,
            "source_path": record.source_path,
            "raw_text": record.raw_text,
            "raw_structure": record.raw_structure,
            "source_metadata": record.source_metadata,
        }
        for record in records
    ]
    snapshot_path = output_dir / "document_reader_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"snapshot_path": str(snapshot_path), "record_count": len(records)}
    print("== document-readers ==")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_builder(args: argparse.Namespace) -> dict[str, object]:
    from p2_engine.adapters.importers import TextbookGraphImportPipeline
    from p2_engine.adapters.persistence import SqliteGraphRepository, SqliteStore

    output_dir = Path(args.output_dir) / "builder"
    output_dir.mkdir(parents=True, exist_ok=True)
    source_path = output_dir / "demo_textbook_source.json"
    draft_path = output_dir / "demo_working_draft.xlsx"
    formal_path = output_dir / "demo_formal_graph.xlsx"
    review_path = output_dir / "demo_review.html"
    reviewed_formal_path = output_dir / "demo_reviewed_formal_graph.xlsx"
    reviewed_review_path = output_dir / "demo_reviewed_review.html"
    db_path = output_dir / "demo_p2_import.sqlite3"
    _write_demo_source(source_path)

    workflow = TextbookDefinitionBuildWorkflow()
    formal = workflow.export_all(
        source_path,
        draft_path=draft_path,
        formal_path=formal_path,
        graph_id="GRAPH_MATH_G8_TERM1_V1",
        review_path=review_path,
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
    imported_graph = SqliteGraphRepository(store).get_graph(formal.metadata.graph_id)
    result = {
        "source_path": str(source_path),
        "draft_path": str(draft_path),
        "formal_path": str(formal_path),
        "review_path": str(review_path),
        "reviewed_formal_path": str(reviewed_formal_path),
        "reviewed_review_path": str(reviewed_review_path),
        "db_path": str(db_path),
        "graph_id": formal.metadata.graph_id,
        "node_count": len(formal.nodes),
        "edge_count": len(formal.edges),
        "reviewed_node_count": len(reviewed_formal.nodes),
        "reviewed_edge_count": len(reviewed_formal.edges),
        "imported_node_count": len(imported_graph.nodes),
        "imported_edge_count": len(imported_graph.edges),
    }
    print("== builder ==")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_tests(args: argparse.Namespace) -> dict[str, object]:
    import pytest

    test_dir = PROJECT_ROOT / "tests"
    basetemp = PROJECT_ROOT / "storage" / "test_runs" / "pytest_tmp"
    basetemp.mkdir(parents=True, exist_ok=True)
    exit_code = pytest.main([
        "-q",
        "-p", "no:cacheprovider",
        f"--basetemp={basetemp}",
        str(test_dir),
    ])
    if exit_code != pytest.ExitCode.OK:
        raise SystemExit(int(exit_code))
    result = {"status": "passed", "test_dir": str(test_dir)}
    print("== tests ==")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run_staged_check(args: argparse.Namespace) -> dict[str, object]:
    output_dir = Path(args.output_dir) / "staged_check"
    output_dir.mkdir(parents=True, exist_ok=True)
    page_range = _parse_page_range(args.pages, args.label)
    dotenv_values = load_project_env(PROJECT_ROOT)
    if args.remote:
        api_key = env_value("TEXTBOOK_BUILDER_LLM_API_KEY", dotenv_values=dotenv_values)
        service = WorkbenchAnalysisService(
            render_dir=output_dir / "rendered",
            ocr_settings=dotenv_values,
        )
        result = service.analyze(
            WorkbenchAnalysisRequest(
                source_path=Path(args.pdf),
                source_format="pdf",
                options=_document_options(args),
                start_page=page_range.start_page,
                end_page=page_range.end_page,
                scope_label=args.label,
                model_mode=MODEL_MODE_REMOTE,
                api_key=api_key,
                dotenv_values=dotenv_values,
                ocr_enabled=not args.disable_ocr,
                force_ocr=bool(args.force_ocr),
                page_order_check_enabled=bool(args.check_page_order),
                staged_pipeline_enabled=True,
                relation_completion_enabled=not args.skip_relation_completion,
                progress_recording_mode=env_value(
                    "TEXTBOOK_BUILDER_PROGRESS_RECORDING_MODE",
                    dotenv_values=dotenv_values,
                    default="full",
                ),
            )
        )
        review_output_dir = output_dir / "review"
        review_service = WorkbenchReviewService()
        review_document = review_service.create_review_document(
            result.drafts,
            source_identity=result.record.source_id,
            analysis_run_id=result.analysis_run_id,
            staged_outcome=result.staged_outcome,
        )
        review_document_path = review_output_dir / "review_document_v2.xlsx"
        review_save = review_service.save_review_document(
            review_document,
            review_document_path,
        )
        render_bundle = review_service.build_review_render_bundle(
            review_document,
            renderer="g6",
        )
        graph_payload = GraphReviewPayloadBuilder().build_from_projection(
            projection=render_bundle.projection,
            geometry=render_bundle.geometry,
            document=review_document,
        )
        publish_plan = review_service.preflight_publish(
            review_document,
            ExportProfileDTO("cli-p4-v1"),
        )
        graph_html_path = review_output_dir / "graph_review.html"
        G6ReviewHtmlRenderer().render_to_file(
            payload=graph_payload,
            output_path=graph_html_path,
        )
        graph_vendor_source = PROJECT_ROOT / "storage" / "graph_review_static" / "vendor"
        if graph_vendor_source.is_dir():
            copytree(
                graph_vendor_source,
                review_output_dir / "vendor",
                dirs_exist_ok=True,
            )
        snapshot = {
            "analysis_run_id": result.analysis_run_id,
            "progress_recording_dir": result.progress_recording_dir,
            "progress_recording_mode": result.progress_recording_mode,
            "run_manifest": asdict(result.run_manifest) if result.run_manifest else {},
            "page_evidence": [asdict(item) for item in result.page_evidence],
            "continuity_results": [asdict(item) for item in result.continuity_results],
            "draft_count": len(result.drafts),
            "node_count": len(result.workbook.nodes),
            "edge_count": len(result.workbook.edges),
            "staged_warnings": result.staged_outcome.warnings if result.staged_outcome else [],
            "book_manifest": (
                asdict(result.staged_outcome.book_manifest) if result.staged_outcome else {}
            ),
            "canonical_nodes": (
                [asdict(item) for item in result.staged_outcome.canonical_nodes]
                if result.staged_outcome
                else []
            ),
            "relation_candidates": (
                [asdict(item) for item in result.staged_outcome.relation_candidates]
                if result.staged_outcome
                else []
            ),
            "unresolved_relation_claim_count": (
                len(result.staged_outcome.unresolved_relation_claims)
                if result.staged_outcome
                else 0
            ),
            "catalog_hierarchy": {
                "combo_count": len(graph_payload["combos"]),
                "visible_node_count": len(graph_payload["nodes"]),
                "hierarchy_link_count": len(
                    graph_payload["hierarchy"]["parent_by_child"]
                ),
                "layout_plan_count": len(graph_payload["layout_plan"]),
                "issue_count": len(graph_payload["hierarchy"]["issues"]),
            },
            "review_artifacts": {
                "review_document_path": str(review_document_path),
                "base_export_id": review_save.base_export_id,
                "preflight_status": publish_plan.status,
                "preflight_blockers": [asdict(item) for item in publish_plan.blockers],
                "formal_path": "",
                "layer_mapping_path": "",
                "graph_html_path": str(graph_html_path),
            },
        }
    else:
        cache = PageEvidenceCache(output_dir / "page_evidence_cache")
        evidence_service = PageEvidenceService(
            analyzer=PaddleOcrPageAnalyzer(cache=cache, settings=dotenv_values),
            render_root=output_dir / "rendered",
        )
        pages = list(
            evidence_service.build(
                Path(args.pdf),
                source_id=args.source_id,
                selected_pages=range(page_range.start_page, page_range.end_page + 1),
                ocr_enabled=not args.disable_ocr,
                force_ocr=bool(args.force_ocr),
            )
        )
        continuity = PageContinuityChecker().check(pages) if args.check_page_order else []
        snapshot = {
            "mode": "local_page_evidence_only",
            "page_evidence": [asdict(item) for item in pages],
            "continuity_results": [asdict(item) for item in continuity],
        }
    snapshot_path = output_dir / "staged_check_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    result_summary = {
        "snapshot_path": str(snapshot_path),
        "page_count": len(snapshot.get("page_evidence", [])),
        "remote": bool(args.remote),
        "page_order_check": bool(args.check_page_order),
    }
    print("== staged-check ==")
    print(json.dumps(result_summary, ensure_ascii=False, indent=2))
    return result_summary


def _read_selected_pdf(
    *,
    pdf_path: Path,
    args: argparse.Namespace,
    split_output_dir: Path,
):
    metadata = derive_subject_metadata(args.subject, args.grade, args.term)
    return SourceDocumentReader().read(
        pdf_path,
        DocumentReadOptions(
            subject=args.subject,
            grade=args.grade,
            term=args.term,
            source_id=args.source_id,
            source_document_type="electronic_textbook",
            education_stage=metadata.education_stage,
            grade_band=metadata.grade_band,
            subject_tags=list(metadata.subject_tags),
            pdf_page_ranges=[_parse_page_range(args.pages, args.label)],
            split_pdf=bool(args.split_pdf),
            split_output_dir=str(split_output_dir),
        ),
    )


def _document_options(args: argparse.Namespace) -> DocumentReadOptions:
    metadata = derive_subject_metadata(args.subject, args.grade, args.term)
    return DocumentReadOptions(
        subject=args.subject,
        grade=args.grade,
        term=args.term,
        source_id=args.source_id,
        source_document_type="electronic_textbook",
        education_stage=metadata.education_stage,
        grade_band=metadata.grade_band,
        subject_tags=list(metadata.subject_tags),
    )


def _parse_page_range(raw_value: str, label: str) -> PdfPageRange:
    value = raw_value.strip()
    if "-" in value:
        start, end = value.split("-", 1)
        return PdfPageRange(int(start.strip()), int(end.strip()), label=label)
    page = int(value)
    return PdfPageRange(page, page, label=label)


def _offline_llm_payload() -> dict[str, object]:
    return {
        "chapters": [
            {
                "chapter": "ch11",
                "title": "三角形",
                "knowledge_points": [
                    {
                        "candidate_display_name": "三角形的边",
                        "candidate_node_name": "triangle_sides",
                        "knowledge_type": "concept",
                        "cognitive_level": "understand",
                        "source_text": "教材中三角形章节围绕三角形的边、角及相关性质展开。",
                        "source_location": "page=chapter_start;section=三角形",
                        "confidence": 0.72,
                        "reasoning_summary": "该候选是三角形章节中最基础的组成要素，适合作为后续三边关系的前置节点。",
                        "review_status": "pending",
                    },
                    {
                        "candidate_display_name": "三角形三边关系",
                        "candidate_node_name": "triangle_side_relation",
                        "knowledge_type": "property",
                        "cognitive_level": "apply",
                        "source_text": "三角形任意两边的和大于第三边。",
                        "source_location": "page=chapter_start;section=三角形",
                        "confidence": 0.78,
                        "reasoning_summary": "该关系是三角形边相关内容中的核心性质，需要教师依据原文确认页码与表述。",
                        "review_status": "pending",
                        "candidate_relations": [
                            {
                                "source_node_id": "math_g8_term1_ch11_triangle_sides",
                                "target_node_id": "math_g8_term1_ch11_triangle_side_relation",
                                "relation_type": "progressive",
                                "confidence": 0.74,
                                "relation_evidence": "先理解三角形的边，再讨论三边之间的数量关系。",
                                "reasoning_summary": "从组成要素到性质判断，属于递进关系。",
                                "review_status": "pending",
                            }
                        ],
                    },
                ],
            }
        ]
    }


def _write_demo_source(source_path: Path) -> None:
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
                        "source_format": "structured_json",
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
                                            "relations": [
                                                {
                                                    "target_node_id": "math_g8_term1_ch13_triangle_side_relation",
                                                    "relation_type": "progressive",
                                                    "confidence": 0.85,
                                                    "relation_evidence": "Textbook sequence introduces concept before side relation.",
                                                    "relation_source": "chapter_order",
                                                    "review_status": "accepted",
                                                }
                                            ],
                                        },
                                        {
                                            "display_name": "Triangle Side Relation",
                                            "node_name": "triangle_side_relation",
                                            "knowledge_type": "property",
                                            "cognitive_level": "apply",
                                            "review_status": "accepted",
                                            "prerequisites": [
                                                "math_g8_term1_ch13_triangle_concept"
                                            ],
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


def _write_minimal_pdf(path: Path) -> None:
    path.write_bytes(
        b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 56 >>
stream
BT /F1 12 Tf 72 96 Td (Triangle Concept) Tj ET
endstream
endobj
trailer
<< /Root 1 0 R >>
%%EOF
"""
    )


def _write_minimal_png(path: Path) -> None:
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a"
            "0000000d49484452"
            "0000000200000002"
            "0802000000"
            "fdd49a73"
            "0000000049454e44ae426082"
        )
    )


def _write_minimal_jpg(path: Path) -> None:
    path.write_bytes(
        bytes.fromhex(
            "ffd8"
            "ffe000104a46494600010100000100010000"
            "ffc00011080002000303012200021101031101"
            "ffd9"
        )
    )
