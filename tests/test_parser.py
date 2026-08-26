from contextlib import nullcontext
from copy import deepcopy

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


def test_layout_pipeline_keeps_internal_ocr_bbox_when_public_bbox_is_disabled(tmp_path):
    import fitz

    path = tmp_path / "scan.pdf"
    document = fitz.open()
    document.new_page(width=200, height=220)
    document.save(path)
    document.close()

    class Limiter:
        def slot(self, _name):
            return nullcontext()

    class Structured:
        last_metadata = {}

        def parse(self, *_args, **_kwargs):
            captured["keep_line_breaks"].append(_kwargs["keep_line_breaks"])
            return (
                {
                    "text": "Heading\nBody",
                    "markdown": "<!-- ODL_PAGE:1 -->\n\nHeading\n\nBody",
                    "json": {"pages": [{"page": 1, "text": "Heading\nBody"}]},
                },
                [],
            )

    parser = DocumentParser(
        Settings(data_dir=tmp_path, api_keys=["test"]),
        concurrency_limiter=Limiter(),
    )
    parser._structured = Structured()
    captured = {"include_bbox": [], "keep_line_breaks": []}
    ocr_page = {
        "page": 1,
        "width": 200,
        "height": 220,
        "text": "Heading\nBody",
        "blocks": [
            {"text": "Heading", "bbox": [10, 10, 70, 18], "confidence": 0.99},
            {"text": "Body", "bbox": [10, 30, 80, 35], "confidence": 0.98},
        ],
    }

    def fake_ocr_pdf(*args, **kwargs):
        include_bbox = args[3] if len(args) > 3 else kwargs["include_bbox"]
        captured["include_bbox"].append(include_bbox)
        page = deepcopy(ocr_page)
        if not include_bbox:
            for block in page["blocks"]:
                block.pop("bbox", None)
        return {
            "text": "Heading\nBody",
            "markdown": "Heading\n\nBody",
            "json": {"pages": [page]},
        }, []

    parser._ocr_pdf = fake_ocr_pdf
    parser._analyze_quality = lambda *_args, **_kwargs: {
        "page_provenance_valid": True,
        "ocr_required_pages": [1],
        "status": "PASSED",
    }

    outputs, _assets = parser._parse_pdf_pipeline(
        path,
        {"text", "markdown", "json"},
        include_bbox=False,
        progress=None,
        page_count=1,
        include_layout=True,
    )

    assert captured["include_bbox"] == [True]
    assert captured["keep_line_breaks"] == [True]
    layout = outputs["_pdf_metadata"]["layout"]
    assert layout["quality"]["status"] == "passed"
    assert outputs["markdown"] == "# Heading\n\nBody"
    assert outputs["json"]["ocr_fallback_pages"][0]["blocks"][0].get("bbox") is None

    json_only, _assets = parser._parse_pdf_pipeline(
        path,
        {"json"},
        include_bbox=True,
        progress=None,
        page_count=1,
        include_layout=True,
    )

    assert "markdown" not in json_only
    assert json_only["_pdf_metadata"]["layout"]["schema_version"] == 1

    legacy_outputs, _assets = parser._parse_pdf_pipeline(
        path,
        {"text", "markdown", "json"},
        include_bbox=False,
        progress=None,
        page_count=1,
        include_layout=False,
    )

    assert "layout" not in legacy_outputs["_pdf_metadata"]
    assert legacy_outputs["markdown"] == (
        "<!-- ODL_PAGE:1 -->\n\nHeading\n\nBody\n\n<!-- PAGE_FALLBACK:OCR -->\n\nHeading\nBody"
    )
    assert legacy_outputs["text"] == "Heading\n\nBody\n\nHeading\nBody"
    assert legacy_outputs["json"] == {
        "pages": [{"page": 1, "text": "Heading\nBody"}],
        "ocr_fallback_pages": [
            {
                "page": 1,
                "width": 200,
                "height": 220,
                "text": "Heading\nBody",
                "blocks": [
                    {"text": "Heading", "confidence": 0.99},
                    {"text": "Body", "confidence": 0.98},
                ],
            }
        ],
    }
    assert captured["keep_line_breaks"] == [True, True, False]
    assert captured["include_bbox"] == [True, True, False]


@pytest.mark.parametrize(
    "warning",
    ["layout_input_limit_exceeded", "layout_source_limit_exceeded"],
)
def test_layout_resource_budget_failure_is_explicit_before_outputs_are_replaced(
    tmp_path, monkeypatch, warning
):
    import fitz

    path = tmp_path / "resource-limit.pdf"
    document = fitz.open()
    document.new_page(width=200, height=220)
    document.save(path)
    document.close()

    class Limiter:
        def slot(self, _name):
            return nullcontext()

    class Structured:
        last_metadata = {}

        def parse(self, *_args, **_kwargs):
            return (
                {
                    "markdown": "<!-- ODL_PAGE:1 -->\n\nHeading\n\nBody",
                    "json": {"pages": [{"page": 1, "text": "Heading\nBody"}]},
                },
                [],
            )

    parser = DocumentParser(
        Settings(data_dir=tmp_path, api_keys=["test"]),
        concurrency_limiter=Limiter(),
    )
    parser._structured = Structured()
    parser._analyze_quality = lambda *_args, **_kwargs: {
        "page_provenance_valid": True,
        "ocr_required_pages": [],
        "status": "PASSED",
    }
    monkeypatch.setattr(
        "app.services.parser.build_pdf_layout",
        lambda *_args, **_kwargs: {
            "schema_version": 1,
            "blocks": [],
            "quality": {
                "status": "degraded",
                "source_line_count": 2,
                "output_block_count": 0,
                "heading_count": 0,
                "row_group_count": 0,
                "warnings": [warning],
            },
        },
    )
    monkeypatch.setattr(
        "app.services.parser.markdown_from_layout",
        lambda _blocks: pytest.fail("resource failure must precede output replacement"),
    )

    with pytest.raises(LinkParseError) as captured:
        parser._parse_pdf_pipeline(
            path,
            {"text", "markdown"},
            include_bbox=False,
            progress=None,
            page_count=1,
            include_layout=True,
        )

    assert captured.value.code == "LAYOUT_RESOURCE_LIMIT"
    assert captured.value.status_code == 413


def test_layout_response_budget_is_checked_against_serialized_payload(tmp_path, monkeypatch):
    path = tmp_path / "large-layout.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    parser = DocumentParser(Settings(data_dir=tmp_path, api_keys=["test"]))
    layout = {
        "schema_version": 1,
        "blocks": [
            {
                "block_id": "0",
                "source_order": 0,
                "source_page": 1,
                "role": "paragraph",
                "heading_level": None,
                "text": "内容" * 100,
                "bbox": [0.1, 0.1, 0.9, 0.2],
                "row_id": None,
                "continuation_of": None,
                "join_with": "",
                "confidence": None,
                "role_source": "visual_inference",
            }
        ],
        "quality": {
            "status": "passed",
            "source_line_count": 1,
            "output_block_count": 1,
            "heading_count": 0,
            "row_group_count": 0,
            "warnings": [],
        },
    }
    assets = [{"id": "asset-large-layout"}]
    parser._parse_pdf_pipeline = lambda *_args, **_kwargs: (
        {
            "markdown": "内容" * 100,
            "_pdf_metadata": {"ocr_pages": [], "layout": layout},
        },
        assets,
    )
    deleted = []
    parser.asset_storage.delete_assets = lambda values: deleted.extend(values)
    monkeypatch.setattr("app.services.parser.validate_pdf", lambda *_args: 1)
    monkeypatch.setattr("app.services.parser.MAX_LAYOUT_RESPONSE_BYTES", 100)

    with pytest.raises(LinkParseError) as captured:
        parser.parse(
            path,
            "large-layout.pdf",
            "application/pdf",
            {"markdown"},
            include_bbox=False,
            include_images=False,
            request_id="large-layout",
            include_layout=True,
        )

    assert captured.value.code == "LAYOUT_RESOURCE_LIMIT"
    assert captured.value.status_code == 413
    assert deleted == assets

    monkeypatch.setattr("app.services.parser.MAX_LAYOUT_RESPONSE_BYTES", 10_000)
    result = parser.parse(
        path,
        "large-layout.pdf",
        "application/pdf",
        {"markdown"},
        include_bbox=False,
        include_images=False,
        request_id="layout-within-budget",
        include_layout=True,
    )

    assert result["outputs"]["markdown"] == "内容" * 100
    assert result["meta"]["pdf"]["layout"] == layout


def test_include_layout_is_ignored_for_image_documents(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"not-read-by-fake-ocr")
    parser = DocumentParser(Settings(data_dir=tmp_path, api_keys=["test"]))
    parser._ocr_image = lambda _path, page, include_bbox: {
        "page": page,
        "width": 100,
        "height": 100,
        "text": "image text",
        "confidence": 0.99,
        "blocks": [
            {
                "type": "text",
                "text": "image text",
                "confidence": 0.99,
                **({"bbox": [1, 1, 20, 10]} if include_bbox else {}),
            }
        ],
    }

    result = parser.parse(
        path,
        "image.png",
        "image/png",
        {"text", "json"},
        include_bbox=True,
        include_images=False,
        request_id="image-layout-ignored",
        include_layout=True,
    )

    assert result["detected_type"] == "image"
    assert result["meta"]["pdf"] is None
    assert set(result["outputs"]) == {"text", "json"}
