import pytest

from app.core.config import Settings
from app.core.errors import ConcurrencyLimitReached
from app.services.result_store import ResultStore
from app.workers import tasks
from app.workers.celery_app import celery_app


def test_parse_task_is_registered_by_worker_loader():
    celery_app.loader.import_default_modules()
    assert "linkparse.parse_document" in celery_app.tasks
    assert "linkparse.cleanup_expired_data" in celery_app.tasks


def test_cleanup_schedule_uses_configured_interval():
    schedule = celery_app.conf.beat_schedule["cleanup-expired-linkparse-data"]
    assert schedule["task"] == "linkparse.cleanup_expired_data"
    assert schedule["schedule"] == 3600


def test_busy_async_job_is_requeued_without_deleting_upload(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, api_keys=["test"])
    settings.ensure_directories()
    input_path = tmp_path / "uploads" / "job_busy"
    input_path.write_bytes(b"document")
    monkeypatch.setattr(tasks, "get_effective_settings", lambda: settings)
    monkeypatch.setattr(
        tasks.DocumentParser,
        "parse",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConcurrencyLimitReached("rapidocr")),
    )

    class RetryScheduled(Exception):
        pass

    monkeypatch.setattr(
        tasks.parse_document,
        "retry",
        lambda **kwargs: (_ for _ in ()).throw(RetryScheduled()),
    )
    arguments = {
        "path": str(input_path),
        "filename": "scan.pdf",
        "media_type": "application/pdf",
        "engine": "auto",
        "formats": ["text"],
        "ocr_mode": "auto",
        "dpi": 200,
        "include_bbox": True,
    }

    with pytest.raises(RetryScheduled):
        tasks.parse_document.run("job_busy", arguments)

    assert input_path.exists()
    assert ResultStore(settings).read("job_busy")["status"] == "queued"
