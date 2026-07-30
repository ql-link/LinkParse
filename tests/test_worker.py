from app.workers.celery_app import celery_app


def test_parse_task_is_registered_by_worker_loader():
    celery_app.loader.import_default_modules()
    assert "linkparse.parse_document" in celery_app.tasks
    assert "linkparse.cleanup_expired_data" in celery_app.tasks


def test_cleanup_schedule_uses_configured_interval():
    schedule = celery_app.conf.beat_schedule["cleanup-expired-linkparse-data"]
    assert schedule["task"] == "linkparse.cleanup_expired_data"
    assert schedule["schedule"] == 3600
