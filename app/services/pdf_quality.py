from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.services.pdf_structure import PAGE_MARKER_RE

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>", re.I | re.S)
_HTML_RE = re.compile(r"<[^>]+>")
_MARKDOWN_RE = re.compile(r"[#*_`>\[\]()|~-]")


def _effective_text(value: str) -> str:
    value = _IMAGE_RE.sub("", value or "")
    value = _HTML_RE.sub("", value)
    value = _MARKDOWN_RE.sub("", value)
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if not character.isspace())


def _page_sections(markdown: str) -> tuple[dict[int, str], list[int], list[str]]:
    matches = list(PAGE_MARKER_RE.finditer(markdown or ""))
    warnings: list[str] = []
    sections: dict[int, str] = {}
    if matches and markdown[: matches[0].start()].strip():
        warnings.append("CONTENT_BEFORE_FIRST_PAGE_MARKER")
    for index, match in enumerate(matches):
        page = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        if page in sections:
            warnings.append(f"DUPLICATE_PAGE_MARKER:page={page}")
            continue
        sections[page] = markdown[match.end() : end]
    return sections, [int(match.group(1)) for match in matches], warnings


def _image_coverage(page: Any) -> tuple[int, float]:
    page_area = float(page.rect.width * page.rect.height) or 1.0
    rectangles: list[tuple[float, float, float, float]] = []
    try:
        for item in page.get_image_info(xrefs=True):
            bbox = item.get("bbox")
            if bbox and len(bbox) == 4:
                rectangles.append(tuple(float(value) for value in bbox))
    except Exception:
        rectangles = []
    area = sum(max(0.0, x1 - x0) * max(0.0, y1 - y0) for x0, y0, x1, y1 in rectangles)
    return len(rectangles), min(1.0, area / page_area)


def _fidelity(source: str, output: str) -> tuple[float | None, float | None]:
    if not source:
        return None, None
    matcher = SequenceMatcher(None, source, output, autojunk=False)
    common = sum(block.size for block in matcher.get_matching_blocks())
    retention = common / len(source)
    precision = common / len(output) if output else 0.0
    return round(retention, 6), round(precision, 6)


def analyze_pdf_quality(
    path: Path,
    markdown: str,
    *,
    ocr_pages: dict[int, dict[str, Any]] | None = None,
    min_effective_text_chars: int = 20,
    image_only_max_text_chars: int = 8,
    image_only_min_coverage_ratio: float = 0.6,
    min_ocr_confidence: float = 0.8,
    min_text_retention_ratio: float = 0.97,
) -> dict[str, Any]:
    """Apply the reference project's ODL-first, page-level OCR quality gate."""

    import fitz

    sections, markers, warnings = _page_sections(markdown)
    ocr_pages = ocr_pages or {}
    per_page: list[dict[str, Any]] = []
    with fitz.open(path) as document:
        page_count = document.page_count
        expected = list(range(1, page_count + 1))
        markers_valid = markers == expected and "CONTENT_BEFORE_FIRST_PAGE_MARKER" not in warnings
        if markers != expected:
            warnings.append(f"PAGE_MARKER_SEQUENCE_MISMATCH:expected={expected},actual={markers}")
        for index, page in enumerate(document):
            page_number = index + 1
            source = _effective_text(page.get_text("text") or "")
            odl = _effective_text(sections.get(page_number, ""))
            image_count, image_coverage = _image_coverage(page)
            raster_dominant = image_count > 0 and image_coverage >= image_only_min_coverage_ratio
            is_image_only = raster_dominant and len(source) <= image_only_max_text_chars
            retention, precision = _fidelity(source, odl)
            text_fidelity_low = bool(
                source
                and (
                    retention is None
                    or retention < min_text_retention_ratio
                    or precision is None
                    or precision < min_text_retention_ratio
                )
            )
            ocr = ocr_pages.get(page_number)
            ocr_text = _effective_text(str((ocr or {}).get("text") or ""))
            ocr_confidence = (ocr or {}).get("confidence")
            needs_ocr = bool(
                (is_image_only and len(ocr_text) < min_effective_text_chars)
                or text_fidelity_low
                or (image_count and len(odl) < min_effective_text_chars and not ocr_text)
            )
            low_confidence = bool(
                ocr is not None
                and (
                    not isinstance(ocr_confidence, (int, float))
                    or float(ocr_confidence) < min_ocr_confidence
                )
            )
            page_warnings: list[str] = []
            if page_number not in sections:
                page_warnings.append("MARKDOWN_SECTION_MISSING")
            if text_fidelity_low:
                page_warnings.append("ODL_TEXT_INSUFFICIENT")
            if is_image_only:
                page_warnings.append("IMAGE_ONLY")
            if needs_ocr:
                page_warnings.append("OCR_REQUIRED")
            if low_confidence:
                page_warnings.append("OCR_LOW_CONFIDENCE")
            per_page.append(
                {
                    "page_number": page_number,
                    "pdf_text_char_count": len(source),
                    "odl_text_char_count": len(odl),
                    "ocr_text_char_count": len(ocr_text),
                    "image_count": image_count,
                    "image_coverage_ratio": round(image_coverage, 6),
                    "is_image_only": is_image_only,
                    "text_retention_ratio": retention,
                    "text_precision_ratio": precision,
                    "ocr_required": needs_ocr,
                    "ocr_applied": ocr is not None,
                    "ocr_confidence": ocr_confidence,
                    "low_confidence": low_confidence,
                    "warnings": page_warnings,
                }
            )
    required = [page["page_number"] for page in per_page if page["ocr_required"]]
    low_confidence_pages = [page["page_number"] for page in per_page if page["low_confidence"]]
    if not markers_valid:
        status = "PAGE_PROVENANCE_INVALID"
    elif low_confidence_pages:
        status = "LOW_CONFIDENCE"
    elif required:
        status = "OCR_REQUIRED"
    else:
        status = "PASSED"
    return {
        "status": status,
        "pdf_page_count": page_count,
        "page_markers": markers,
        "page_provenance_valid": markers_valid,
        "ocr_required_pages": required,
        "ocr_page_count": len(ocr_pages),
        "low_confidence_pages": low_confidence_pages,
        "warnings": list(dict.fromkeys(warnings)),
        "per_page": per_page,
        "thresholds": {
            "min_effective_text_chars": min_effective_text_chars,
            "image_only_max_text_chars": image_only_max_text_chars,
            "image_only_min_coverage_ratio": image_only_min_coverage_ratio,
            "min_ocr_confidence": min_ocr_confidence,
            "min_text_retention_ratio": min_text_retention_ratio,
        },
    }
