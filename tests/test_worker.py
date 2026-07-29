from app.workers.celery_app import celery_app


def test_parse_task_is_registered_by_worker_loader():
    celery_app.loader.import_default_modules()
    assert "linkparse.parse_document" in celery_app.tasks
