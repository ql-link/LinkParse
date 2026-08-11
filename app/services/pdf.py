from collections.abc import Callable
from pathlib import Path

from app.core.errors import LinkParseError


def validate_pdf(path: Path, max_pages: int) -> int:
    """Validate that a PDF is readable and return its page count.

    Content type is deliberately not classified here: every PDF enters the
    same OpenDataLoader + page-level OCR pipeline.
    """
    try:
        import fitz

        with fitz.open(path) as document:
            page_count = len(document)
            if page_count > max_pages:
                raise LinkParseError(
                    "PDF_TOO_MANY_PAGES", f"PDF has {page_count} pages; limit is {max_pages}", 413
                )
    except LinkParseError:
        raise
    except Exception as exc:
        raise LinkParseError("PDF_RENDER_FAILED", f"Unable to read PDF: {exc}", 422) from exc
    return page_count


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
