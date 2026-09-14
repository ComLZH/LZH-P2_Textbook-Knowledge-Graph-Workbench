from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from textbook_builder.pipeline_contracts import OcrTextBlockDTO, PageEvidenceBundleDTO


OCR_PIPELINE_VERSION = "p2-page-evidence-v1"
DEFAULT_DET_MODEL_NAME = "PP-OCRv6_medium_det"
DEFAULT_REC_MODEL_NAME = "PP-OCRv6_small_rec"
DEFAULT_DET_MODEL_FOLDER = "PP-OCRv6_medium_det_infer"
DEFAULT_REC_MODEL_FOLDER = "PP-OCRv6_small_rec_infer"


class PageEvidenceCache:
    """Stores OCR/native-text page evidence without changing source files."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def make_key(*, image_sha256: str, detector: str, recognizer: str) -> str:
        material = f"{OCR_PIPELINE_VERSION}|{detector}|{recognizer}|{image_sha256}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def load(self, key: str) -> PageEvidenceBundleDTO | None:
        path = self.root / f"{key}.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            blocks = [OcrTextBlockDTO(**item) for item in payload.pop("ocr_blocks", [])]
            payload["ocr_blocks"] = blocks
            return PageEvidenceBundleDTO(**payload)
        except (OSError, ValueError, TypeError):
            return None

    def save(self, key: str, bundle: PageEvidenceBundleDTO) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{key}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(asdict(bundle), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return path


class PaddleOcrPageAnalyzer:
    """Lazy PaddleOCR adapter backed by the system-level shared OCR models."""

    def __init__(
        self,
        *,
        cache: PageEvidenceCache,
        shared_model_root: Path | None = None,
        predictor_factory: Callable[[Path, Path], Any] | None = None,
        settings: dict[str, str] | None = None,
    ) -> None:
        self.cache = cache
        self.shared_model_root = shared_model_root or self._default_shared_model_root()
        self._predictor_factory = predictor_factory
        self._predictor: Any | None = None
        self._settings = dict(settings or {})

    @staticmethod
    def _default_shared_model_root() -> Path:
        system_root = Path(__file__).resolve().parents[4]
        return system_root / "shared_resources" / "models" / "ocr"

    @property
    def detector_name(self) -> str:
        return self._setting("TEXTBOOK_BUILDER_OCR_DET_MODEL", DEFAULT_DET_MODEL_NAME)

    @property
    def recognizer_name(self) -> str:
        return self._setting("TEXTBOOK_BUILDER_OCR_REC_MODEL", DEFAULT_REC_MODEL_NAME)

    def analyze(
        self,
        image_path: Path,
        *,
        source_id: str,
        page_index: int,
        source_path: Path,
        printed_page_number: str = "",
    ) -> PageEvidenceBundleDTO:
        image_path = Path(image_path)
        image_sha256 = _sha256_file(image_path)
        cache_key = self.cache.make_key(
            image_sha256=image_sha256,
            detector=self.detector_name,
            recognizer=self.recognizer_name,
        )
        cached = self.cache.load(cache_key)
        if cached is not None:
            return replace(
                cached,
                source_id=source_id,
                page_id=_page_id(source_id, page_index),
                page_index=page_index,
                source_path=str(source_path),
                image_path=str(image_path),
                printed_page_number=printed_page_number or cached.printed_page_number,
                cache_hit=True,
            )

        try:
            predictor = self._get_predictor()
            raw_results = predictor.predict(str(image_path))
            blocks = tuple(_extract_text_blocks(raw_results))
            source_text = "\n".join(block.text for block in blocks if block.text.strip()).strip()
            status = "ok" if source_text else "needs_review"
            error_message = "" if source_text else "OCR未识别到有效文本。"
        except Exception as exc:  # Paddle/model failures must become visible evidence state.
            blocks = ()
            source_text = ""
            status = "unavailable"
            error_message = f"OCR不可用：{type(exc).__name__}: {exc}"

        bundle = PageEvidenceBundleDTO(
            source_id=source_id,
            page_id=_page_id(source_id, page_index),
            page_index=page_index,
            source_path=str(source_path),
            image_path=str(image_path),
            image_sha256=image_sha256,
            printed_page_number=printed_page_number,
            source_text=source_text,
            ocr_blocks=list(blocks),
            ocr_status=status,
            ocr_model=f"{self.detector_name}+{self.recognizer_name}",
            cache_hit=False,
            requires_visual_review=status != "ok",
            review_reason=error_message,
            metadata={"ocr_pipeline_version": OCR_PIPELINE_VERSION},
        )
        self.cache.save(cache_key, bundle)
        return bundle

    def _get_predictor(self) -> Any:
        if self._predictor is not None:
            return self._predictor
        det_dir = self._resolve_model_dir(
            env_name="TEXTBOOK_BUILDER_OCR_DET_MODEL_DIR",
            default_folder=DEFAULT_DET_MODEL_FOLDER,
        )
        rec_dir = self._resolve_model_dir(
            env_name="TEXTBOOK_BUILDER_OCR_REC_MODEL_DIR",
            default_folder=DEFAULT_REC_MODEL_FOLDER,
        )
        if self._predictor_factory is not None:
            self._predictor = self._predictor_factory(det_dir, rec_dir)
            return self._predictor

        from paddleocr import PaddleOCR

        self._predictor = PaddleOCR(
            text_detection_model_name=self.detector_name,
            text_detection_model_dir=str(det_dir),
            text_recognition_model_name=self.recognizer_name,
            text_recognition_model_dir=str(rec_dir),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device=self._setting("TEXTBOOK_BUILDER_OCR_DEVICE", "cpu"),
            lang=self._setting("TEXTBOOK_BUILDER_OCR_LANG", "ch"),
            text_det_limit_side_len=960,
            text_det_limit_type="max",
            text_det_box_thresh=0.5,
        )
        return self._predictor

    def _resolve_model_dir(self, *, env_name: str, default_folder: str) -> Path:
        explicit = self._setting(env_name, "")
        source = Path(explicit) if explicit else self.shared_model_root / default_folder
        if not source.is_dir():
            raise FileNotFoundError(f"共享OCR模型目录不存在：{source}")
        if _is_ascii_path(source):
            return source

        cache_root_text = self._setting("TEXTBOOK_BUILDER_OCR_MODEL_CACHE_DIR", "")
        cache_root = (
            Path(cache_root_text)
            if cache_root_text
            else Path(tempfile.gettempdir()) / "p2_textbook_ocr_models"
        )
        target = cache_root / default_folder
        _copy_model_if_needed(source, target)
        return target

    def _setting(self, key: str, default: str) -> str:
        configured = self._settings.get(key, "").strip()
        if configured:
            return configured
        return os.environ.get(key, default).strip()


class PageEvidenceService:
    """Builds a page-indexed evidence bundle for PDF or image input."""

    def __init__(
        self,
        *,
        analyzer: PaddleOcrPageAnalyzer,
        render_root: Path,
        min_native_text_chars: int = 24,
    ) -> None:
        self.analyzer = analyzer
        self.render_root = Path(render_root)
        self.min_native_text_chars = min_native_text_chars

    def build(
        self,
        source_path: Path,
        *,
        source_id: str,
        selected_pages: Iterable[int] | None = None,
        ocr_enabled: bool = True,
        force_ocr: bool = False,
    ) -> tuple[PageEvidenceBundleDTO, ...]:
        source_path = Path(source_path)
        suffix = source_path.suffix.lower()
        if suffix == ".pdf":
            return self._build_pdf(
                source_path,
                source_id=source_id,
                selected_pages=selected_pages,
                ocr_enabled=ocr_enabled,
                force_ocr=force_ocr,
            )
        if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
            return (
                self._build_image(
                    source_path,
                    source_id=source_id,
                    page_index=1,
                    ocr_enabled=ocr_enabled,
                ),
            )
        raise ValueError(f"暂不支持的页面证据输入格式：{suffix or '<无扩展名>'}")

    def _build_pdf(
        self,
        source_path: Path,
        *,
        source_id: str,
        selected_pages: Iterable[int] | None,
        ocr_enabled: bool,
        force_ocr: bool,
    ) -> tuple[PageEvidenceBundleDTO, ...]:
        try:
            import pymupdf as fitz
        except ImportError:  # PyMuPDF releases before the pymupdf import alias.
            import fitz

        document = fitz.open(source_path)
        try:
            pages = _normalize_page_selection(selected_pages, document.page_count)
            source_digest = _sha256_file(source_path)[:12]
            output_dir = self.render_root / f"{_safe_filename(source_path.stem)}_{source_digest}"
            output_dir.mkdir(parents=True, exist_ok=True)
            bundles: list[PageEvidenceBundleDTO] = []
            for page_index in pages:
                page = document.load_page(page_index - 1)
                image_path = output_dir / f"page_{page_index:04d}.png"
                if not image_path.is_file():
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                    pixmap.save(image_path)

                native_blocks = _native_pdf_blocks(page)
                native_text = "\n".join(block.text for block in native_blocks).strip()
                printed_page_number = _infer_printed_page_number(native_blocks)
                if not force_ocr and len(_compact_text(native_text)) >= self.min_native_text_chars:
                    bundles.append(
                        PageEvidenceBundleDTO(
                            source_id=source_id,
                            page_id=_page_id(source_id, page_index),
                            page_index=page_index,
                            source_path=str(source_path),
                            image_path=str(image_path),
                            image_sha256=_sha256_file(image_path),
                            printed_page_number=printed_page_number,
                            source_text=native_text,
                            ocr_blocks=list(native_blocks),
                            ocr_status="native_text",
                            ocr_model="pymupdf-native-text",
                            cache_hit=False,
                            requires_visual_review=False,
                            review_reason="",
                            metadata={"ocr_pipeline_version": OCR_PIPELINE_VERSION},
                        )
                    )
                elif ocr_enabled:
                    bundles.append(
                        self.analyzer.analyze(
                            image_path,
                            source_id=source_id,
                            page_index=page_index,
                            source_path=source_path,
                            printed_page_number=printed_page_number,
                        )
                    )
                else:
                    bundles.append(
                        _unavailable_bundle(
                            source_path=source_path,
                            image_path=image_path,
                            source_id=source_id,
                            page_index=page_index,
                            printed_page_number=printed_page_number,
                            message="页面无足够原生文本，且本次已关闭OCR。",
                        )
                    )
            return tuple(bundles)
        finally:
            document.close()

    def _build_image(
        self,
        source_path: Path,
        *,
        source_id: str,
        page_index: int,
        ocr_enabled: bool,
    ) -> PageEvidenceBundleDTO:
        if ocr_enabled:
            return self.analyzer.analyze(
                source_path,
                source_id=source_id,
                page_index=page_index,
                source_path=source_path,
            )
        return _unavailable_bundle(
            source_path=source_path,
            image_path=source_path,
            source_id=source_id,
            page_index=page_index,
            printed_page_number="",
            message="本次已关闭OCR，图片输入无法建立文本证据。",
        )


def _native_pdf_blocks(page: Any) -> tuple[OcrTextBlockDTO, ...]:
    blocks: list[OcrTextBlockDTO] = []
    for index, item in enumerate(page.get_text("blocks")):
        if len(item) < 5:
            continue
        x0, y0, x1, y1, text = item[:5]
        cleaned = str(text).strip()
        if not cleaned:
            continue
        blocks.append(
            OcrTextBlockDTO(
                block_id=f"native-{index + 1}",
                text=cleaned,
                confidence=1.0,
                bbox={
                    "x": float(x0),
                    "y": float(y0),
                    "width": max(0.0, float(x1) - float(x0)),
                    "height": max(0.0, float(y1) - float(y0)),
                },
                polygon=(),
            )
        )
    return tuple(blocks)


def _extract_text_blocks(raw_results: Any) -> list[OcrTextBlockDTO]:
    if raw_results is None:
        return []
    items = list(raw_results) if not isinstance(raw_results, dict) else [raw_results]
    blocks: list[OcrTextBlockDTO] = []
    for result in items:
        payload = getattr(result, "json", result)
        if callable(payload):
            payload = payload()
        if not isinstance(payload, dict):
            continue
        if isinstance(payload.get("res"), dict):
            payload = payload["res"]
        texts = payload.get("rec_texts") or payload.get("texts") or []
        scores = payload.get("rec_scores") or payload.get("scores") or []
        polygons = payload.get("dt_polys") or payload.get("rec_polys") or payload.get("polys") or []
        for index, text in enumerate(texts):
            cleaned = str(text).strip()
            if not cleaned:
                continue
            score = _safe_float(scores[index] if index < len(scores) else 0.0)
            polygon = _normalize_polygon(polygons[index] if index < len(polygons) else [])
            blocks.append(
                OcrTextBlockDTO(
                    block_id=f"ocr-{len(blocks) + 1}",
                    text=cleaned,
                    confidence=score,
                    bbox=_polygon_bbox(polygon),
                    polygon=polygon,
                )
            )
    return blocks


def _unavailable_bundle(
    *,
    source_path: Path,
    image_path: Path,
    source_id: str,
    page_index: int,
    printed_page_number: str,
    message: str,
) -> PageEvidenceBundleDTO:
    return PageEvidenceBundleDTO(
        source_id=source_id,
        page_id=_page_id(source_id, page_index),
        page_index=page_index,
        source_path=str(source_path),
        rendered_image_path=str(image_path),
        image_sha256=_sha256_file(image_path),
        printed_page_number=printed_page_number,
        source_text="",
        ocr_blocks=(),
        ocr_status="unavailable",
        ocr_model="",
        cache_hit=False,
        requires_visual_review=True,
        review_reason=message,
        metadata={"ocr_pipeline_version": OCR_PIPELINE_VERSION},
    )


def _normalize_page_selection(selected_pages: Iterable[int] | None, page_count: int) -> list[int]:
    if selected_pages is None:
        return list(range(1, page_count + 1))
    result = sorted({int(page) for page in selected_pages})
    if not result:
        raise ValueError("至少选择一个页面。")
    invalid = [page for page in result if page < 1 or page > page_count]
    if invalid:
        raise ValueError(f"页码超出范围：{invalid}，PDF共{page_count}页。")
    return result


def _infer_printed_page_number(blocks: tuple[OcrTextBlockDTO, ...]) -> str:
    candidates: list[tuple[float, str]] = []
    for block in blocks:
        stripped = block.text.strip()
        if re.fullmatch(r"(?:第\s*)?\d{1,4}(?:\s*页)?", stripped):
            candidates.append((float(block.bbox.get("y", 0.0)), stripped))
    if not candidates:
        return ""
    return max(candidates, key=lambda item: item[0])[1]


def _normalize_polygon(value: Any) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    try:
        for point in value:
            if len(point) >= 2:
                points.append((_safe_float(point[0]), _safe_float(point[1])))
    except TypeError:
        return ()
    return tuple(points)


def _polygon_bbox(polygon: tuple[tuple[float, float], ...]) -> dict[str, float]:
    if not polygon:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return {
        "x": min(xs),
        "y": min(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }


def _copy_model_if_needed(source: Path, target: Path) -> None:
    if target.is_dir() and (target / "inference.json").is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _page_id(source_id: str, page_index: int) -> str:
    return f"{source_id}:page:{page_index}"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return cleaned or "textbook"


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False
