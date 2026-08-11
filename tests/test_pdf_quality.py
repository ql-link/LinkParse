from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from app.services.pdf_quality import analyze_pdf_quality


def _create_mixed_pdf(path: Path) -> None:
    image_path = path.with_suffix(".png")
    image = Image.new("RGB", (1200, 1600), "white")
    ImageDraw.Draw(image).text((80, 100), "scanned carbon accounting page", fill="black")
    image.save(image_path)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Born digital energy accounting content")
    page = document.new_page(width=600, height=800)
    page.insert_image(page.rect, filename=str(image_path))
    document.save(path)
    document.close()


def test_quality_gate_selects_only_scanned_page_for_ocr(tmp_path: Path):
    pdf = tmp_path / "mixed.pdf"
    _create_mixed_pdf(pdf)
    markdown = (
        "<!-- ODL_PAGE:1 -->\n\nBorn digital energy accounting content\n\n<!-- ODL_PAGE:2 -->\n\n"
    )

    report = analyze_pdf_quality(pdf, markdown)

    assert report["status"] == "OCR_REQUIRED"
    assert report["page_provenance_valid"] is True
    assert report["ocr_required_pages"] == [2]
    assert report["per_page"][0]["ocr_required"] is False
    assert report["per_page"][1]["is_image_only"] is True


def test_quality_gate_passes_after_required_page_ocr(tmp_path: Path):
    pdf = tmp_path / "mixed.pdf"
    _create_mixed_pdf(pdf)
    markdown = (
        "<!-- ODL_PAGE:1 -->\n\nBorn digital energy accounting content\n\n"
        "<!-- ODL_PAGE:2 -->\n\n"
        "<!-- PAGE_FALLBACK:OCR -->\n\nscanned carbon accounting page content"
    )

    report = analyze_pdf_quality(
        pdf,
        markdown,
        ocr_pages={
            2: {
                "text": "scanned carbon accounting page content",
                "confidence": 0.95,
            }
        },
    )

    assert report["status"] == "PASSED"
    assert report["ocr_required_pages"] == []
    assert report["ocr_page_count"] == 1


def test_quality_gate_rejects_invalid_page_provenance(tmp_path: Path):
    pdf = tmp_path / "mixed.pdf"
    _create_mixed_pdf(pdf)

    report = analyze_pdf_quality(pdf, "<!-- ODL_PAGE:2 -->\ncontent")

    assert report["status"] == "PAGE_PROVENANCE_INVALID"
    assert report["page_provenance_valid"] is False
