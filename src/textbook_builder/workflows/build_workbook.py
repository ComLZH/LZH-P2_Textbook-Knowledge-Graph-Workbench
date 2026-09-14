from __future__ import annotations

from pathlib import Path

from ..contracts import DraftKnowledgeItemDTO, FormalGraphWorkbookDTO
from ..exporters import FormalGraphWorkbookBuilder, ReviewHtmlExporter, XlsxWorkbookExporter
from ..extractors import StructuredTextbookExtractor
from ..normalizers import DraftKnowledgeNormalizer
from ..readers import DraftWorkbookReader, StructuredTextbookJsonReader


class TextbookDefinitionBuildWorkflow:
    def __init__(
        self,
        *,
        reader: StructuredTextbookJsonReader | None = None,
        extractor: StructuredTextbookExtractor | None = None,
        normalizer: DraftKnowledgeNormalizer | None = None,
        formal_builder: FormalGraphWorkbookBuilder | None = None,
        exporter: XlsxWorkbookExporter | None = None,
        review_exporter: ReviewHtmlExporter | None = None,
        draft_reader: DraftWorkbookReader | None = None,
    ) -> None:
        self._reader = reader or StructuredTextbookJsonReader()
        self._extractor = extractor or StructuredTextbookExtractor()
        self._normalizer = normalizer or DraftKnowledgeNormalizer()
        self._formal_builder = formal_builder or FormalGraphWorkbookBuilder()
        self._exporter = exporter or XlsxWorkbookExporter()
        self._review_exporter = review_exporter or ReviewHtmlExporter()
        self._draft_reader = draft_reader or DraftWorkbookReader()

    def build_draft(self, source_path: str | Path) -> list[DraftKnowledgeItemDTO]:
        records = self._reader.read(source_path)
        drafts = self._extractor.extract(records)
        return self._normalizer.normalize(drafts)

    def build_formal(
        self,
        source_path: str | Path,
        *,
        graph_id: str,
        version: str = "v1",
        textbook_version: str = "default_mvp_v1",
    ) -> FormalGraphWorkbookDTO:
        drafts = self.build_draft(source_path)
        return self._formal_builder.build(
            drafts,
            graph_id=graph_id,
            version=version,
            textbook_version=textbook_version,
        )

    def build_formal_from_draft(
        self,
        draft_path: str | Path,
        *,
        graph_id: str,
        version: str = "v1",
        textbook_version: str = "default_mvp_v1",
    ) -> FormalGraphWorkbookDTO:
        drafts = self._draft_reader.read(draft_path)
        return self._formal_builder.build(
            drafts,
            graph_id=graph_id,
            version=version,
            textbook_version=textbook_version,
        )

    def export_all(
        self,
        source_path: str | Path,
        *,
        draft_path: str | Path,
        formal_path: str | Path,
        graph_id: str,
        review_path: str | Path | None = None,
        version: str = "v1",
        textbook_version: str = "default_mvp_v1",
    ) -> FormalGraphWorkbookDTO:
        drafts = self.build_draft(source_path)
        self._exporter.export_draft(drafts, draft_path)
        formal = self._formal_builder.build(
            drafts,
            graph_id=graph_id,
            version=version,
            textbook_version=textbook_version,
        )
        self._exporter.export_formal(formal, formal_path)
        if review_path is not None:
            review_workbook = self._formal_builder.build_review_workbook(
                drafts,
                graph_id=graph_id,
                version=version,
                textbook_version=textbook_version,
            )
            self._review_exporter.export_review(
                drafts=drafts,
                workbook=review_workbook,
                file_path=review_path,
            )
        return formal

    def export_reviewed_draft(
        self,
        draft_path: str | Path,
        *,
        formal_path: str | Path,
        graph_id: str,
        review_path: str | Path | None = None,
        version: str = "v1",
        textbook_version: str = "default_mvp_v1",
    ) -> FormalGraphWorkbookDTO:
        drafts = self._draft_reader.read(draft_path)
        formal = self._formal_builder.build(
            drafts,
            graph_id=graph_id,
            version=version,
            textbook_version=textbook_version,
        )
        self._exporter.export_formal(formal, formal_path)
        if review_path is not None:
            review_workbook = self._formal_builder.build_review_workbook(
                drafts,
                graph_id=graph_id,
                version=version,
                textbook_version=textbook_version,
            )
            self._review_exporter.export_review(
                drafts=drafts,
                workbook=review_workbook,
                file_path=review_path,
            )
        return formal

    def export_review_only(
        self,
        drafts: list[DraftKnowledgeItemDTO],
        *,
        review_path: str | Path,
        graph_id: str,
        version: str = "v1",
        textbook_version: str = "default_mvp_v1",
    ) -> FormalGraphWorkbookDTO:
        review_workbook = self._formal_builder.build_review_workbook(
            drafts,
            graph_id=graph_id,
            version=version,
            textbook_version=textbook_version,
        )
        self._review_exporter.export_review(
            drafts=drafts,
            workbook=review_workbook,
            file_path=review_path,
        )
        return review_workbook
