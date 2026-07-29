from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


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
