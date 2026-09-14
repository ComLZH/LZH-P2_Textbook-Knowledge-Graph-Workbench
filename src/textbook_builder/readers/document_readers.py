from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..contracts import SourceRecordDTO


@dataclass(slots=True)
class PdfPageRange:
    start_page: int
    end_page: int
    label: str = ""

    def normalized(self) -> "PdfPageRange":
        start = min(self.start_page, self.end_page)
        end = max(self.start_page, self.end_page)
        if start < 1:
            raise ValueError("PDF page ranges are 1-based and must start at page 1 or later.")
        return PdfPageRange(start_page=start, end_page=end, label=self.label)


@dataclass(slots=True)
class DocumentReadOptions:
    subject: str
    grade: str
    term: str
    source_id: str = ""
    source_type: str = "raw_document"
    source_document_type: str = "electronic_textbook"
    education_stage: str = ""
    grade_band: str = ""
    subject_tags: list[str] | None = None
    pdf_page_ranges: list[PdfPageRange] | None = None
    split_pdf: bool = False
    split_output_dir: str = ""


class PdfSourceReader:
    def read(
        self,
        file_path: str | Path,
        options: DocumentReadOptions,
    ) -> list[SourceRecordDTO]:
        path = Path(file_path)
        payload = path.read_bytes()
        if not payload.startswith(b"%PDF"):
            raise ValueError(f"Not a PDF file: {path}")
        page_count = self._count_pdf_pages_with_fitz(path) or self._count_pdf_pages(payload)
        page_ranges = [page_range.normalized() for page_range in options.pdf_page_ranges or []]
        if page_ranges:
            return self._read_page_ranges(
                path=path,
                payload=payload,
                options=options,
                page_count=page_count,
                page_ranges=page_ranges,
            )

        extracted_text = self._extract_pdf_text_with_fitz(path) or self._extract_simple_pdf_text(payload)
        extraction_status = "text_extracted" if extracted_text else "metadata_only"
        return [
            SourceRecordDTO(
                source_id=options.source_id or path.stem,
                source_type=options.source_type,
                source_path=str(path),
                subject=options.subject,
                grade=options.grade,
                term=options.term,
                raw_text=extracted_text,
                raw_structure={
                    "document_blocks": [
                        {
                            "block_type": "pdf_document",
                            "source_location": "page=all",
                            "page_start": 1,
                            "page_end": page_count,
                            "text": extracted_text,
                        }
                    ],
                    "page_count": page_count,
                },
                source_format="pdf",
                source_document_type=options.source_document_type,
                education_stage=options.education_stage,
                grade_band=options.grade_band,
                subject_tags=list(options.subject_tags or []),
                source_metadata={
                    "file_name": path.name,
                    "file_size": path.stat().st_size,
                    "page_count": page_count,
                    "selected_pages": "all",
                    "selected_page_count": page_count,
                    "extraction_status": extraction_status,
                    "requires_ocr": not bool(extracted_text),
                    "reader_note": "MVP reader extracts simple embedded text only; scanned PDFs should enter OCR later.",
                },
            )
        ]

    def _read_page_ranges(
        self,
        *,
        path: Path,
        payload: bytes,
        options: DocumentReadOptions,
        page_count: int,
        page_ranges: list[PdfPageRange],
    ) -> list[SourceRecordDTO]:
        records: list[SourceRecordDTO] = []
        for index, page_range in enumerate(page_ranges, start=1):
            if page_range.end_page > page_count:
                raise ValueError(
                    f"PDF page range {page_range.start_page}-{page_range.end_page} "
                    f"exceeds page count {page_count}: {path}"
                )
            selected_pages = list(range(page_range.start_page, page_range.end_page + 1))
            range_text, blocks = self._extract_page_range_text_with_fitz(
                path,
                selected_pages=selected_pages,
            )
            if not blocks:
                blocks = [
                    {
                        "block_type": "pdf_page_range",
                        "source_location": self._page_range_location(page_range),
                        "page_start": page_range.start_page,
                        "page_end": page_range.end_page,
                        "text": "",
                    }
                ]
            split_path = ""
            split_status = "not_requested"
            if options.split_pdf:
                split_path = self._write_split_pdf(
                    path,
                    page_range=page_range,
                    output_dir=Path(options.split_output_dir)
                    if options.split_output_dir
                    else path.parent / f"{path.stem}_splits",
                )
                split_status = "written" if split_path else "unavailable"
            source_id = self._range_source_id(
                options.source_id or path.stem,
                page_range=page_range,
                index=index,
            )
            extraction_status = "text_extracted" if range_text else "metadata_only"
            records.append(
                SourceRecordDTO(
                    source_id=source_id,
                    source_type=options.source_type,
                    source_path=split_path or str(path),
                    subject=options.subject,
                    grade=options.grade,
                    term=options.term,
                    raw_text=range_text,
                    raw_structure={
                        "document_blocks": blocks,
                        "page_count": page_count,
                        "selected_pages": selected_pages,
                        "selected_page_count": len(selected_pages),
                        "page_range_label": page_range.label,
                        "original_source_path": str(path),
                    },
                    source_format="pdf",
                    source_document_type=options.source_document_type,
                    education_stage=options.education_stage,
                    grade_band=options.grade_band,
                    subject_tags=list(options.subject_tags or []),
                    source_metadata={
                        "file_name": path.name,
                        "file_size": path.stat().st_size,
                        "page_count": page_count,
                        "selected_pages": selected_pages,
                        "selected_page_count": len(selected_pages),
                        "page_start": page_range.start_page,
                        "page_end": page_range.end_page,
                        "page_range_label": page_range.label,
                        "original_source_path": str(path),
                        "split_pdf_path": split_path,
                        "split_pdf_status": split_status,
                        "extraction_status": extraction_status,
                        "requires_ocr": not bool(range_text),
                        "reader_note": "Manual page range selection for teacher-guided LLM candidate extraction.",
                    },
                )
            )
        return records

    @staticmethod
    def _count_pdf_pages(payload: bytes) -> int:
        matches = re.findall(rb"/Type\s*/Page\b", payload)
        return max(len(matches), 1)

    @staticmethod
    def _count_pdf_pages_with_fitz(path: Path) -> int:
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz

            if not hasattr(fitz, "open"):
                return 0
            with fitz.open(path) as document:
                return int(document.page_count)
        except Exception:
            return 0

    @staticmethod
    def _extract_pdf_text_with_fitz(path: Path) -> str:
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz

            if not hasattr(fitz, "open"):
                return ""
            fragments = []
            with fitz.open(path) as document:
                for page in document:
                    fragments.append(page.get_text("text").strip())
            return "\n".join(fragment for fragment in fragments if fragment).strip()
        except Exception:
            return ""

    def _extract_page_range_text_with_fitz(
        self,
        path: Path,
        *,
        selected_pages: list[int],
    ) -> tuple[str, list[dict[str, object]]]:
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz

            if not hasattr(fitz, "open"):
                return "", []
            fragments = []
            blocks: list[dict[str, object]] = []
            with fitz.open(path) as document:
                for page_number in selected_pages:
                    page = document.load_page(page_number - 1)
                    text = page.get_text("text").strip()
                    fragments.append(text)
                    blocks.append(
                        {
                            "block_type": "pdf_page",
                            "source_location": f"page={page_number}",
                            "page_index": page_number,
                            "text": text,
                        }
                    )
            return "\n".join(fragment for fragment in fragments if fragment).strip(), blocks
        except Exception:
            return "", []

    @staticmethod
    def _write_split_pdf(
        path: Path,
        *,
        page_range: PdfPageRange,
        output_dir: Path,
    ) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        label = f"p{page_range.start_page}_p{page_range.end_page}"
        if page_range.label:
            label = f"{label}_{PdfSourceReader._safe_file_part(page_range.label)}"
        output_path = output_dir / f"{path.stem}_{label}.pdf"
        if PdfSourceReader._write_split_pdf_with_pypdf(path, page_range, output_path):
            return str(output_path)
        if PdfSourceReader._write_split_pdf_with_fitz(path, page_range, output_path):
            return str(output_path)
        return ""

    @staticmethod
    def _write_split_pdf_with_pypdf(
        path: Path,
        page_range: PdfPageRange,
        output_path: Path,
    ) -> bool:
        try:
            from pypdf import PdfReader, PdfWriter
        except Exception:
            try:
                from PyPDF2 import PdfReader, PdfWriter
            except Exception:
                return False
        try:
            reader = PdfReader(str(path))
            writer = PdfWriter()
            for page_index in range(page_range.start_page - 1, page_range.end_page):
                writer.add_page(reader.pages[page_index])
            with output_path.open("wb") as stream:
                writer.write(stream)
            return True
        except Exception:
            return False

    @staticmethod
    def _write_split_pdf_with_fitz(
        path: Path,
        page_range: PdfPageRange,
        output_path: Path,
    ) -> bool:
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz

            if not hasattr(fitz, "open"):
                return False
            with fitz.open(path) as source_document:
                with fitz.open() as target_document:
                    target_document.insert_pdf(
                        source_document,
                        from_page=page_range.start_page - 1,
                        to_page=page_range.end_page - 1,
                    )
                    target_document.save(output_path)
            return True
        except Exception:
            return False

    @staticmethod
    def _extract_simple_pdf_text(payload: bytes) -> str:
        fragments = []
        for match in re.findall(rb"\(([^()]*)\)\s*Tj", payload):
            fragments.append(PdfSourceReader._decode_pdf_text(match))
        return "\n".join(fragment for fragment in fragments if fragment).strip()

    @staticmethod
    def _decode_pdf_text(raw_value: bytes) -> str:
        text = raw_value.replace(rb"\(", b"(").replace(rb"\)", b")")
        text = text.replace(rb"\\", b"\\")
        return text.decode("utf-8", errors="ignore") or text.decode(
            "latin-1", errors="ignore"
        )

    @staticmethod
    def _page_range_location(page_range: PdfPageRange) -> str:
        if page_range.start_page == page_range.end_page:
            return f"page={page_range.start_page}"
        return f"page={page_range.start_page}-{page_range.end_page}"

    @staticmethod
    def _range_source_id(base_source_id: str, *, page_range: PdfPageRange, index: int) -> str:
        label = PdfSourceReader._safe_id_part(page_range.label) if page_range.label else ""
        suffix = f"p{page_range.start_page}_p{page_range.end_page}"
        if label:
            suffix = f"{suffix}_{label}"
        return f"{base_source_id}_{suffix or index}"

    @staticmethod
    def _safe_id_part(value: str) -> str:
        safe = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value.strip())
        return safe.strip("_")

    @staticmethod
    def _safe_file_part(value: str) -> str:
        safe = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value.strip())
        return safe.strip("_") or "range"


class ImageSourceReader:
    def read(
        self,
        file_path: str | Path,
        options: DocumentReadOptions,
    ) -> list[SourceRecordDTO]:
        path = Path(file_path)
        payload = path.read_bytes()
        image_format, width, height = self._read_image_info(payload, path.suffix)
        return [
            SourceRecordDTO(
                source_id=options.source_id or path.stem,
                source_type=options.source_type,
                source_path=str(path),
                subject=options.subject,
                grade=options.grade,
                term=options.term,
                raw_text="",
                raw_structure={
                    "image_blocks": [
                        {
                            "block_type": "image_document",
                            "source_location": "image=1",
                            "width": width,
                            "height": height,
                            "ocr_text": "",
                        }
                    ]
                },
                source_format=image_format,
                source_document_type=options.source_document_type,
                education_stage=options.education_stage,
                grade_band=options.grade_band,
                subject_tags=list(options.subject_tags or []),
                source_metadata={
                    "file_name": path.name,
                    "file_size": path.stat().st_size,
                    "width": width,
                    "height": height,
                    "extraction_status": "metadata_only",
                    "requires_ocr": True,
                    "reader_note": "MVP image reader captures metadata; OCR should be connected as a separate module.",
                },
            )
        ]

    def _read_image_info(self, payload: bytes, suffix: str) -> tuple[str, int, int]:
        lowered = suffix.lower()
        if lowered == ".png":
            return "png", *self._read_png_size(payload)
        if lowered in {".jpg", ".jpeg"}:
            return "jpg", *self._read_jpeg_size(payload)
        raise ValueError(f"Unsupported image format: {suffix}")

    @staticmethod
    def _read_png_size(payload: bytes) -> tuple[int, int]:
        if not payload.startswith(b"\x89PNG\r\n\x1a\n") or len(payload) < 24:
            raise ValueError("Invalid PNG file.")
        width = int.from_bytes(payload[16:20], "big")
        height = int.from_bytes(payload[20:24], "big")
        return width, height

    @staticmethod
    def _read_jpeg_size(payload: bytes) -> tuple[int, int]:
        if not payload.startswith(b"\xff\xd8"):
            raise ValueError("Invalid JPG file.")
        index = 2
        while index < len(payload):
            while index < len(payload) and payload[index] == 0xFF:
                index += 1
            if index >= len(payload):
                break
            marker = payload[index]
            index += 1
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(payload):
                break
            length = int.from_bytes(payload[index : index + 2], "big")
            if length < 2 or index + length > len(payload):
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                height = int.from_bytes(payload[index + 3 : index + 5], "big")
                width = int.from_bytes(payload[index + 5 : index + 7], "big")
                return width, height
            index += length
        raise ValueError("Unable to read JPG dimensions.")


class SourceDocumentReader:
    def __init__(self) -> None:
        self._pdf_reader = PdfSourceReader()
        self._image_reader = ImageSourceReader()

    def read(
        self,
        file_path: str | Path,
        options: DocumentReadOptions,
    ) -> list[SourceRecordDTO]:
        suffix = Path(file_path).suffix.lower()
        if suffix == ".pdf":
            return self._pdf_reader.read(file_path, options)
        if suffix in {".png", ".jpg", ".jpeg"}:
            return self._image_reader.read(file_path, options)
        raise ValueError(f"Unsupported document input format: {suffix}")
