from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "linkparse",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=settings.task_time_limit_seconds,
    task_soft_time_limit=max(1, settings.task_time_limit_seconds - 5),
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    beat_schedule={
        "cleanup-expired-linkparse-data": {
            "task": "linkparse.cleanup_expired_data",
            "schedule": settings.cleanup_interval_minutes * 60,
        }
    },
    timezone="UTC",
)
