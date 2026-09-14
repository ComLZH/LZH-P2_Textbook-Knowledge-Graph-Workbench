from __future__ import annotations

import re
from pathlib import Path

from textbook_builder.pipeline_contracts import (
    BookManifestDTO,
    ChapterManifestDTO,
    PageEvidenceBundleDTO,
)


CHAPTER_PATTERN = re.compile(
    r"第\s*([一二三四五六七八九十百零〇\d０-９]+)\s*章\s*([^\n。；]{0,30})"
)
SECTION_PATTERN = re.compile(
    r"(?<!\d)(\d{1,2}(?:[\.．]\d{1,2}){1,2})\s*([^\n。；]{1,28})"
)


class BookManifestBuilder:
    """Builds a conservative chapter/page index from cached page text."""

    def build(
        self,
        pages: list[PageEvidenceBundleDTO],
        *,
        book_id: str,
        source_path: str,
        subject: str,
        grade: str,
        term: str,
    ) -> BookManifestDTO:
        if not pages:
            return BookManifestDTO(
                book_id=book_id,
                source_path=source_path,
                subject=subject,
                grade=grade,
                term=term,
                status="empty",
            )

        chapter_starts: list[tuple[int, str, str]] = []
        section_titles_by_page: dict[int, list[str]] = {}
        non_content_pages: list[int] = []
        printed_page_map: dict[str, str] = {}
        title = ""
        for page in pages:
            text = page.combined_text().strip()
            if not title and text:
                title = _first_meaningful_line(text)
            if page.printed_page_number:
                printed_page_map[str(page.page_index)] = page.printed_page_number
            head = text[:500]
            chapter_match = None if "目录" in head[:100] else CHAPTER_PATTERN.search(head)
            if chapter_match:
                chapter_number = chapter_match.group(1).strip()
                chapter_title = chapter_match.group(2).strip(" 　:：")
                chapter_id = f"chapter_{_ascii_number(chapter_number) or chapter_number}"
                display_title = f"第{chapter_number}章"
                if chapter_title:
                    display_title = f"{display_title} {chapter_title}"
                if not chapter_starts or chapter_starts[-1][2] != display_title:
                    chapter_starts.append((page.page_index, chapter_id, display_title))
            sections = [
                f"{match.group(1).replace('．', '.')} {match.group(2).strip()}"
                for match in SECTION_PATTERN.finditer(head)
            ]
            if sections:
                section_titles_by_page[page.page_index] = _ordered_unique(sections)
            if not chapter_starts and ("目录" in head or len(re.sub(r"\s+", "", text)) < 40):
                non_content_pages.append(page.page_index)

        chapters: list[ChapterManifestDTO] = []
        last_page = max(page.page_index for page in pages)
        for index, (start_page, chapter_id, chapter_title) in enumerate(chapter_starts):
            end_page = chapter_starts[index + 1][0] - 1 if index + 1 < len(chapter_starts) else last_page
            section_titles: list[str] = []
            for page_index in range(start_page, end_page + 1):
                section_titles.extend(section_titles_by_page.get(page_index, []))
            chapters.append(
                ChapterManifestDTO(
                    chapter_id=chapter_id,
                    title=chapter_title,
                    start_page=start_page,
                    end_page=end_page,
                    section_titles=_ordered_unique(section_titles),
                    status="detected_from_text",
                )
            )

        if not chapters:
            chapters.append(
                ChapterManifestDTO(
                    chapter_id="selected_scope",
                    title="选定页面范围",
                    start_page=min(page.page_index for page in pages),
                    end_page=last_page,
                    section_titles=_ordered_unique(
                        title
                        for titles in section_titles_by_page.values()
                        for title in titles
                    ),
                    status="scope_fallback",
                )
            )
        return BookManifestDTO(
            book_id=book_id,
            source_path=str(Path(source_path)),
            title=title,
            subject=subject,
            grade=grade,
            term=term,
            page_count=len(pages),
            chapters=chapters,
            non_content_pages=non_content_pages,
            printed_page_map=printed_page_map,
            status="detected",
        )


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:80]
    return ""


def _ascii_number(value: str) -> str:
    translation = str.maketrans("０１２３４５６７８９", "0123456789")
    translated = value.translate(translation)
    return translated if translated.isdigit() else ""


def _ordered_unique(values: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:  # type: ignore[union-attr]
        value = str(raw).strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
