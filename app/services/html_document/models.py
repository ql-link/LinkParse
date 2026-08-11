from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ImageRewriteResult:
    markdown: str
    source: str
    warning: str | None = None


@dataclass(slots=True)
class TableCellIR:
    row: int
    column: int
    row_span: int
    column_span: int
    is_header: bool
    text: str
    html: str
    image_sources: list[str] = field(default_factory=list)
    links: list[list[str]] = field(default_factory=list)
    nested_tables: list[TableIR] = field(default_factory=list)
    block_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "row": self.row,
            "column": self.column,
            "row_span": self.row_span,
            "column_span": self.column_span,
            "is_header": self.is_header,
            "text": self.text,
            "html": self.html,
            "image_sources": list(self.image_sources),
            "links": [list(link) for link in self.links],
            "nested_tables": [table.to_dict() for table in self.nested_tables],
            "block_count": self.block_count,
        }


@dataclass(slots=True)
class TableIR:
    caption: str
    row_count: int
    column_count: int
    cells: list[TableCellIR]
    header_row_count: int = 0
    complexity_reasons: list[str] = field(default_factory=list)

    @property
    def is_complex(self) -> bool:
        return bool(self.complexity_reasons)

    @property
    def image_count(self) -> int:
        return sum(
            len(cell.image_sources)
            + sum(nested_table.image_count for nested_table in cell.nested_tables)
            for cell in self.cells
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption": self.caption,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "header_row_count": self.header_row_count,
            "complexity_reasons": list(self.complexity_reasons),
            "cells": [cell.to_dict() for cell in self.cells],
        }


@dataclass(slots=True)
class TableRenderResult:
    markdown: str
    strategy: str
    table_ir: TableIR | None = None
    warning: str | None = None
    image_count: int = 0
    warnings: list[str] = field(default_factory=list)
