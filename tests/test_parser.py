import pytest

from app.core.config import Settings
from app.core.errors import LinkParseError
from app.services.parser import DocumentParser, parse_formats


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
    merged = DocumentParser._merge_page_fallback(structured, ocr)
    assert merged["text"] == "digital page\n\nscanned page"
    assert merged["json"]["ocr_fallback_pages"][0]["page"] == 2


def test_page_ocr_is_appended_to_odl_page_in_original_order():
    structured = {
        "markdown": (
            "<!-- ODL_PAGE:1 -->\n\nfirst page\n\n"
            "<!-- ODL_PAGE:2 -->\n\nempty scan\n\n"
            "<!-- ODL_PAGE:3 -->\n\nthird page"
        ),
        "json": {"document": "odl"},
    }
    ocr = {
        "markdown": "## Page 2\n\nrecognized scan",
        "json": {"pages": [{"page": 2, "text": "recognized scan"}]},
    }

    merged = DocumentParser._merge_page_fallback(structured, ocr)

    assert merged["markdown"].index("first page") < merged["markdown"].index("recognized scan")
    assert merged["markdown"].index("recognized scan") < merged["markdown"].index("third page")
    assert "empty scan" in merged["markdown"]
    assert "<!-- PAGE_FALLBACK:OCR -->" in merged["markdown"]
    assert merged["json"]["ocr_fallback_pages"][0]["page"] == 2


def test_opendataloader_settings_are_forwarded_to_engine(tmp_path):
    parser = DocumentParser(
        Settings(
            data_dir=tmp_path,
            api_keys=["test"],
            opendataloader_timeout_seconds=45,
            opendataloader_table_method="cluster",
            opendataloader_markdown_with_html=True,
            opendataloader_max_output_files=123,
            opendataloader_max_output_mb=64,
        )
    )

    assert parser.structured.timeout_seconds == 45
    assert parser.structured.table_method == "cluster"
    assert parser.structured.markdown_with_html is True
    assert parser.structured.max_output_files == 123
    assert parser.structured.max_output_bytes == 64 * 1024 * 1024
