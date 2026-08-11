import json

from fastapi.testclient import TestClient

from app.core.config import Settings, get_effective_settings, get_settings
from app.db import User, database_for_url
from app.main import app
from app.services.runtime_config import RuntimeConfigStore

client = TestClient(app)


def test_console_is_available_with_security_headers():
    response = client.get("/")
    assert response.status_code == 200
    assert "LinkParse · 文档解析控制台" in response.text
    assert 'class="auth-gate" id="auth-gate"' in response.text
    assert 'class="app-shell hidden" id="app-shell"' in response.text
    assert 'id="auth-dialog"' not in response.text
    assert 'class="dock-item admin-only hidden" data-view="configuration"' in response.text
    assert 'id="config-api-key"' not in response.text
    assert 'id="parse-engine"' not in response.text
    assert 'id="parse-ocr"' not in response.text
    assert 'id="parse-dpi"' not in response.text
    assert (
        'accept=".pdf,.doc,application/msword,.docx,'
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document,'
        '.png,.jpg,.jpeg,.webp,.tif,.tiff"'
    ) in response.text
    assert "OpenDataLoader + 按页 OCR" in response.text
    assert 'src="/assets/clipboard.js' in response.text
    assert 'id="preview-markdown" disabled' in response.text
    assert 'id="markdown-preview-dialog"' in response.text
    assert 'src="/assets/vendor/markdown-it/markdown-it.umd.min.js?v=15.0.0"' in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_custom_api_documentation_replaces_swagger():
    response = client.get("/docs")
    assert response.status_code == 200
    assert "LinkParse API · 接入文档" in response.text
    assert 'class="auth-pending"' in response.text
    assert 'src="/assets/clipboard.js' in response.text
    assert "swagger-ui" not in response.text.lower()
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_clipboard_helper_has_http_compatible_fallback():
    response = client.get("/assets/clipboard.js")
    assert response.status_code == 200
    assert "navigator.clipboard?.writeText" in response.text
    assert 'document.execCommand("copy")' in response.text


def test_markdown_preview_renderer_is_served_locally():
    response = client.get("/assets/vendor/markdown-it/markdown-it.umd.min.js")
    assert response.status_code == 200
    assert "markdownit" in response.text


def test_openapi_schema_remains_available_for_tooling():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == "0.2.0"
    for path in ("/v1/parse", "/v1/jobs"):
        request_schema = schema["paths"][path]["post"]["requestBody"]["content"][
            "multipart/form-data"
        ]["schema"]
        properties = schema["components"]["schemas"][request_schema["$ref"].split("/")[-1]][
            "properties"
        ]
        assert "engine" not in properties
        assert "ocr" not in properties
        assert "dpi" not in properties


def test_public_info_describes_service_limits():
    response = client.get("/v1/info")
    assert response.status_code == 200
    assert response.json()["version"] == "0.2.0"
    assert response.json()["limits"]["pdf_quality"]["fallback_render_dpi"] == 280
    assert response.json()["input_types"][:3] == ["PDF", "DOC", "DOCX"]
    assert response.json()["limits"]["doc_conversion_timeout_seconds"] == 120
    assert response.json()["word_pipeline"]["legacy_doc_conversion"] == (
        "libreoffice_to_docx"
    )
    assert response.json()["limits"]["concurrency"] == {
        "rapidocr": 1,
        "opendataloader": 3,
        "word": 2,
        "wait_seconds": 30,
    }


def test_health_reports_database_configuration(tmp_path):
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'health.db'}")
    app.dependency_overrides[get_effective_settings] = lambda: settings
    try:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["database"] == {"configured": True, "available": True}
    finally:
        app.dependency_overrides.clear()


def test_admin_config_requires_api_key():
    response = client.get("/v1/admin/config")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_admin_config_uses_account_role(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'roles.db'}",
        api_keys=["system-admin"],
    )
    settings.ensure_directories()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        registered = client.post(
            "/v1/auth/register",
            json={
                "username": "config_owner",
                "email": "config_owner@example.com",
                "password": "correct-horse-battery",
            },
        ).json()
        headers = {"Authorization": f"Bearer {registered['access_token']}"}

        denied = client.get("/v1/admin/config", headers=headers)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "ADMIN_REQUIRED"

        database = database_for_url(settings.database_url)
        with database.session() as session:
            user = session.query(User).filter_by(username="config_owner").one()
            user.is_admin = True

        allowed = client.get("/v1/admin/config", headers=headers)
        assert allowed.status_code == 200
        profile = client.get("/v1/auth/me", headers=headers)
        assert profile.json()["is_admin"] is True
    finally:
        app.dependency_overrides.clear()


def test_admin_can_update_and_reset_runtime_config(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["admin-test"])
    settings.ensure_directories()
    app.dependency_overrides[get_settings] = lambda: settings
    headers = {"Authorization": "Bearer admin-test"}
    try:
        initial = client.get("/v1/admin/config", headers=headers)
        assert initial.json()["source"] == "environment"

        values = initial.json()["values"]
        values["max_upload_mb"] = 72
        updated = client.put("/v1/admin/config", headers=headers, json=values)
        assert updated.status_code == 200
        assert updated.json()["source"] == "runtime"
        assert RuntimeConfigStore(settings).resolve().max_upload_mb == 72

        reset = client.delete("/v1/admin/config", headers=headers)
        assert reset.status_code == 200
        assert reset.json()["source"] == "environment"
        assert RuntimeConfigStore(settings).resolve().max_upload_mb == settings.max_upload_mb
    finally:
        app.dependency_overrides.clear()


def test_admin_rejects_invalid_page_limit(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["admin-test"])
    app.dependency_overrides[get_settings] = lambda: settings
    headers = {"Authorization": "Bearer admin-test"}
    try:
        values = RuntimeConfigStore(settings).defaults().model_dump()
        values["max_pdf_pages"] = 0
        response = client.put("/v1/admin/config", headers=headers, json=values)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    finally:
        app.dependency_overrides.clear()


def test_legacy_runtime_config_gets_new_concurrency_defaults(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["admin-test"])
    settings.ensure_directories()
    config_path = tmp_path / "config" / "runtime.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"values": {"max_upload_mb": 77}}),
        encoding="utf-8",
    )

    resolved = RuntimeConfigStore(settings).resolve()

    assert resolved.max_upload_mb == 77
    assert resolved.ocr_max_concurrency == 1
    assert resolved.opendataloader_max_concurrency == 3
