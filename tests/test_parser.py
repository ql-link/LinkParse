import pytest

from app.core.config import Settings
from app.core.errors import LinkParseError
from app.services.parser import DocumentParser, parse_formats
from app.services.pdf import PdfInfo


def test_parse_formats():
    assert parse_formats("text, json,text") == {"text", "json"}


def test_parse_formats_rejects_unknown():
    with pytest.raises(LinkParseError):
        parse_formats("xml")


def test_mixed_pdf_ocr_pages_are_merged_without_losing_structured_output():
    structured = {
        "text": "digital page",
        "json": {"pages": [{"page": 1, "text": "digital page"}]},
    }
    ocr = {
        "text": "scanned page",
        "json": {"pages": [{"page": 2, "text": "scanned page"}]},
    }
    merged = DocumentParser._merge_ocr_fallback(structured, ocr)
    assert merged["text"] == "digital page\n\nscanned page"
    assert merged["json"]["ocr_fallback_pages"][0]["page"] == 2


def test_auto_routes_short_text_pdf_to_rapidocr(tmp_path):
    parser = DocumentParser(Settings(data_dir=tmp_path, api_keys=["test"]))
    info = PdfInfo(
        page_count=1,
        sampled_average_text_length=20,
        page_text_lengths=[20],
        detected_type="scanned_pdf",
    )
    assert parser._select_pdf_engine("auto", "auto", info) == "rapidocr"


def test_auto_routes_text_pdf_to_opendataloader(tmp_path):
    parser = DocumentParser(Settings(data_dir=tmp_path, api_keys=["test"]))
    info = PdfInfo(
        page_count=1,
        sampled_average_text_length=80,
        page_text_lengths=[80],
        detected_type="text_pdf",
    )
    assert parser._select_pdf_engine("auto", "auto", info) == "opendataloader"
