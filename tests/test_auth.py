import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.core.config import Settings, get_settings
from app.db import Database, User, UserSession, database_for_url, utcnow
from app.main import app
from app.services.parser import DocumentParser

client = TestClient(app)


def configured_settings(tmp_path) -> Settings:
    settings = Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        api_keys=["system-admin"],
    )
    settings.ensure_directories()
    return settings


def register(settings: Settings, suffix: str = "one") -> dict:
    app.dependency_overrides[get_settings] = lambda: settings
    response = client.post(
        "/v1/auth/register",
        json={
            "username": f"tester_{suffix}",
            "email": f"tester_{suffix}@example.com",
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_register_login_and_profile(tmp_path):
    settings = configured_settings(tmp_path)
    try:
        created = register(settings)
        assert created["access_token"].startswith("lps_")
        assert created["user"]["is_admin"] is False
        assert "password" not in created

        duplicate = client.post(
            "/v1/auth/register",
            json={
                "username": "tester_one",
                "email": "other@example.com",
                "password": "correct-horse-battery",
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "ACCOUNT_EXISTS"

        invalid = client.post(
            "/v1/auth/login",
            json={"account": "tester_one", "password": "wrong"},
        )
        assert invalid.status_code == 401

        login = client.post(
            "/v1/auth/login",
            json={"account": "TESTER_ONE", "password": "correct-horse-battery"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        profile = client.get("/v1/auth/me", headers=headers)
        assert profile.status_code == 200
        assert profile.json()["username"] == "tester_one"
        assert profile.json()["is_admin"] is False
        assert profile.json()["stats"] == {"active_keys": 0, "parse_records": 0}

        assert client.post("/v1/auth/logout", headers=headers).status_code == 204
        assert client.get("/v1/auth/me", headers=headers).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_bootstrap_admin_username_is_persisted_on_registration(tmp_path):
    settings = configured_settings(tmp_path)
    settings.bootstrap_admin_usernames = ["root"]
    try:
        created = register(settings, "member")
        assert created["user"]["is_admin"] is False

        response = client.post(
            "/v1/auth/register",
            json={
                "username": "root",
                "email": "root@example.com",
                "password": "correct-horse-battery",
            },
        )
        assert response.status_code == 201
        assert response.json()["user"]["is_admin"] is True

        database = database_for_url(settings.database_url)
        with database.session() as session:
            assert session.query(User).filter_by(username="root").one().is_admin is True
    finally:
        app.dependency_overrides.clear()


def test_legacy_user_table_adds_admin_role_and_promotes_existing_root(tmp_path):
    import sqlite3

    database_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email VARCHAR(255) NOT NULL UNIQUE,
          username VARCHAR(64) NOT NULL UNIQUE,
          password_hash VARCHAR(255) NOT NULL,
          status VARCHAR(20) NOT NULL DEFAULT 'active',
          created_at DATETIME NOT NULL,
          updated_at DATETIME NOT NULL
        );
        INSERT INTO users (email, username, password_hash, status, created_at, updated_at)
        VALUES (
          'root@example.com', 'root', 'unused', 'active',
          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        );
        """
    )
    connection.close()

    database = Database(f"sqlite:///{database_path}")
    database.initialize()
    with database.session() as session:
        root = session.query(User).filter_by(username="root").one()
        assert root.is_admin is True


def test_api_key_lifecycle_and_parse_records(tmp_path, monkeypatch):
    settings = configured_settings(tmp_path)
    try:
        created = register(settings, "keys")
        session_headers = {"Authorization": f"Bearer {created['access_token']}"}
        key_response = client.post(
            "/v1/account/keys",
            headers=session_headers,
            json={"name": "CI integration"},
        )
        assert key_response.status_code == 201
        api_key = key_response.json()["key"]
        key_id = key_response.json()["id"]
        assert api_key.startswith("lpk_")

        monkeypatch.setattr(
            DocumentParser,
            "parse",
            lambda *args, **kwargs: {
                "request_id": kwargs.get("request_id") or args[-1],
                "filename": "sample.png",
                "engine": "rapidocr",
                "detected_type": "image/png",
                "outputs": {"text": "hello"},
                "assets": [],
                "meta": {"page_count": 1, "duration_ms": 12},
            },
        )
        parse_response = client.post(
            "/v1/parse",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("sample.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert parse_response.status_code == 200

        records = client.get("/v1/account/records", headers=session_headers)
        assert records.status_code == 200
        assert records.json()["total"] == 1
        assert records.json()["items"][0]["status"] == "succeeded"
        assert records.json()["items"][0]["filename"] == "sample.png"

        assert (
            client.delete(f"/v1/account/keys/{key_id}", headers=session_headers).status_code == 204
        )
        rejected = client.post(
            "/v1/parse",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("sample.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert rejected.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_registration_rolls_back_when_session_creation_fails(tmp_path, monkeypatch):
    settings = configured_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    original = auth_api.build_session
    monkeypatch.setattr(
        auth_api,
        "build_session",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("session insert failed")),
    )
    payload = {
        "username": "atomic_user",
        "email": "atomic@example.com",
        "password": "correct-horse-battery",
    }
    try:
        with pytest.raises(RuntimeError, match="session insert failed"):
            client.post("/v1/auth/register", json=payload)

        monkeypatch.setattr(auth_api, "build_session", original)
        retried = client.post("/v1/auth/register", json=payload)
        assert retried.status_code == 201
    finally:
        app.dependency_overrides.clear()


def test_expired_sessions_are_removed_by_database_cleanup(tmp_path):
    settings = configured_settings(tmp_path)
    created = register(settings, "expired")
    database = database_for_url(settings.database_url)

    with database.session() as session:
        session.query(UserSession).update({UserSession.expires_at: utcnow()})

    try:
        assert database.delete_expired_sessions() == 1
        headers = {"Authorization": f"Bearer {created['access_token']}"}
        assert client.get("/v1/auth/me", headers=headers).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_duplicate_request_id_is_rejected_instead_of_losing_the_record(
    tmp_path, monkeypatch
):
    settings = configured_settings(tmp_path)
    created = register(settings, "request_id")
    headers = {
        "Authorization": f"Bearer {created['access_token']}",
        "X-Request-ID": "client-reused-id",
    }
    monkeypatch.setattr(
        DocumentParser,
        "parse",
        lambda *args, **kwargs: {
            "request_id": args[-1],
            "filename": "sample.png",
            "engine": "rapidocr",
            "detected_type": "image/png",
            "outputs": {"text": "hello"},
            "assets": [],
            "meta": {"page_count": 1, "duration_ms": 12},
        },
    )
    try:
        first = client.post(
            "/v1/parse",
            headers=headers,
            files={"file": ("sample.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        second = client.post(
            "/v1/parse",
            headers=headers,
            files={"file": ("sample.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert first.status_code == 200
        assert second.status_code == 503
        assert second.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        assert list((settings.data_dir / "uploads").iterdir()) == []
    finally:
        app.dependency_overrides.clear()
