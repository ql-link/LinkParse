import re
from typing import Literal

from bs4 import BeautifulSoup, Tag

from .models import TableCellIR, TableIR, TableRenderResult

TableStrategy = Literal["markdown_table", "html_table", "html_fallback"]
COMPLEX_BLOCK_TAGS = {"p", "pre", "blockquote", "ul", "ol", "table"}
MAX_TABLE_SPAN = 256


class HtmlTableProcessor:
    """Build a loss-aware table IR, then select Markdown or embedded HTML."""

    def render(self, table: Tag, *, table_id: str) -> TableRenderResult:
        try:
            table_ir = self._build_ir(table)
            if not table_ir.cells:
                raise ValueError("table contains no readable cells")
            if table_ir.is_complex:
                return TableRenderResult(
                    markdown=self._render_html_table(table, table_ir, table_id),
                    strategy="html_table",
                    table_ir=table_ir,
                    image_count=sum(len(cell.image_sources) for cell in table_ir.cells),
                )
            return TableRenderResult(
                markdown=self._render_markdown_table(table_ir),
                strategy="markdown_table",
                table_ir=table_ir,
            )
        except Exception as exc:
            return TableRenderResult(
                markdown=self._render_html_fallback(table, table_id),
                strategy="html_fallback",
                warning=str(exc),
                image_count=len(table.find_all("img")),
            )

    def _build_ir(self, table: Tag) -> TableIR:
        rows = self._direct_rows(table)
        cells: list[TableCellIR] = []
        occupied: set[tuple[int, int]] = set()
        max_column = 0
        header_rows = 0

        for row_index, row in enumerate(rows):
            row_cells = row.find_all(["th", "td"], recursive=False)
            if row_cells and all(cell.name == "th" for cell in row_cells):
                header_rows += 1
            column_index = 0
            for cell in row_cells:
                while (row_index, column_index) in occupied:
                    column_index += 1
                row_span = self._parse_span(cell.get("rowspan"))
                column_span = self._parse_span(cell.get("colspan"))
                image_sources = [
                    str(image.get("src", ""))
                    for image in cell.find_all("img")
                    if str(image.get("src", ""))
                ]
                nested_tables = cell.find_all("table")
                block_count = len(
                    [
                        child
                        for child in cell.children
                        if isinstance(child, Tag) and child.name in COMPLEX_BLOCK_TAGS
                    ]
                )
                cells.append(
                    TableCellIR(
                        row=row_index,
                        column=column_index,
                        row_span=row_span,
                        column_span=column_span,
                        is_header=cell.name == "th",
                        text=self._cell_text(cell),
                        html="".join(str(child) for child in cell.children).strip(),
                        image_sources=image_sources,
                        nested_table_count=len(nested_tables),
                        block_count=block_count,
                    )
                )
                for row_offset in range(row_span):
                    for column_offset in range(column_span):
                        occupied.add((row_index + row_offset, column_index + column_offset))
                column_index += column_span
                max_column = max(max_column, column_index)

        row_count = max((row + 1 for row, _ in occupied), default=len(rows))
        reasons: set[str] = set()
        if header_rows > 1:
            reasons.add("multi_header")
        for cell in cells:
            if cell.row_span > 1:
                reasons.add("rowspan")
            if cell.column_span > 1:
                reasons.add("colspan")
            if cell.nested_table_count:
                reasons.add("nested_table")
            if cell.image_sources:
                reasons.add("image_cell")
            if cell.block_count > 1:
                reasons.add("multi_block_cell")

        return TableIR(
            caption=self._table_caption(table),
            row_count=row_count,
            column_count=max_column,
            cells=cells,
            complexity_reasons=sorted(reasons),
        )

    def _render_markdown_table(self, table_ir: TableIR) -> str:
        matrix = [["" for _ in range(table_ir.column_count)] for _ in range(table_ir.row_count)]
        for cell in table_ir.cells:
            matrix[cell.row][cell.column] = cell.text
        if not matrix:
            raise ValueError("table matrix is empty")

        headers = [
            value or f"列{column_index + 1}"
            for column_index, value in enumerate(matrix[0])
        ]
        lines = [
            "| " + " | ".join(self._escape_table_cell(cell) for cell in headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        lines.extend(
            "| " + " | ".join(self._escape_table_cell(cell) for cell in row) + " |"
            for row in matrix[1:]
        )
        return "\n".join(lines)

    def _render_html_table(self, table: Tag, table_ir: TableIR, table_id: str) -> str:
        reasons = ",".join(table_ir.complexity_reasons)
        start_marker = (
            f'<!-- LINKPARSE_TABLE_START id="{table_id}" format="html" '
            f'reasons="{reasons}" -->'
        )
        end_marker = f'<!-- LINKPARSE_TABLE_END id="{table_id}" -->'
        return f"{start_marker}\n{str(table)}\n{end_marker}"

    @staticmethod
    def _render_html_fallback(table: Tag, table_id: str) -> str:
        start_marker = (
            f'<!-- LINKPARSE_TABLE_START id="{table_id}" format="html_fallback" -->'
        )
        end_marker = f'<!-- LINKPARSE_TABLE_END id="{table_id}" -->'
        return f"{start_marker}\n{str(table)}\n{end_marker}"

    @staticmethod
    def _direct_rows(table: Tag) -> list[Tag]:
        rows: list[Tag] = []
        for child in table.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "tr":
                rows.append(child)
            elif child.name in {"thead", "tbody", "tfoot"}:
                rows.extend(child.find_all("tr", recursive=False))
        return rows

    def _cell_text(self, cell: Tag) -> str:
        parsed = BeautifulSoup(str(cell), "lxml")
        copied_cell = parsed.find(["td", "th"])
        if copied_cell is None:
            return self._clean_text(cell.get_text(" ", strip=True))
        for nested_table in copied_cell.find_all("table"):
            nested_table.decompose()
        return self._clean_text(copied_cell.get_text(" ", strip=True))

    def _table_caption(self, table: Tag) -> str:
        caption = table.find("caption", recursive=False)
        return self._clean_text(caption.get_text(" ", strip=True)) if caption else ""

    @staticmethod
    def _parse_span(raw_value: object) -> int:
        try:
            value = max(1, int(str(raw_value or "1")))
        except ValueError:
            return 1
        if value > MAX_TABLE_SPAN:
            raise ValueError(f"table cell span exceeds {MAX_TABLE_SPAN}")
        return value

    def _escape_table_cell(self, text: str) -> str:
        return self._clean_text(text).replace("|", "\\|").replace("\n", " ")

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"\s+", " ", text or "").strip()
