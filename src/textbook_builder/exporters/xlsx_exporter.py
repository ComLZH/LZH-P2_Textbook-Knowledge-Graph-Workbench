from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from ..contracts import DraftKnowledgeItemDTO, FormalGraphWorkbookDTO


class XlsxWorkbookExporter:
    def export_draft(self, drafts: list[DraftKnowledgeItemDTO], file_path: str | Path) -> None:
        columns = [
            "draft_id",
            "subject",
            "grade",
            "term",
            "chapter",
            "section",
            "candidate_display_name",
            "candidate_node_name",
            "candidate_node_id",
            "candidate_parent_name",
            "candidate_parent_node_id",
            "candidate_prerequisites",
            "candidate_relations",
            "knowledge_type",
            "cognitive_level",
            "education_stage",
            "grade_band",
            "subject_tags",
            "source_id",
            "source_path",
            "source_format",
            "source_document_type",
            "source_text",
            "source_location",
            "evidence_anchors",
            "confidence",
            "reasoning_summary",
            "extractor_source",
            "review_status",
            "review_notes",
        ]
        rows = [columns]
        for draft in drafts:
            payload = asdict(draft)
            rows.append([self._cell_value(payload[column]) for column in columns])
        self._write_workbook(Path(file_path), {"draft": rows})

    def export_formal(
        self, workbook: FormalGraphWorkbookDTO, file_path: str | Path
    ) -> None:
        graph_rows = [
            ["graph_id", "subject", "grade_scope", "term_scope", "version"],
            [
                workbook.metadata.graph_id,
                workbook.metadata.subject,
                json.dumps(workbook.metadata.grade_scope, ensure_ascii=False),
                json.dumps(workbook.metadata.term_scope, ensure_ascii=False),
                workbook.metadata.version,
            ],
        ]
        node_columns = [
            "node_id",
            "subject",
            "grade",
            "term",
            "chapter",
            "display_name",
            "node_name",
            "parent_node_id",
            "prerequisite_nodes",
            "node_type",
            "knowledge_type",
            "cognitive_level",
            "education_stage",
            "grade_band",
            "subject_tags",
            "textbook_version",
            "chapter_aliases",
            "source_locations",
            "extraction_tags",
            "version",
        ]
        node_rows = [node_columns]
        for node in workbook.nodes:
            payload = asdict(node)
            node_rows.append([self._cell_value(payload[column]) for column in node_columns])
        edge_columns = [
            "source_node_id",
            "target_node_id",
            "relation_type",
            "confidence",
            "relation_evidence",
            "relation_source",
            "review_status",
        ]
        edge_rows = [edge_columns]
        for edge in workbook.edges:
            payload = asdict(edge)
            edge_rows.append([self._cell_value(payload[column]) for column in edge_columns])
        self._write_workbook(
            Path(file_path),
            {"graph": graph_rows, "nodes": node_rows, "edges": edge_rows},
        )

    def export_rows(
        self,
        sheet_rows: dict[str, list[list[str]]],
        file_path: str | Path,
        *,
        hidden_sheet_names: set[str] | None = None,
        atomic: bool = False,
    ) -> None:
        target = Path(file_path)
        if not atomic:
            self._write_workbook(
                target,
                sheet_rows,
                hidden_sheet_names=hidden_sheet_names,
            )
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            self._write_workbook(
                temporary,
                sheet_rows,
                hidden_sheet_names=hidden_sheet_names,
            )
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _cell_value(value: object) -> str:
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _write_workbook(
        self,
        file_path: Path,
        sheet_rows: dict[str, list[list[str]]],
        *,
        hidden_sheet_names: set[str] | None = None,
    ) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        sheet_names = list(sheet_rows.keys())
        hidden_sheet_names = set(hidden_sheet_names or set())
        with ZipFile(file_path, "w") as archive:
            archive.writestr("[Content_Types].xml", self._build_content_types_xml(sheet_names))
            archive.writestr("_rels/.rels", self._build_root_rels_xml())
            archive.writestr(
                "xl/workbook.xml",
                self._build_workbook_xml(sheet_names, hidden_sheet_names),
            )
            archive.writestr("xl/_rels/workbook.xml.rels", self._build_workbook_rels_xml(sheet_names))
            for index, sheet_name in enumerate(sheet_names, start=1):
                archive.writestr(
                    f"xl/worksheets/sheet{index}.xml",
                    self._build_sheet_xml(sheet_rows[sheet_name]),
                )

    @staticmethod
    def _build_workbook_xml(
        sheet_names: list[str],
        hidden_sheet_names: set[str] | None = None,
    ) -> str:
        hidden_sheet_names = set(hidden_sheet_names or set())
        sheets = []
        for index, name in enumerate(sheet_names, start=1):
            state = ' state="hidden"' if name in hidden_sheet_names else ""
            sheets.append(
                f'<sheet name="{name}" sheetId="{index}" r:id="rId{index}"{state}/>'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(sheets)}</sheets>"
            "</workbook>"
        )

    @staticmethod
    def _build_workbook_rels_xml(sheet_names: list[str]) -> str:
        rels = []
        for index, _ in enumerate(sheet_names, start=1):
            rels.append(
                '<Relationship '
                f'Id="rId{index}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{index}.xml"/>'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(rels)}"
            "</Relationships>"
        )

    @staticmethod
    def _build_content_types_xml(sheet_names: list[str]) -> str:
        overrides = [
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        ]
        for index, _ in enumerate(sheet_names, start=1):
            overrides.append(
                f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f"{''.join(overrides)}"
            "</Types>"
        )

    @staticmethod
    def _build_root_rels_xml() -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/>'
            "</Relationships>"
        )

    @staticmethod
    def _build_sheet_xml(rows: list[list[str]]) -> str:
        row_xml_fragments = []
        for row_index, row in enumerate(rows, start=1):
            cells = []
            for column_index, value in enumerate(row, start=1):
                cell_ref = f"{_column_name(column_index)}{row_index}"
                escaped = (
                    str(value)
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                cells.append(
                    f'<c r="{cell_ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'
                )
            row_xml_fragments.append(f'<row r="{row_index}">{"".join(cells)}</row>')
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(row_xml_fragments)}</sheetData>'
            "</worksheet>"
        )


def _column_name(index: int) -> str:
    result = []
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result.append(chr(ord("A") + remainder))
    return "".join(reversed(result))
