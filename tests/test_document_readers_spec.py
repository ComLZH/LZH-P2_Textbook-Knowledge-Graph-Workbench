from __future__ import annotations

import sys
from pathlib import Path
from shutil import rmtree
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from textbook_builder.readers import DocumentReadOptions, PdfPageRange, SourceDocumentReader


def test_document_reader_supports_pdf_png_and_jpg_inputs() -> None:
    temp_dir = PROJECT_ROOT / "storage" / "test_runs" / f"documents_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        pdf_path = temp_dir / "sample.pdf"
        png_path = temp_dir / "sample.png"
        jpg_path = temp_dir / "sample.jpg"
        _write_minimal_pdf(pdf_path)
        _write_minimal_png(png_path)
        _write_minimal_jpg(jpg_path)
        options = DocumentReadOptions(
            subject="math",
            grade="g8",
            term="term1",
            source_document_type="electronic_textbook",
            education_stage="junior_middle_school",
            grade_band="g7_g9",
            subject_tags=["math", "geometry"],
        )
        reader = SourceDocumentReader()

        pdf = reader.read(pdf_path, options)[0]
        png = reader.read(png_path, options)[0]
        jpg = reader.read(jpg_path, options)[0]

        assert pdf.source_format == "pdf"
        assert "Triangle Concept" in pdf.raw_text
        assert pdf.source_metadata["page_count"] == 1
        assert png.source_format == "png"
        assert png.source_metadata["width"] == 2
        assert png.source_metadata["height"] == 2
        assert png.source_metadata["requires_ocr"] is True
        assert jpg.source_format == "jpg"
        assert jpg.source_metadata["width"] == 3
        assert jpg.source_metadata["height"] == 2
        assert jpg.source_metadata["requires_ocr"] is True
    finally:
        rmtree(temp_dir, ignore_errors=True)


def test_pdf_reader_supports_manual_page_range_selection_and_split() -> None:
    temp_dir = PROJECT_ROOT / "storage" / "test_runs" / f"pdf_ranges_{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        pdf_path = temp_dir / "multi_page.pdf"
        _write_manual_three_page_pdf(pdf_path)
        split_dir = temp_dir / "splits"
        options = DocumentReadOptions(
            subject="math",
            grade="g8",
            term="term1",
            source_id="TEXTBOOK",
            pdf_page_ranges=[PdfPageRange(2, 3, label="chapter_1")],
            split_pdf=True,
            split_output_dir=str(split_dir),
        )

        record = SourceDocumentReader().read(pdf_path, options)[0]

        assert record.source_id == "TEXTBOOK_p2_p3_chapter_1"
        assert record.source_format == "pdf"
        assert record.source_metadata["page_count"] == 3
        assert record.source_metadata["selected_pages"] == [2, 3]
        assert record.source_metadata["selected_page_count"] == 2
        assert record.source_metadata["page_range_label"] == "chapter_1"
        split_path = str(record.source_metadata["split_pdf_path"])
        if split_path:
            assert Path(split_path).exists()
            assert record.source_metadata["split_pdf_status"] == "written"
        else:
            assert record.source_metadata["split_pdf_status"] == "unavailable"
        assert Path(record.source_path).exists()
    finally:
        rmtree(temp_dir, ignore_errors=True)


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


def _write_manual_three_page_pdf(path: Path) -> None:
    path.write_bytes(
        b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R 5 0 R 7 0 R] /Count 3 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R >>
endobj
4 0 obj
<< /Length 49 >>
stream
BT /F1 12 Tf 72 96 Td (Page One Intro) Tj ET
endstream
endobj
5 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 6 0 R >>
endobj
6 0 obj
<< /Length 53 >>
stream
BT /F1 12 Tf 72 96 Td (Page Two Triangle) Tj ET
endstream
endobj
7 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 8 0 R >>
endobj
8 0 obj
<< /Length 55 >>
stream
BT /F1 12 Tf 72 96 Td (Page Three Relation) Tj ET
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


if __name__ == "__main__":
    test_document_reader_supports_pdf_png_and_jpg_inputs()
    test_pdf_reader_supports_manual_page_range_selection_and_split()
    print("PASS document reader tests")
