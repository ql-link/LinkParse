from __future__ import annotations

import html
import math
import re
import statistics
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Any

PAGE_MARKER_RE = re.compile(r"<!--\s*ODL_PAGE:(\d+)\s*-->")
MARKDOWN_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")

# OpenDataLoader has emitted a few different JSON layouts over its releases
# (``kids``, ``children`` and ``blocks`` are all in the wild).  The layout
# contract deliberately normalises those variants here instead of making the
# API consumer know which ODL version produced the document.
_CHILD_KEYS = (
    "children",
    "kids",
    "pages",
    "blocks",
    "lines",
    "items",
    "content",
)
_TEXT_KEYS = ("text", "content", "value", "markdown", "str")
_PAGE_KEYS = ("page", "page number", "page_number", "pageNumber", "source_page")
_BBOX_KEYS = ("bbox", "bounding box", "bounding_box", "coordinates", "rect")
_ROLE_KEYS = ("role", "layout_role", "block_type", "semantic_type", "kind", "type")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$")
_ORDERED_RE = re.compile(
    r"^\s*(?P<number>\d{1,5})(?:(?P<punctuation>[.．、)）])|(?P<space>[ \t]+))"
    r"\s*(?P<body>.*)$"
)
_BULLET_RE = re.compile(r"^\s*([-*+•▪◦●○])\s*(\S.*)$")

# Row grouping, heading inference and column ambiguity checks compare layout
# lines with one another. Bound their input before entering those quadratic
# passes so an opt-in layout request cannot monopolise the shared service.
MAX_LAYOUT_SOURCE_BLOCKS = 2000
MAX_LAYOUT_LINE_CHARS = 20_000
MAX_LAYOUT_TOTAL_TEXT_CHARS = 1_000_000
MAX_LAYOUT_ODL_NODES = 10_000
MAX_LAYOUT_ROLE_RECORDS = 4_000
MAX_LAYOUT_HINT_RECORDS = 4_000
MAX_LAYOUT_HINT_CHARS = 1_000_000
MAX_LAYOUT_CONTAINMENT_CANDIDATES = 64
_FONT_FLAG_BOLD = 1 << 4


@dataclass(slots=True)
class _LayoutLine:
    page: int
    text: str
    bbox: tuple[float, float, float, float] | None
    page_width: float
    page_height: float
    confidence: float | None = None
    font_size: float | None = None
    font_flags: int | None = None
    source: str = "visual_inference"
    role_hint: str | None = None
    heading_level_hint: int | None = None
    source_index: int = 0
    row_id: str | None = None
    continuation_of: str | None = None
    join_with: str = ""


@dataclass(slots=True)
class _OdlText:
    text: str
    page: int | None
    role: str | None
    heading_level: int | None
    key: str = ""


@dataclass(slots=True)
class _RoleLookup:
    records: list[_OdlText]
    exact: dict[tuple[int | None, str], deque[int]]
    by_page: dict[int | None, list[int]]


@dataclass(slots=True)
class _MarkdownHint:
    role: str
    level: int | None
    text: str
    key: str


@dataclass(slots=True)
class _MarkdownLookup:
    records: list[_MarkdownHint]
    exact: dict[tuple[int, str], deque[int]]
    by_page: dict[int, list[int]]


def _first_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group())
    return None


def _text_value(item: dict[str, Any]) -> str | None:
    for key in _TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _page_value(item: dict[str, Any], inherited: int | None = None) -> int | None:
    for key in _PAGE_KEYS:
        if key in item:
            value = _first_int(item.get(key))
            if value is not None:
                return value
    return inherited


def _bbox_value(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    for key in _BBOX_KEYS:
        value = item.get(key)
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            continue
        try:
            values = tuple(float(part) for part in value)
        except (TypeError, ValueError):
            continue
        if all(math.isfinite(part) for part in values):
            x0, y0, x1, y1 = values
            return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
    return None


def _normalise_heading_level(value: object) -> int | None:
    level = _first_int(value)
    if level is None:
        return None
    return max(1, min(2, level))


def _normalise_role(value: object) -> tuple[str | None, int | None]:
    if not isinstance(value, str):
        return None, None
    lowered = value.strip().casefold().replace("-", "_").replace(" ", "_")
    level = None
    heading_match = re.search(r"(?:heading|title|h)([12])$", lowered)
    if heading_match:
        level = int(heading_match.group(1))
    if lowered in {"h1", "heading1", "heading_1", "title", "document_title"}:
        return "heading", level or 1
    if lowered in {"h2", "heading2", "heading_2"}:
        return "heading", level or 2
    if lowered in {"heading", "section_heading", "section_title", "subtitle"}:
        return "heading", level or 1
    if lowered in {
        "ordered_list_item",
        "numbered_list_item",
        "ordered_list",
        "numbered_list",
        "ol",
        "ordered",
    }:
        return "ordered_list_item", None
    if lowered in {
        "bullet_list_item",
        "unordered_list_item",
        "bulleted_list_item",
        "unordered_list",
        "bulleted_list",
        "ul",
        "bullet",
    }:
        return "bullet_list_item", None
    if lowered in {"list_item", "list"}:
        return "list_item", None
    if lowered in {"paragraph", "body", "text", "line", "span", "content"}:
        return "paragraph", None
    return None, None


def _walk_odl_text(value: object, inherited_page: int | None = None) -> tuple[list[_OdlText], bool]:
    """Collect leaf-ish ODL text records without trusting its shape.

    Parent records often carry an aggregate ``text`` as well as child spans.
    A parent is retained only when it has no textual children, preventing
    duplicate lines while still supporting the compact flat JSON form.
    """

    result: list[_OdlText] = []
    stack: list[tuple[object, int | None]] = [(value, inherited_page)]
    visited_nodes = 0
    text_chars = 0
    exceeded = False
    while stack:
        item, page = stack.pop()
        visited_nodes += 1
        if visited_nodes > MAX_LAYOUT_ODL_NODES:
            exceeded = True
            break
        if isinstance(item, list):
            stack.extend((child, page) for child in reversed(item))
            continue
        if not isinstance(item, dict):
            continue
        current_page = _page_value(item, page)
        child_values: list[object] = []
        for key in _CHILD_KEYS:
            child = item.get(key)
            if isinstance(child, (dict, list)):
                child_values.append(child)
        child_text = any(
            isinstance(grandchild, dict) and _text_value(grandchild) is not None
            for child in child_values
            for grandchild in ([child] if isinstance(child, dict) else child)
        )
        text = _text_value(item)
        if text and not child_text:
            text_chars += len(text)
            if (
                len(text) > MAX_LAYOUT_LINE_CHARS
                or text_chars > MAX_LAYOUT_TOTAL_TEXT_CHARS
                or len(result) >= MAX_LAYOUT_ROLE_RECORDS
            ):
                exceeded = True
                break
            role = None
            heading_level = None
            for key in _ROLE_KEYS:
                role, heading_level = _normalise_role(item.get(key))
                if role:
                    break
            if role == "heading" and heading_level is None:
                heading_level = _normalise_heading_level(item.get("level")) or 1
            result.append(
                _OdlText(
                    text,
                    current_page,
                    role,
                    heading_level,
                    _text_key(text),
                )
            )
        stack.extend((child, current_page) for child in reversed(child_values))

    return result, exceeded


def _text_key(value: str) -> str:
    value = html.unescape(unicodedata.normalize("NFKC", value or ""))
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"^\s*#{1,6}\s*", "", value)
    value = re.sub(r"^\s*(?:\d+)[.．、)）]\s*", "", value)
    value = re.sub(r"^\s*[-*+•▪◦●○]\s*", "", value)
    return "".join(value.split()).casefold()


def _role_records(value: object) -> tuple[_RoleLookup, bool]:
    walked, exceeded = _walk_odl_text(value)
    records = [record for record in walked if record.text.strip() and record.key]
    exact: dict[tuple[int | None, str], deque[int]] = defaultdict(deque)
    by_page: dict[int | None, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        exact[(record.page, record.key)].append(index)
        by_page[record.page].append(index)
    return _RoleLookup(records, dict(exact), dict(by_page)), exceeded


def _role_for_text(
    text: str,
    page: int,
    lookup: _RoleLookup,
    used: set[int],
) -> tuple[str | None, int | None, str | None]:
    key = _text_key(text)
    if not key:
        return None, None, None
    for record_page in (page, None):
        candidates = lookup.exact.get((record_page, key))
        if candidates is None:
            continue
        while candidates and candidates[0] in used:
            candidates.popleft()
        if candidates:
            index = candidates.popleft()
            record = lookup.records[index]
            used.add(index)
            return record.role, record.heading_level, "opendataloader" if record.role else None

    # A PyMuPDF line can be a continuation of a longer ODL span. Keep this
    # compatibility fallback bounded: exact keys above are O(1), while an
    # unbounded contains scan creates a source-lines x ODL-records CPU spike.
    checked = 0
    for record_page in (page, None):
        for index in lookup.by_page.get(record_page, []):
            if index in used:
                continue
            checked += 1
            if checked > MAX_LAYOUT_CONTAINMENT_CANDIDATES:
                break
            record = lookup.records[index]
            if len(key) >= 8 and (key in record.key or record.key in key):
                used.add(index)
                return (
                    record.role,
                    record.heading_level,
                    "opendataloader" if record.role else None,
                )
        if checked > MAX_LAYOUT_CONTAINMENT_CANDIDATES:
            break
    if checked == 0:
        return None, None, None
    return None, None, None


def _markdown_line_hints(markdown: str) -> tuple[_MarkdownLookup, bool]:
    records: list[_MarkdownHint] = []
    exact: dict[tuple[int, str], deque[int]] = defaultdict(deque)
    by_page: dict[int, list[int]] = defaultdict(list)
    if len(markdown) > MAX_LAYOUT_HINT_CHARS:
        return _MarkdownLookup([], {}, {}), True
    text_chars = 0
    for page, content in _page_sections(markdown):
        if page is None:
            continue
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("<!--"):
                continue
            text_chars += len(line)
            if (
                len(line) > MAX_LAYOUT_LINE_CHARS
                or text_chars > MAX_LAYOUT_HINT_CHARS
                or len(records) >= MAX_LAYOUT_HINT_RECORDS
            ):
                return _MarkdownLookup(records, dict(exact), dict(by_page)), True
            heading = _HEADING_RE.match(line)
            if heading:
                role = "heading"
                level = min(2, len(heading.group(1)))
                hint_text = heading.group(2)
            else:
                role = _looks_like_list(line)[0] or "paragraph"
                level = None
                hint_text = line
            key = _text_key(hint_text)
            if not key:
                continue
            index = len(records)
            records.append(_MarkdownHint(role, level, hint_text, key))
            exact[(page, key)].append(index)
            by_page[page].append(index)
    return _MarkdownLookup(records, dict(exact), dict(by_page)), False


def _line_hint_from_markdown(
    text: str,
    page: int,
    hints: _MarkdownLookup,
    used: dict[int, set[int]],
) -> tuple[str | None, int | None, str | None]:
    key = _text_key(text)
    consumed = used.setdefault(page, set())
    exact = hints.exact.get((page, key))
    if exact is not None:
        while exact and exact[0] in consumed:
            exact.popleft()
        if exact:
            index = exact.popleft()
            consumed.add(index)
            hint = hints.records[index]
            return hint.role, hint.level, "opendataloader"
    checked = 0
    for index in hints.by_page.get(page, []):
        if index in consumed:
            continue
        checked += 1
        if checked > MAX_LAYOUT_CONTAINMENT_CANDIDATES:
            break
        hint = hints.records[index]
        if len(key) >= 8 and (key in hint.key or hint.key in key):
            consumed.add(index)
            return hint.role, hint.level, "opendataloader"
    return None, None, None


def _extract_pymupdf_lines(page: Any, page_number: int) -> list[_LayoutLine]:
    lines: list[_LayoutLine] = []
    try:
        payload = page.get_text("dict")
    except Exception:
        return lines
    width = float(getattr(page.rect, "width", 1.0) or 1.0)
    height = float(getattr(page.rect, "height", 1.0) or 1.0)
    for block in payload.get("blocks", []) if isinstance(payload, dict) else []:
        if not isinstance(block, dict) or block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            if not isinstance(line, dict):
                continue
            spans = line.get("spans", [])
            text = "".join(
                str(span.get("text", "")) for span in spans if isinstance(span, dict)
            ).strip()
            if not text:
                continue
            bbox = _bbox_value(line)
            sizes = [
                float(span["size"])
                for span in spans
                if isinstance(span, dict) and isinstance(span.get("size"), (int, float))
            ]
            flags = [
                int(span["flags"])
                for span in spans
                if isinstance(span, dict) and isinstance(span.get("flags"), int)
            ]
            combined_flags = None
            for flag in flags:
                combined_flags = (combined_flags or 0) | flag
            lines.append(
                _LayoutLine(
                    page=page_number,
                    text=text,
                    bbox=bbox,
                    page_width=width,
                    page_height=height,
                    font_size=max(sizes) if sizes else None,
                    font_flags=combined_flags,
                )
            )
    return lines


def _page_dimensions(document_page: Any) -> tuple[float, float]:
    rect = getattr(document_page, "rect", None)
    return (
        float(getattr(rect, "width", 1.0) or 1.0),
        float(getattr(rect, "height", 1.0) or 1.0),
    )


def _ocr_lines(page: dict[str, Any], page_number: int) -> tuple[list[_LayoutLine], list[str]]:
    width = float(page.get("width") or 0.0)
    height = float(page.get("height") or 0.0)
    warnings: list[str] = []
    if width <= 0 or height <= 0:
        warnings.append(f"OCR_PAGE_SIZE_MISSING:page={page_number}")
        width = height = 1.0
    raw_blocks = page.get("blocks")
    if not isinstance(raw_blocks, list):
        raw_blocks = []
        warnings.append(f"OCR_BLOCKS_MISSING:page={page_number}")
    lines: list[_LayoutLine] = []
    for raw in raw_blocks:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        bbox = _bbox_value(raw)
        if bbox is None:
            warnings.append(f"OCR_BBOX_MISSING:page={page_number}")
        confidence = raw.get("confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(float(confidence))
            or not 0 <= float(confidence) <= 1
        ):
            if confidence is not None:
                warnings.append(f"OCR_CONFIDENCE_INVALID:page={page_number}")
            confidence = None
        lines.append(
            _LayoutLine(
                page=page_number,
                text=text,
                bbox=bbox,
                page_width=width,
                page_height=height,
                confidence=round(float(confidence), 6) if confidence is not None else None,
                source="ocr",
            )
        )
    return lines, warnings


def _bbox_sort_key(line: _LayoutLine) -> tuple[int, float, float]:
    if line.bbox is None:
        return line.page, float("inf"), float("inf")
    return line.page, line.bbox[1], line.bbox[0]


def _line_geometry(line: _LayoutLine) -> tuple[float, float, float, float] | None:
    if line.bbox is None:
        return None
    x0, y0, x1, y1 = line.bbox
    return x0, y0, x1, y1


def _relative_geometry(line: _LayoutLine) -> tuple[float, float, float, float] | None:
    if line.bbox is None or line.page_width <= 0 or line.page_height <= 0:
        return None
    x0, y0, x1, y1 = line.bbox
    return (
        x0 / line.page_width,
        y0 / line.page_height,
        x1 / line.page_width,
        y1 / line.page_height,
    )


def _height(line: _LayoutLine) -> float:
    if line.bbox is None:
        return 0.0
    return max(0.0, line.bbox[3] - line.bbox[1])


def _relative_height(line: _LayoutLine) -> float:
    geometry = _relative_geometry(line)
    if geometry is None:
        return 0.0
    return max(0.0, geometry[3] - geometry[1])


def _vertical_gap(previous: _LayoutLine, current: _LayoutLine) -> float:
    if previous.bbox is None or current.bbox is None:
        return float("inf")
    return current.bbox[1] - previous.bbox[3]


def _relative_vertical_gap(previous: _LayoutLine, current: _LayoutLine) -> float:
    previous_geometry = _relative_geometry(previous)
    current_geometry = _relative_geometry(current)
    if previous_geometry is None or current_geometry is None:
        return float("inf")
    return current_geometry[1] - previous_geometry[3]


def _same_row(left: _LayoutLine, right: _LayoutLine, median_height: float) -> bool:
    if left.page != right.page:
        return False
    left_geometry = _relative_geometry(left)
    right_geometry = _relative_geometry(right)
    if left_geometry is None or right_geometry is None:
        return False
    lx0, ly0, lx1, ly1 = left_geometry
    rx0, ry0, rx1, ry1 = right_geometry
    vertical_overlap = max(0.0, min(ly1, ry1) - max(ly0, ry0))
    min_height = max(1e-9, min(ly1 - ly0, ry1 - ry0))
    horizontal_overlap = max(0.0, min(lx1, rx1) - max(lx0, rx0))
    # Full-width text lines that touch by a few pixels are adjacent lines,
    # not a row.  Same-row blocks must be mostly side-by-side.
    if horizontal_overlap > min(max(1e-9, lx1 - lx0), max(1e-9, rx1 - rx0)) * 0.18:
        return False
    return (
        vertical_overlap / min_height >= 0.24
        and abs((ly0 + ly1) - (ry0 + ry1)) <= median_height * 0.45 + 1e-9
    )


def _has_ambiguous_columns(
    lines: list[_LayoutLine],
    median_height_by_page: dict[int, float],
) -> bool:
    """Detect a stable, independent two-column reading order.

    A single visual row such as ``school | date`` is explicitly excluded.
    Repeated, reasonably balanced left/right text bands are intentionally a
    fail-closed signal even when their baselines align: geometry alone cannot
    prove whether they should be read by row or down each column first.
    """

    by_page: dict[int, list[_LayoutLine]] = {}
    for line in lines:
        geometry = _relative_geometry(line)
        if geometry is None or _relative_height(line) <= 0:
            continue
        x0, _y0, x1, _y1 = geometry
        # Full-width lines (headings, summaries, and ordinary one-column
        # paragraphs) do not establish a column.  Keep narrower blocks as
        # candidates for stable left/right bands.
        if x1 - x0 > 0.72:
            continue
        by_page.setdefault(line.page, []).append(line)

    for page, page_lines in by_page.items():
        if len(page_lines) < 4:
            continue
        centers = sorted(
            (geometry[0] + geometry[2]) / 2
            for line in page_lines
            if (geometry := _relative_geometry(line)) is not None
        )
        if len(centers) < 4:
            continue
        gaps = [right - left for left, right in zip(centers, centers[1:], strict=False)]
        split_index = max(range(len(gaps)), key=gaps.__getitem__)
        split_gap = gaps[split_index]
        median_height = median_height_by_page.get(page, 0.0)
        if split_gap < max(0.12, median_height * 2.0):
            continue
        split = (centers[split_index] + centers[split_index + 1]) / 2
        left = [
            line
            for line in page_lines
            if (geometry := _relative_geometry(line)) is not None
            and (geometry[0] + geometry[2]) / 2 <= split
        ]
        right = [
            line
            for line in page_lines
            if (geometry := _relative_geometry(line)) is not None
            and (geometry[0] + geometry[2]) / 2 > split
        ]
        if len(left) < 2 or len(right) < 2:
            continue
        left_geometry = [box for line in left if (box := _relative_geometry(line))]
        right_geometry = [box for line in right if (box := _relative_geometry(line))]
        left_x = [box[0] for box in left_geometry]
        right_x = [box[0] for box in right_geometry]
        if not left_x or not right_x:
            continue
        horizontal_gap = min(right_x) - max(box[2] for box in left_geometry)
        if horizontal_gap < max(median_height * 1.5, 0.02):
            continue
        left_width = statistics.median(box[2] - box[0] for box in left_geometry)
        right_width = statistics.median(box[2] - box[0] for box in right_geometry)
        width_ratio = min(left_width, right_width) / max(left_width, right_width)
        # Repeated school/company + narrow date pairs are row metadata, not
        # independent columns. Two reasonably balanced text bands are treated
        # fail-closed because baseline alignment cannot prove reading order.
        if width_ratio < 0.55:
            continue
        left_y = [value for box in left_geometry for value in box[1::2]]
        right_y = [value for box in right_geometry for value in box[1::2]]
        overlap = min(max(left_y), max(right_y)) - max(min(left_y), min(right_y))
        shorter_span = min(max(left_y) - min(left_y), max(right_y) - min(right_y))
        if shorter_span <= 0 or overlap / shorter_span < 0.35:
            continue
        return True
    return False


def _is_cjk(value: str) -> bool:
    value = value.strip()
    if not value:
        return False
    character = value[-1] if value else ""
    codepoint = ord(character)
    return (
        0x2E80 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
    )


def _join_for_continuation(previous: str, current: str) -> str:
    if _is_cjk(previous) and current and (ord(current[0]) >= 0x2E80):
        return ""
    return " "


def _starts_labeled_line(text: str) -> bool:
    """Return whether a line begins with a short ``label:`` prefix.

    This is a visual/text-shape boundary only.  It deliberately does not
    name resume-specific labels, so an arbitrary document using labels gets
    the same protection from accidental paragraph adhesion.
    """

    return bool(re.match(r"^\s*(?!\d+[.．、)）])[^:：\n]{1,20}[:：]", text))


def _looks_like_list(text: str) -> tuple[str | None, str | None]:
    visible = html.unescape(unicodedata.normalize("NFKC", text or ""))
    ordered = _ORDERED_RE.match(visible)
    if ordered and ordered.group("body").strip():
        marker = visible[: ordered.start("body")]
        body = ordered.group("body").strip()
        # Dates, versions and decimal values are not list markers.  A list
        # marker either uses punctuation or a short numeric index plus space.
        if not (ordered.group("punctuation") == "." and body[:1].isdigit()) and (
            ordered.group("punctuation") is not None or len(ordered.group("number")) <= 3
        ):
            return "ordered_list_item", marker
    bullet = _BULLET_RE.match(visible)
    if bullet:
        return "bullet_list_item", bullet.group(1)
    return None, None


def _normalise_list_text(text: str, role: str) -> str:
    """Keep list markers/indices while normalising spacing for CommonMark."""

    text = html.unescape(unicodedata.normalize("NFKC", text or ""))
    if role == "ordered_list_item":
        match = _ORDERED_RE.match(text)
        if match and match.group("body").strip():
            return f"{int(match.group('number'))}. {match.group('body').strip()}"
    if role == "bullet_list_item":
        match = re.match(r"^\s*([-*+•▪◦●○])\s*(.+)$", text)
        if match:
            return f"- {match.group(2).strip()}"
    return text.strip()


def _infer_heading_candidates(lines: list[_LayoutLine]) -> set[int]:
    if not lines:
        return set()
    pages = {line.page for line in lines}
    median_height_by_page: dict[int, float] = {}
    median_font_size_by_page: dict[int, float | None] = {}
    median_gap_by_page: dict[int, float] = {}
    for page in pages:
        page_lines = [line for line in lines if line.page == page]
        heights = [_relative_height(line) for line in page_lines if _relative_height(line) > 0]
        median_height = statistics.median(heights) if heights else 1.0
        median_height_by_page[page] = median_height
        font_sizes = [
            line.font_size
            for line in page_lines
            if line.font_size is not None and line.font_size > 0
        ]
        median_font_size_by_page[page] = statistics.median(font_sizes) if font_sizes else None
        gaps = [
            _relative_vertical_gap(previous, current)
            for previous, current in zip(page_lines, page_lines[1:], strict=False)
            if _relative_vertical_gap(previous, current) >= 0
        ]
        median_gap_by_page[page] = statistics.median(gaps) if gaps else median_height * 0.4
    candidates: set[int] = set()
    for index, line in enumerate(lines):
        if line.bbox is None or _looks_like_list(line.text)[0]:
            continue
        x0, y0, x1, y1 = line.bbox
        width = max(1.0, line.page_width)
        short_left = x0 / width <= 0.2 and (x1 - x0) / width <= 0.48
        if not short_left or len(_text_key(line.text)) > 48:
            continue
        previous = lines[index - 1] if index else None
        following = lines[index + 1] if index + 1 < len(lines) else None
        median_height = median_height_by_page[line.page]
        median_font_size = median_font_size_by_page[line.page]
        median_gap = median_gap_by_page[line.page]
        before_gap = (
            _relative_vertical_gap(previous, line)
            if previous is not None and previous.page == line.page
            else float("inf")
        )
        after_gap = (
            _relative_vertical_gap(line, following)
            if following is not None and following.page == line.page
            else float("inf")
        )
        paired = any(
            _same_row(line, other, median_height)
            for other in lines
            if other is not line and other.page == line.page
        )
        if paired:
            continue
        visual_size = (
            median_font_size is not None
            and line.font_size is not None
            and line.font_size >= median_font_size * 1.15
        )
        visual_height = _relative_height(line) >= median_height * 1.12
        visual_bold = bool(line.font_flags is not None and line.font_flags & _FONT_FLAG_BOLD)
        # Section headings are commonly separated from the preceding or
        # following line by a larger vertical gap, but not necessarily both
        # (especially at the top or bottom of a page).  Treat a gap as visual
        # evidence only when a neighbouring line exists; an infinite page
        # boundary gap alone must not turn every first/last line into a title.
        gap_threshold = max(median_gap * 1.25, median_height * 0.25)
        whitespace_before = previous is not None and before_gap >= gap_threshold
        whitespace_after = following is not None and after_gap >= gap_threshold
        # A gap on both sides is strong evidence even when OCR gives every
        # line the same height.  A larger-than-body bbox/font can disambiguate
        # the common case where only the gap before a section title is large.
        whitespace = whitespace_before and whitespace_after
        # A distinctly taller OCR bbox or larger text run is itself strong
        # visual evidence once the line has passed the generic short/left and
        # non-row filters above.  This matters for headings whose only
        # spacing cue is the small margin below them.
        page_boundary = previous is None or previous.page != line.page
        bold_boundary = visual_bold and (page_boundary or whitespace_before or whitespace_after)
        visual_boundary = visual_height or visual_size or bold_boundary
        if visual_boundary or whitespace:
            candidates.add(index)
    return candidates


def _normalise_bbox(
    line: _LayoutLine,
) -> tuple[list[float], bool]:
    if line.bbox is None or line.page_width <= 0 or line.page_height <= 0:
        return [0.0, 0.0, 1.0, 1.0], False
    x0, y0, x1, y1 = line.bbox
    values = [
        x0 / line.page_width,
        y0 / line.page_height,
        x1 / line.page_width,
        y1 / line.page_height,
    ]
    valid = all(0.0 <= value <= 1.0 for value in values) and x0 < x1 and y0 < y1
    normalised = [round(min(1.0, max(0.0, value)), 6) for value in values]
    # A box entirely outside the page can collapse to zero width/height after
    # clamping.  Keep the response contract valid while marking the layout as
    # degraded instead of letting response-model validation turn it into a
    # server error.
    if normalised[0] >= normalised[2] or normalised[1] >= normalised[3]:
        return [0.0, 0.0, 1.0, 1.0], False
    return normalised, valid


def _wire_row_is_valid(members: list[dict[str, Any]], median_height: float) -> bool:
    """Recheck a row using the exact rounded geometry sent on the wire.

    Initial grouping uses source precision. Six-decimal bbox normalization can
    move a value across a ratio threshold, so the final DTO must be checked
    with the same predicates used by the LinkCV consumer.
    """

    for left, right in zip(members, members[1:], strict=False):
        if left["source_page"] != right["source_page"]:
            return False
        left_x0, left_y0, left_x1, left_y1 = left["bbox"]
        right_x0, right_y0, right_x1, right_y1 = right["bbox"]
        minimum_height = min(left_y1 - left_y0, right_y1 - right_y0)
        if minimum_height <= 0:
            return False
        vertical_overlap = max(0.0, min(left_y1, right_y1) - max(left_y0, right_y0))
        if vertical_overlap / minimum_height < 0.24:
            return False
        center_delta = abs((left_y0 + left_y1) - (right_y0 + right_y1))
        if center_delta > median_height * 0.45 + 1e-6:
            return False
        if right_x0 <= left_x0:
            return False
        horizontal_overlap = max(0.0, min(left_x1, right_x1) - right_x0)
        minimum_width = min(left_x1 - left_x0, right_x1 - right_x0)
        if minimum_width <= 0 or horizontal_overlap > minimum_width * 0.18:
            return False
    return True


def _wire_continuation_is_valid(
    blocks: list[dict[str, Any]],
    index: int,
    median_height_by_page: dict[int, float],
) -> bool:
    block = blocks[index]
    target_id = block.get("continuation_of")
    if target_id is None:
        return True
    if index == 0:
        return False
    target = blocks[index - 1]
    if target_id != target["block_id"]:
        return False
    if (
        target["role"] != block["role"]
        or target["heading_level"] != block["heading_level"]
        or target["role"] == "heading"
        or target["row_id"] is not None
        or block["row_id"] is not None
    ):
        return False
    target_x0, target_y0, _target_x1, target_y1 = target["bbox"]
    block_x0, block_y0, _block_x1, _block_y1 = block["bbox"]
    if target["source_page"] != block["source_page"]:
        return (
            block["source_page"] == target["source_page"] + 1
            and target["role"] in {"ordered_list_item", "bullet_list_item"}
            and target_y1 >= 0.88
            and block_y0 <= 0.12
            and abs(block_x0 - target_x0) <= 0.04 + 1e-6
        )
    target_height = target_y1 - target_y0
    allowed_gap = (
        max(
            target_height,
            median_height_by_page[block["source_page"]],
        )
        * 0.55
    )
    return (
        block_y0 >= target_y0
        and block_y0 - target_y1 <= allowed_gap + 1e-6
        and abs(target_x0 - block_x0) <= 0.04 + 1e-6
    )


def _line_id(line: _LayoutLine, page_ordinals: dict[int, int]) -> str:
    ordinal = page_ordinals.setdefault(line.page, 0) + 1
    page_ordinals[line.page] = ordinal
    return f"page-{line.page}-line-{ordinal}"


def _build_markdown_from_layout(blocks: list[dict[str, Any]]) -> str:
    # Collapse continuation chains first.  This mirrors the LinkCV consumer's
    # renderer exactly: physical OCR lines remain in the contract, while the
    # visible Markdown contains each logical paragraph/list item once.
    by_id = {str(block.get("block_id")): block for block in blocks}
    root_for: dict[str, str] = {}
    text_for: dict[str, str] = {}
    root_blocks: list[dict[str, Any]] = []
    for block in blocks:
        block_id = str(block.get("block_id"))
        text = str(block.get("text") or "").strip()
        target = block.get("continuation_of")
        if target:
            target_id = str(target)
            root = root_for.get(target_id)
            if root is None or target_id not in by_id:
                # The contract validator will reject this response.  Keep the
                # builder deterministic for diagnostics instead of guessing.
                root = target_id
            root_for[block_id] = root
            text_for[root] = text_for.get(root, "") + (block.get("join_with") or "") + text
            continue
        root_for[block_id] = block_id
        text_for[block_id] = text
        root_blocks.append(block)

    roots_by_row: dict[str, list[dict[str, Any]]] = {}
    for block in root_blocks:
        row_id = block.get("row_id")
        if row_id is not None:
            roots_by_row.setdefault(str(row_id), []).append(block)

    units: list[tuple[int, str, int | None, str, str | None]] = []
    emitted_rows: set[str] = set()
    for block in root_blocks:
        block_id = str(block.get("block_id"))
        row_id = block.get("row_id")
        if row_id is not None:
            row_key = str(row_id)
            if row_key in emitted_rows:
                continue
            emitted_rows.add(row_key)
            members = roots_by_row[row_key]
            units.append(
                (
                    int(members[0].get("source_order", 0)),
                    str(members[0].get("role") or "paragraph"),
                    members[0].get("heading_level"),
                    " ｜ ".join(
                        text_for.get(str(member.get("block_id")), "") for member in members
                    ),
                    row_key,
                )
            )
            continue
        units.append(
            (
                int(block.get("source_order", 0)),
                str(block.get("role") or "paragraph"),
                block.get("heading_level"),
                text_for.get(block_id, ""),
                None,
            )
        )

    ordered_marker = re.compile(r"^\s*(\d+)(?:[.．、)）])\s*(.*)$")
    bullet_marker = re.compile(r"^\s*[-*+•▪◦●○]\s*(.*)$")
    segments: list[tuple[str, list[str]]] = []
    previous_list_role: str | None = None
    ordered_number = 0
    for _order, role, level, text, _row_id in sorted(units, key=lambda item: item[0]):
        text = text.strip()
        if not text:
            continue
        if role == "heading":
            heading = _HEADING_RE.match(text)
            body = heading.group(2).strip() if heading else text
            heading_level = max(1, min(2, int(level or 1)))
            segments.append(("text", [f"{'#' * heading_level} {body}"]))
            previous_list_role = None
            continue
        if role == "ordered_list_item":
            match = ordered_marker.match(text)
            if match:
                ordered_number = int(match.group(1))
                body = match.group(2).strip()
            else:
                ordered_number = ordered_number + 1 if previous_list_role == role else 1
                body = text
            line = f"{ordered_number}. {body}"
            if segments and segments[-1][0] == role:
                segments[-1][1].append(line)
            else:
                segments.append((role, [line]))
            previous_list_role = role
            continue
        if role == "bullet_list_item":
            match = bullet_marker.match(text)
            body = match.group(1).strip() if match else text
            line = f"- {body}"
            if segments and segments[-1][0] == role:
                segments[-1][1].append(line)
            else:
                segments.append((role, [line]))
            previous_list_role = role
            continue
        segments.append(("text", [text]))
        previous_list_role = None
    return "\n\n".join("\n".join(lines) for _kind, lines in segments).strip()


def _empty_layout(warning: str, source_line_count: int = 0) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "blocks": [],
        "quality": {
            "status": "degraded",
            "source_line_count": source_line_count,
            "output_block_count": 0,
            "heading_count": 0,
            "row_group_count": 0,
            "warnings": [warning] if warning else [],
        },
    }


def build_pdf_layout(
    path: Any,
    *,
    markdown: str,
    page_count: int,
    odl_json: object = None,
    ocr_pages: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the additive PDF layout contract from ODL/PyMuPDF/OCR evidence.

    The function intentionally returns a degraded report instead of raising.
    LinkCV is the strict consumer: it can reject a degraded layout while the
    generic parsing API remains useful for diagnostics and other callers.
    """

    import fitz

    warnings: list[str] = []
    degraded = False
    role_lookup, role_input_exceeded = _role_records(odl_json)
    role_used: set[int] = set()
    markdown_hints, markdown_input_exceeded = _markdown_line_hints(markdown)
    markdown_used: dict[int, set[int]] = {}
    source_lines: list[_LayoutLine] = []
    ocr_pages = ocr_pages or {}
    if role_input_exceeded or markdown_input_exceeded:
        return _empty_layout("layout_input_limit_exceeded")
    try:
        document = fitz.open(path)
    except Exception:
        return _empty_layout("")
    with document:
        actual_page_count = len(document)
        if actual_page_count != page_count:
            degraded = True
        for index, document_page in enumerate(document, start=1):
            width, height = _page_dimensions(document_page)
            if index in ocr_pages:
                page_lines, page_warnings = _ocr_lines(ocr_pages[index], index)
                # OCR pages carry raster dimensions.  Keep them as the source
                # coordinate system; the normalized output is page invariant.
                source_lines.extend(page_lines)
                if page_warnings:
                    degraded = True
                if page_lines:
                    warnings.append("ocr_used")
                if any("BBOX" in warning for warning in page_warnings):
                    warnings.append("bbox_recovered")
                continue
            page_lines = _extract_pymupdf_lines(document_page, index)
            for line in page_lines:
                line.page_width = width
                line.page_height = height
            if not page_lines:
                # A text page without extractable lines is only a degraded
                # layout if OCR was not supplied for it.  We do not invent a
                # full-page block, because that would violate source closure.
                degraded = True
            source_lines.extend(page_lines)

    if len(source_lines) > MAX_LAYOUT_SOURCE_BLOCKS:
        return _empty_layout("layout_source_limit_exceeded", len(source_lines))
    if (
        any(len(line.text) > MAX_LAYOUT_LINE_CHARS for line in source_lines)
        or sum(len(line.text) for line in source_lines) > MAX_LAYOUT_TOTAL_TEXT_CHARS
    ):
        return _empty_layout("layout_input_limit_exceeded", len(source_lines))

    source_lines.sort(key=_bbox_sort_key)
    for source_index, line in enumerate(source_lines):
        line.source_index = source_index
        visible_role = _looks_like_list(line.text)[0]
        role, level, source = _role_for_text(line.text, line.page, role_lookup, role_used)
        if role is None:
            role, level, source = _line_hint_from_markdown(
                line.text, line.page, markdown_hints, markdown_used
            )
        # Visible markers are authoritative. This repairs engines that label
        # numbered items as bullets and prevents generic list/page metadata
        # from fabricating a list marker that is not present in the PDF.
        if visible_role is not None:
            if role != visible_role:
                warnings.append("role_inferred")
            role = visible_role
            level = None
            source = "ocr" if line.source == "ocr" else "visual_inference"
        elif role == "list_item":
            role = None
            level = None
            source = None
        if role:
            line.role_hint = role
            line.heading_level_hint = level
            # OCR geometry remains the provenance of an OCR line even when a
            # Markdown/ODL hint supplies its semantic role.  The role_source
            # field must not claim OpenDataLoader produced an OCR-only block.
            if source and line.source != "ocr":
                line.source = source
        if line.role_hint is None and visible_role is not None:
            line.role_hint = visible_role
            warnings.append("role_inferred")

    heading_candidates = _infer_heading_candidates(source_lines)
    for index, line in enumerate(source_lines):
        if line.role_hint == "heading":
            line.heading_level_hint = line.heading_level_hint or 1
            continue
        if index in heading_candidates:
            line.role_hint = "heading"
            line.heading_level_hint = 1
            line.source = "visual_inference"
            warnings.extend(("heading_inferred", "visual_role_inferred"))
        elif line.role_hint not in {"ordered_list_item", "bullet_list_item"}:
            line.role_hint = "paragraph"

    median_height_by_page: dict[int, float] = {}
    for page in {line.page for line in source_lines}:
        heights = [
            _relative_height(line)
            for line in source_lines
            if line.page == page and _relative_height(line) > 0
        ]
        median_height_by_page[page] = statistics.median(heights) if heights else 1.0
    # Assign row IDs only to genuine side-by-side blocks.  Grouping is
    # transitive within a page, so three aligned columns get one deterministic
    # row ID while adjacent full-width lines remain separate.
    row_groups: list[list[int]] = []
    for index, line in enumerate(source_lines):
        matching: list[int] = []
        for group_index, group in enumerate(row_groups):
            if any(
                _same_row(
                    line,
                    source_lines[member],
                    median_height_by_page.get(line.page, 1.0),
                )
                for member in group
            ):
                matching.append(group_index)
        if not matching:
            row_groups.append([index])
        else:
            first = matching[0]
            row_groups[first].append(index)
            for group_index in reversed(matching[1:]):
                row_groups[first].extend(row_groups.pop(group_index))

    # The initial page/y/x sort is only a candidate order. OCR baselines can
    # differ by a few pixels across two columns, which would otherwise put a
    # right-hand date before the left-hand school/company in source order.
    # Make each visual row one reading unit, order its members by x, then
    # order those units by page and their top-coordinate anchor. This keeps
    # row members contiguous before source_order and continuation links are
    # assigned below.
    row_source_lines = source_lines

    def row_sort_key(group: list[int]) -> tuple[int, float, float, int]:
        members = [row_source_lines[index] for index in group]
        y_values = [member.bbox[1] for member in members if member.bbox is not None]
        x_values = [member.bbox[0] for member in members if member.bbox is not None]
        page = members[0].page
        anchor_y = min(y_values) if y_values else float("inf")
        anchor_x = min(x_values) if x_values else float("inf")
        return page, anchor_y, anchor_x, min(group)

    ordered_groups: list[list[int]] = []
    for group in sorted(row_groups, key=row_sort_key):
        ordered_group = sorted(
            group,
            key=lambda index: (
                row_source_lines[index].bbox[0]
                if row_source_lines[index].bbox is not None
                else float("inf"),
                index,
            ),
        )
        if len(ordered_group) > 1 and any(
            not _same_row(
                row_source_lines[left],
                row_source_lines[right],
                median_height_by_page.get(row_source_lines[left].page, 1.0),
            )
            for left, right in zip(ordered_group, ordered_group[1:], strict=False)
        ):
            # Transitive grouping can bridge two overlapping neighbours via a
            # distant third block.  Such a row would violate the public v1
            # consumer geometry, so fail closed and remove the invalid row
            # relationship instead of emitting a producer-only "passed" DTO.
            degraded = True
            warnings.append("layout_ambiguous")
            ordered_groups.extend([[index] for index in ordered_group])
            continue
        ordered_groups.append(ordered_group)
    source_lines = [row_source_lines[index] for group in ordered_groups for index in group]
    for source_index, line in enumerate(source_lines):
        line.source_index = source_index

    row_id_by_line: dict[int, str] = {}
    row_group_count = 0
    for group in ordered_groups:
        if len(group) < 2:
            continue
        row_group_count += 1
        warnings.append("row_inferred")
        page = row_source_lines[group[0]].page
        row_id = f"page-{page}-row-{row_group_count}"
        # ``group`` indexes refer to the pre-flattening source_lines list;
        # locate members by object identity after flattening so the mapping
        # remains stable even when the row's x-order differs from y-order.
        for member_index in group:
            row_id_by_line[id(row_source_lines[member_index])] = row_id
    for line in source_lines:
        line.row_id = row_id_by_line.get(id(line))
    if _has_ambiguous_columns(source_lines, median_height_by_page):
        degraded = True
        warnings.append("layout_ambiguous")

    # A row is rendered as one logical unit using the first member's role.
    # Mixed roles would otherwise silently promote/demote siblings. Normalize
    # the whole geometrically valid row to the safe paragraph role and expose
    # the bounded recovery through ``row_role_recovered``.
    for group in ordered_groups:
        if len(group) < 2:
            continue
        members = [row_source_lines[index] for index in group]
        role_shapes = {
            (member.role_hint or "paragraph", member.heading_level_hint) for member in members
        }
        if len(role_shapes) <= 1:
            continue
        warnings.append("row_role_recovered")
        for member in members:
            member.role_hint = "paragraph"
            member.heading_level_hint = None
            member.source = "visual_inference"

    # Derive continuation links after roles and rows are known.  A continuation
    # must stay on the same page/section and be close to its predecessor.  A
    # heading or a fresh list marker always starts a new block.
    for index, line in enumerate(source_lines):
        if index == 0 or line.role_hint == "heading":
            continue
        previous = source_lines[index - 1]
        if previous.role_hint == "heading":
            continue
        if line.role_hint in {"ordered_list_item", "bullet_list_item"}:
            continue
        previous_geometry = _relative_geometry(previous)
        current_geometry = _relative_geometry(line)
        if previous.page != line.page:
            if previous_geometry is None or current_geometry is None:
                continue
            x_aligned = abs(current_geometry[0] - previous_geometry[0]) <= 0.04
            near_page_edges = previous_geometry[3] >= 0.88 and current_geometry[1] <= 0.12
            previous_is_list = previous.role_hint in {
                "ordered_list_item",
                "bullet_list_item",
            }
            if (
                previous_is_list
                and x_aligned
                and near_page_edges
                and previous.row_id is None
                and line.row_id is None
            ):
                line.continuation_of = str(index - 1)
                line.join_with = _join_for_continuation(previous.text, line.text)
                line.role_hint = previous.role_hint
                warnings.append("continuation_inferred")
            elif (
                previous_is_list
                and previous_geometry[3] >= 0.78
                and current_geometry[1] <= 0.20
                and abs(current_geometry[0] - previous_geometry[0]) <= 0.06
            ):
                degraded = True
                warnings.append("layout_ambiguous")
            continue
        gap = _relative_vertical_gap(previous, line)
        if gap < 0:
            gap = 0
        previous_height = max(
            _relative_height(previous),
            median_height_by_page.get(previous.page, 1.0),
        )
        if gap > previous_height * 0.55:
            continue
        if previous.row_id is not None or line.row_id is not None:
            continue
        if _starts_labeled_line(previous.text) and _starts_labeled_line(line.text):
            continue
        # A new left-aligned paragraph separated by a visible baseline gap is
        # independent; an indented or nearly aligned continuation is joined.
        previous_x = previous_geometry[0] if previous_geometry else 0.0
        current_x = current_geometry[0] if current_geometry else 0.0
        if abs(current_x - previous_x) > 0.04 + 1e-9:
            continue
        line.continuation_of = str(index - 1)
        line.join_with = _join_for_continuation(previous.text, line.text)
        line.role_hint = previous.role_hint or "paragraph"
        warnings.append("continuation_inferred")

    blocks: list[dict[str, Any]] = []
    page_ordinals: dict[int, int] = {}
    line_to_block: dict[int, str] = {}
    for index, line in enumerate(source_lines):
        block_id = _line_id(line, page_ordinals)
        line_to_block[index] = block_id
        bbox, bbox_valid = _normalise_bbox(line)
        if not bbox_valid:
            degraded = True
            warnings.append("bbox_recovered")
        role = line.role_hint or "paragraph"
        block_text = _normalise_list_text(line.text, role)
        continuation_of = None
        if line.continuation_of is not None:
            target = int(line.continuation_of)
            continuation_of = line_to_block.get(target)
            if continuation_of is None:
                degraded = True
        block = {
            "block_id": block_id,
            "source_order": index,
            "source_page": line.page,
            "role": role,
            "heading_level": (line.heading_level_hint if role == "heading" else None),
            "text": block_text,
            "bbox": bbox,
            "row_id": line.row_id,
            "continuation_of": continuation_of,
            # ``join_with`` describes only continuation lines.  Row members
            # are rendered with the deterministic `` ｜ `` separator and must
            # keep the empty value required by the wire validator.
            "join_with": line.join_with if continuation_of else "",
            "confidence": line.confidence,
            "role_source": ("ocr" if line.source == "ocr" and role != "heading" else line.source),
        }
        blocks.append(block)

    # Turn internal line-index references into block IDs and validate closure.
    for index, block in enumerate(blocks):
        if block["continuation_of"] is not None:
            target = block["continuation_of"]
            if not any(candidate["block_id"] == target for candidate in blocks[:index]):
                degraded = True
                block["continuation_of"] = None
                block["join_with"] = ""

    # A role hint from ODL is authoritative for a matching line, but an
    # explicit visual boundary must not be crossed by a continuation link.
    for index, block in enumerate(blocks):
        target = block.get("continuation_of")
        if not target:
            continue
        previous = blocks[index - 1] if index else None
        if previous is None or previous.get("role") == "heading" or block.get("role") == "heading":
            degraded = True
            block["continuation_of"] = None
            block["join_with"] = ""

    wire_heights_by_page: dict[int, list[float]] = defaultdict(list)
    for block in blocks:
        wire_heights_by_page[block["source_page"]].append(block["bbox"][3] - block["bbox"][1])
    wire_median_height_by_page = {
        page: statistics.median(heights) for page, heights in wire_heights_by_page.items()
    }
    positions_by_row: dict[str, list[int]] = defaultdict(list)
    for index, block in enumerate(blocks):
        if block["row_id"] is not None:
            positions_by_row[block["row_id"]].append(index)
    invalid_wire_rows = {
        row_id
        for row_id, positions in positions_by_row.items()
        if positions != list(range(positions[0], positions[-1] + 1))
        or not _wire_row_is_valid(
            [blocks[index] for index in positions],
            wire_median_height_by_page[blocks[positions[0]]["source_page"]],
        )
    }
    if invalid_wire_rows:
        degraded = True
        warnings.append("layout_ambiguous")
        for block in blocks:
            if block["row_id"] in invalid_wire_rows:
                block["row_id"] = None
    row_group_count = len({block["row_id"] for block in blocks if block["row_id"] is not None})
    if row_group_count == 0:
        warnings = [warning for warning in warnings if warning != "row_inferred"]

    invalid_wire_continuation = False
    for index, block in enumerate(blocks):
        if block["continuation_of"] is None:
            continue
        if _wire_continuation_is_valid(blocks, index, wire_median_height_by_page):
            continue
        invalid_wire_continuation = True
        block["continuation_of"] = None
        block["join_with"] = ""
    if invalid_wire_continuation:
        degraded = True
        warnings.append("layout_ambiguous")

    # Remove duplicate warning strings while retaining deterministic order.
    warnings = list(
        dict.fromkeys(
            warning
            for warning in warnings
            if warning
            in {
                "bbox_recovered",
                "continuation_inferred",
                "heading_inferred",
                "layout_ambiguous",
                "ocr_applied",
                "ocr_used",
                "role_inferred",
                "row_inferred",
                "row_role_recovered",
                "visual_role_inferred",
            }
        )
    )
    heading_count = sum(block["role"] == "heading" for block in blocks)
    if heading_count == 0:
        degraded = True
    if not blocks:
        degraded = True
    status = "degraded" if degraded else "passed"
    quality = {
        "status": status,
        "source_line_count": len(source_lines),
        "output_block_count": len(blocks),
        "heading_count": heading_count,
        "row_group_count": row_group_count,
        "warnings": list(dict.fromkeys(warnings)),
    }
    return {
        "schema_version": 1,
        "blocks": blocks,
        "quality": quality,
    }


# Public aliases make the layout helper easy to exercise from integration
# tests while keeping the descriptive name used by the parser.
build_pdf_layout_contract = build_pdf_layout
markdown_from_layout = _build_markdown_from_layout
render_pdf_layout_markdown = _build_markdown_from_layout


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
