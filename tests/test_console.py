from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app
from app.services.runtime_config import RuntimeConfigStore

client = TestClient(app)


def test_console_is_available_with_security_headers():
    response = client.get("/")
    assert response.status_code == 200
    assert "LinkParse · 文档解析控制台" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_custom_api_documentation_replaces_swagger():
    response = client.get("/docs")
    assert response.status_code == 200
    assert "LinkParse API · 接入文档" in response.text
    assert "swagger-ui" not in response.text.lower()
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_openapi_schema_remains_available_for_tooling():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["version"] == "0.2.0"


def test_public_info_describes_service_limits():
    response = client.get("/v1/info")
    assert response.status_code == 200
    assert response.json()["version"] == "0.2.0"
    assert response.json()["limits"]["default_dpi"] == 200


def test_admin_config_requires_api_key():
    response = client.get("/v1/admin/config")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


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
        values["default_dpi"] = 220
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


def test_admin_rejects_invalid_dpi_range(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["admin-test"])
    app.dependency_overrides[get_settings] = lambda: settings
    headers = {"Authorization": "Bearer admin-test"}
    try:
        values = RuntimeConfigStore(settings).defaults().model_dump()
        values["default_dpi"] = 400
        values["max_dpi"] = 300
        response = client.put("/v1/admin/config", headers=headers, json=values)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    finally:
        app.dependency_overrides.clear()
