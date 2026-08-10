from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any

PAGE_MARKER_RE = re.compile(r"<!--\s*ODL_PAGE:(\d+)\s*-->")
MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


@dataclass(slots=True)
class _RawTable:
    page: int | None
    rows: list[list[str]]
    source_format: str


class _HtmlTableParser(HTMLParser):
    def __init__(self, page: int | None) -> None:
        super().__init__(convert_charrefs=True)
        self.page = page
        self.tables: list[_RawTable] = []
        self._depth = 0
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._rows = []
        elif self._depth == 1 and tag == "tr":
            self._row = []
        elif self._depth == 1 and tag in {"td", "th"}:
            self._cell = []
            values = dict(attrs)
            try:
                self._colspan = max(1, int(values.get("colspan") or 1))
            except ValueError:
                self._colspan = 1
        elif self._cell is not None and tag == "br":
            self._cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._depth == 1 and tag in {"td", "th"} and self._cell is not None:
            value = _normalize_cell("".join(self._cell))
            if self._row is not None:
                self._row.extend([value] * self._colspan)
            self._cell = None
            self._colspan = 1
        elif self._depth == 1 and tag == "tr":
            if self._row and any(self._row):
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            if self._depth == 1 and self._rows:
                self.tables.append(_RawTable(self.page, self._rows, "html"))
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _normalize_cell(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _split_markdown_row(line: str) -> list[str]:
    value = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", value)
    return [_normalize_cell(cell.replace(r"\|", "|")) for cell in cells]


def _page_sections(markdown: str) -> list[tuple[int | None, str]]:
    matches = list(PAGE_MARKER_RE.finditer(markdown or ""))
    if not matches:
        return [(None, markdown or "")]
    sections: list[tuple[int | None, str]] = []
    if markdown[: matches[0].start()].strip():
        sections.append((None, markdown[: matches[0].start()]))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections.append((int(match.group(1)), markdown[match.end() : end]))
    return sections


def _extract_markdown_tables(page: int | None, content: str) -> list[_RawTable]:
    lines = content.splitlines()
    tables: list[_RawTable] = []
    index = 0
    while index + 1 < len(lines):
        if "|" not in lines[index] or not MARKDOWN_TABLE_SEPARATOR_RE.match(lines[index + 1]):
            index += 1
            continue
        rows = [_split_markdown_row(lines[index])]
        index += 2
        while index < len(lines) and "|" in lines[index] and lines[index].strip():
            rows.append(_split_markdown_row(lines[index]))
            index += 1
        width = max((len(row) for row in rows), default=0)
        if width:
            rows = [row + [""] * (width - len(row)) for row in rows]
            tables.append(_RawTable(page, rows, "markdown"))
    return tables


def _extract_html_tables(page: int | None, content: str) -> list[_RawTable]:
    parser = _HtmlTableParser(page)
    parser.feed(content)
    return parser.tables


def analyze_pdf_markdown(markdown: str, page_count: int) -> dict[str, Any]:
    """Build JSON-safe page provenance and table metadata from ODL Markdown."""

    markers = [int(value) for value in PAGE_MARKER_RE.findall(markdown or "")]
    warnings: list[str] = []
    expected = list(range(1, page_count + 1))
    if markers != expected:
        warnings.append("PAGE_MARKERS_INCOMPLETE")

    raw_tables: list[_RawTable] = []
    for page, content in _page_sections(markdown):
        html_spans = [
            match.span() for match in re.finditer(r"<table\b.*?</table>", content, re.I | re.S)
        ]
        markdown_only = content
        for start, end in reversed(html_spans):
            markdown_only = markdown_only[:start] + "\n" + markdown_only[end:]
        raw_tables.extend(_extract_markdown_tables(page, markdown_only))
        raw_tables.extend(_extract_html_tables(page, content))

    tables = []
    for index, table in enumerate(raw_tables, start=1):
        width = max((len(row) for row in table.rows), default=0)
        rows = [row + [""] * (width - len(row)) for row in table.rows]
        tables.append(
            {
                "table_id": f"table-{index:04d}",
                "source_page": table.page,
                "source_format": table.source_format,
                "row_count": len(rows),
                "column_count": width,
                "header": rows[0] if rows else [],
                "rows": rows,
            }
        )
    if any(table["source_page"] is None for table in tables):
        warnings.append("TABLE_PAGE_UNMAPPED")
    return {
        "schema_version": 1,
        "page_markers": markers,
        "page_provenance_complete": markers == expected,
        "table_count": len(tables),
        "tables": tables,
        "warnings": list(dict.fromkeys(warnings)),
    }


def image_page_map(markdown: str) -> dict[str, int]:
    """Resolve image pages only from page markers, never filename conventions."""

    pattern = re.compile(
        r"!\[[^\]]*\]\(\s*(?:<(?P<angle>[^>]+)>|(?P<plain>[^\s)]+))",
        re.I,
    )
    pages: dict[str, int] = {}
    current_page: int | None = None
    for line in (markdown or "").splitlines():
        marker = PAGE_MARKER_RE.search(line)
        if marker:
            current_page = int(marker.group(1))
        if current_page is None:
            continue
        for match in pattern.finditer(line):
            source = (match.group("angle") or match.group("plain") or "").lstrip("./")
            if source:
                pages.setdefault(source, current_page)
    return pages
