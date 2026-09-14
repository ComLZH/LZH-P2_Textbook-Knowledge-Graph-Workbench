from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "doc_rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


@dataclass(slots=True)
class SimpleXlsxReaderConfig:
    json_columns: set[str] = field(default_factory=set)


class SimpleXlsxWorkbookReader:
    def __init__(self, config: SimpleXlsxReaderConfig | None = None) -> None:
        self._config = config or SimpleXlsxReaderConfig()

    def read_sheets(
        self, file_path: str | Path, sheet_names: list[str]
    ) -> dict[str, list[dict[str, object]]]:
        workbook_path = Path(file_path)
        with ZipFile(workbook_path) as archive:
            shared_strings = self._load_shared_strings(archive)
            sheet_paths = self._load_sheet_paths(archive)
            return {
                name: self._read_sheet_rows(
                    archive,
                    sheet_paths[name],
                    shared_strings,
                )
                for name in sheet_names
                if name in sheet_paths
            }

    def _read_sheet_rows(
        self,
        archive: ZipFile,
        sheet_path: str,
        shared_strings: list[str],
    ) -> list[dict[str, object]]:
        root = ET.fromstring(archive.read(sheet_path))
        rows = root.findall(".//main:sheetData/main:row", _NS)
        if not rows:
            return []
        header_values = self._read_row_values(rows[0], shared_strings)
        headers = [str(value).strip() for value in header_values if str(value).strip()]
        records: list[dict[str, object]] = []
        for row in rows[1:]:
            row_values = self._read_row_values(row, shared_strings)
            if not any(str(value).strip() for value in row_values):
                continue
            record: dict[str, object] = {}
            for index, header in enumerate(headers):
                raw_value = row_values[index] if index < len(row_values) else ""
                parsed_value = self._coerce_value(header, raw_value)
                # JSON "" is a meaningful explicit empty value and must not be
                # conflated with an absent cell during three-way import.
                if parsed_value == "" and not (
                    header in self._config.json_columns and str(raw_value).strip()
                ):
                    continue
                record[header] = parsed_value
            if record:
                records.append(record)
        return records

    def _read_row_values(
        self, row: ET.Element, shared_strings: list[str]
    ) -> list[str]:
        values: dict[int, str] = {}
        for cell in row.findall("main:c", _NS):
            ref = cell.attrib.get("r", "")
            index = self._column_index(ref)
            values[index] = self._read_cell_value(cell, shared_strings)
        if not values:
            return []
        max_index = max(values)
        return [values.get(index, "") for index in range(max_index + 1)]

    def _read_cell_value(self, cell: ET.Element, shared_strings: list[str]) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(
                node.text or "" for node in cell.findall("main:is/main:t", _NS)
            )
        value_node = cell.find("main:v", _NS)
        if value_node is None or value_node.text is None:
            return ""
        raw_value = value_node.text
        if cell_type == "s":
            return shared_strings[int(raw_value)]
        return raw_value

    def _load_shared_strings(self, archive: ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        values: list[str] = []
        for item in root.findall("main:si", _NS):
            fragments = [node.text or "" for node in item.findall(".//main:t", _NS)]
            values.append("".join(fragments))
        return values

    def _load_sheet_paths(self, archive: ZipFile) -> dict[str, str]:
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rel_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rel_root.findall("rel:Relationship", _NS)
        }
        sheet_paths: dict[str, str] = {}
        for sheet in workbook_root.findall("main:sheets/main:sheet", _NS):
            name = sheet.attrib["name"]
            rel_id = sheet.attrib[f"{{{_NS['doc_rel']}}}id"]
            target = rel_map[rel_id].lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            sheet_paths[name] = target
        return sheet_paths

    @staticmethod
    def _column_index(cell_ref: str) -> int:
        letters = []
        for char in cell_ref:
            if char.isalpha():
                letters.append(char.upper())
            else:
                break
        index = 0
        for char in letters:
            index = index * 26 + (ord(char) - ord("A") + 1)
        return max(index - 1, 0)

    def _coerce_value(self, header: str, raw_value: object) -> object:
        if not isinstance(raw_value, str):
            return raw_value
        value = raw_value.strip()
        if not value:
            return ""
        if header in self._config.json_columns:
            return json.loads(value)
        return value
