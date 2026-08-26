import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import jobs as jobs_api
from app.api.jobs import create_job
from app.api.parse import parse_document
from app.core.config import Settings, get_effective_settings, get_settings
from app.core.errors import LinkParseError
from app.db import Database, get_database
from app.main import app
from app.schemas.models import ParseResponse
from app.services.parser import DocumentParser
from app.workers import tasks

client = TestClient(app)


@pytest.fixture
def isolated_api_settings(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["change-me"], database_url="")
    settings.ensure_directories()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_effective_settings] = lambda: settings
    app.dependency_overrides[get_database] = lambda: Database("")
    try:
        yield settings
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_effective_settings, None)
        app.dependency_overrides.pop(get_database, None)


def test_health_is_public():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"ok", "degraded"}


def test_request_id_is_reused_in_error_and_response_header():
    response = client.post("/v1/parse", headers={"x-request-id": "caller-request-1"})
    assert response.status_code == 401
    assert response.headers["x-request-id"] == "caller-request-1"
    assert response.json()["error"]["request_id"] == "caller-request-1"


def test_parse_requires_api_key():
    response = client.post(
        "/v1/parse",
        files={"file": ("sample.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_rejects_fake_file_even_when_content_type_is_allowed():
    response = client.post(
        "/v1/parse",
        headers={"Authorization": "Bearer change-me"},
        files={"file": ("sample.pdf", b"not really a pdf", "application/pdf")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_layout_flag_is_optional_on_sync_and_async_forms():
    assert inspect.signature(parse_document).parameters["include_layout"].default is False
    assert inspect.signature(create_job).parameters["include_layout"].default is False


def test_sync_parse_forwards_include_layout_to_document_parser(monkeypatch, isolated_api_settings):
    captured = {}

    def fake_parse(_self, *args, **kwargs):
        captured["include_layout"] = kwargs.get("include_layout")
        return {
            "request_id": args[6],
            "filename": args[1],
            "engine": "opendataloader_ocr",
            "detected_type": "text_pdf",
            "outputs": {"text": "parsed", "json": {}},
            "assets": [],
            "meta": {
                "page_count": 1,
                "duration_ms": 1,
                "pdf": {"pipeline": "opendataloader_ocr", "warnings": []},
                "word": None,
            },
        }

    monkeypatch.setattr(DocumentParser, "parse", fake_parse)

    response = client.post(
        "/v1/parse",
        headers={"Authorization": "Bearer change-me"},
        files={"file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf")},
        data={"include_layout": "true"},
    )

    assert response.status_code == 200
    assert captured["include_layout"] is True


def test_sync_parse_surfaces_layout_resource_limit(monkeypatch, isolated_api_settings):
    def resource_limited(_self, *args, **kwargs):
        assert kwargs.get("include_layout") is True
        raise LinkParseError(
            "LAYOUT_RESOURCE_LIMIT",
            "PDF layout exceeded the resource limit",
            413,
        )

    monkeypatch.setattr(DocumentParser, "parse", resource_limited)

    response = client.post(
        "/v1/parse",
        headers={"Authorization": "Bearer change-me"},
        files={"file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf")},
        data={"include_layout": "true"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "LAYOUT_RESOURCE_LIMIT"


def test_async_parse_rejects_layout_without_application_side_effects(
    monkeypatch, isolated_api_settings
):
    async def unexpected_save(*_args, **_kwargs):
        pytest.fail("layout rejection must happen before save_upload")

    monkeypatch.setattr(jobs_api, "save_upload", unexpected_save)
    monkeypatch.setattr(
        jobs_api,
        "create_parse_record",
        lambda *_args, **_kwargs: pytest.fail(
            "layout rejection must happen before create_parse_record"
        ),
    )
    monkeypatch.setattr(
        jobs_api.ResultStore,
        "write",
        lambda *_args, **_kwargs: pytest.fail(
            "layout rejection must happen before ResultStore.write"
        ),
    )
    monkeypatch.setattr(
        tasks.parse_document,
        "delay",
        lambda *_args, **_kwargs: pytest.fail("layout rejection must happen before Celery enqueue"),
    )

    response = client.post(
        "/v1/jobs",
        headers={"Authorization": "Bearer change-me"},
        files={"file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf")},
        data={"include_layout": "true"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LAYOUT_ASYNC_UNSUPPORTED"


@pytest.mark.parametrize("form", [{}, {"include_layout": "false"}])
def test_async_parse_preserves_legacy_celery_payload(monkeypatch, isolated_api_settings, form):
    captured = {}

    def fake_delay(job_id, arguments):
        captured["job_id"] = job_id
        captured["arguments"] = arguments

    monkeypatch.setattr(tasks.parse_document, "delay", fake_delay)

    response = client.post(
        "/v1/jobs",
        headers={"Authorization": "Bearer change-me"},
        files={"file": ("sample.pdf", b"%PDF-1.4\n", "application/pdf")},
        data=form,
    )

    assert response.status_code == 202
    assert captured["job_id"] == response.json()["job_id"]
    arguments = captured["arguments"]
    assert set(arguments) == {
        "path",
        "filename",
        "media_type",
        "formats",
        "include_bbox",
        "include_images",
        "record_id",
    }
    assert arguments["filename"] == "sample.pdf"
    assert arguments["media_type"] == "application/pdf"
    assert arguments["formats"] == ["json", "text"]
    assert arguments["include_bbox"] is True
    assert arguments["include_images"] is False


def test_parse_response_openapi_exposes_optional_pdf_layout():
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    parse_meta = schema["ParseMeta"]["properties"]["pdf"]
    pdf_meta_name = parse_meta["anyOf"][0]["$ref"].rsplit("/", 1)[-1]
    pdf_meta = schema[pdf_meta_name]
    assert "layout" in pdf_meta["properties"]
    layout_ref = pdf_meta["properties"]["layout"]["anyOf"][0]["$ref"]
    layout_name = layout_ref.rsplit("/", 1)[-1]
    assert schema[layout_name]["properties"]["schema_version"]["const"] == 1
    assert "blocks" in schema[layout_name]["properties"]
    assert "quality" in schema[layout_name]["properties"]


def test_legacy_pdf_response_shape_omits_unrequested_layout():
    compatibility_app = FastAPI()

    @compatibility_app.get("/result", response_model=ParseResponse)
    def legacy_result():
        return {
            "request_id": "req_legacy",
            "filename": "legacy.pdf",
            "engine": "opendataloader_ocr",
            "detected_type": "text_pdf",
            "outputs": {"text": "legacy"},
            "assets": [],
            "meta": {
                "page_count": 1,
                "duration_ms": 1,
                "pdf": {
                    "pipeline": "opendataloader_ocr",
                    "warnings": [],
                },
                "word": None,
            },
        }

    payload = TestClient(compatibility_app).get("/result").json()

    assert payload["meta"]["pdf"] == {
        "pipeline": "opendataloader_ocr",
        "warnings": [],
    }
