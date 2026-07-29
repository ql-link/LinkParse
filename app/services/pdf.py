from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import LinkParseError


@dataclass(slots=True)
class PdfInfo:
    page_count: int
    sampled_average_text_length: float
    page_text_lengths: list[int]
    detected_type: str


def inspect_pdf(path: Path, max_pages: int, text_threshold: int) -> PdfInfo:
    try:
        import fitz

        with fitz.open(path) as document:
            page_count = len(document)
            if page_count > max_pages:
                raise LinkParseError(
                    "PDF_TOO_MANY_PAGES", f"PDF has {page_count} pages; limit is {max_pages}", 413
                )
            lengths = [len(page.get_text().strip()) for page in document]
    except LinkParseError:
        raise
    except Exception as exc:
        raise LinkParseError("PDF_RENDER_FAILED", f"Unable to read PDF: {exc}", 422) from exc

    sample = lengths[:3]
    average = sum(sample) / len(sample) if sample else 0
    has_text = [length >= text_threshold for length in lengths]
    if has_text and all(has_text):
        detected = "text_pdf"
    elif has_text and any(has_text):
        detected = "mixed_pdf"
    else:
        detected = "scanned_pdf"
    return PdfInfo(page_count, average, lengths, detected)


def extract_pdf_text(path: Path) -> list[str]:
    import fitz

    with fitz.open(path) as document:
        return [page.get_text().strip() for page in document]


def render_pdf(
    path: Path,
    output_dir: Path,
    dpi: int,
    page_numbers: set[int] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> list[Path]:
    try:
        import fitz

        output_dir.mkdir(parents=True, exist_ok=True)
        rendered: list[Path] = []
        with fitz.open(path) as document:
            selected = page_numbers or set(range(1, len(document) + 1))
            total = len(selected)
            completed = 0
            for index, page in enumerate(document):
                page_number = index + 1
                if page_number not in selected:
                    continue
                output = output_dir / f"page_{index + 1:04d}.png"
                page.get_pixmap(dpi=dpi, alpha=False).save(output)
                rendered.append(output)
                completed += 1
                if progress:
                    progress(completed, total)
        return rendered
    except Exception as exc:
        raise LinkParseError("PDF_RENDER_FAILED", f"Unable to render PDF: {exc}", 422) from exc
