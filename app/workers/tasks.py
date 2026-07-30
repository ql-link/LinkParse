import json
import logging
import os
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded
from redis import Redis
from redis.exceptions import LockError

from app.core.config import get_effective_settings
from app.core.errors import LinkParseError
from app.services.cleanup import DataCleanup
from app.services.parser import DocumentParser
from app.services.result_store import ResultStore
from app.workers.celery_app import celery_app

logger = logging.getLogger("linkparse.worker")


@celery_app.task(name="linkparse.parse_document")
def parse_document(job_id: str, arguments: dict) -> None:
    settings = get_effective_settings()
    store = ResultStore(settings)
    input_path = Path(arguments["path"])

    def progress(current: int, total: int) -> None:
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "processing",
                "progress": {"current_page": current, "total_pages": total},
            },
        )

    store.write(
        job_id,
        {
            "job_id": job_id,
            "status": "processing",
            "progress": {"current_page": 0, "total_pages": 0},
        },
    )
    try:
        result = DocumentParser(settings).parse(
            path=input_path,
            filename=arguments["filename"],
            media_type=arguments["media_type"],
            engine=arguments["engine"],
            formats=set(arguments["formats"]),
            ocr_mode=arguments["ocr_mode"],
            dpi=arguments["dpi"],
            include_bbox=arguments["include_bbox"],
            request_id=job_id,
            progress=progress,
        )
        result_path = settings.data_dir / "results" / f"{job_id}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_result = result_path.with_suffix(".tmp")
        temporary_result.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary_result, result_path)
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "succeeded",
                "progress": {
                    "current_page": result["meta"]["page_count"],
                    "total_pages": result["meta"]["page_count"],
                },
                "result_path": str(result_path),
            },
        )
    except SoftTimeLimitExceeded:
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "failed",
                "progress": {},
                "error": {"code": "PARSE_TIMEOUT", "message": "Document parsing timed out"},
            },
        )
    except LinkParseError as exc:
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "failed",
                "progress": {},
                "error": {"code": exc.code, "message": exc.message},
            },
        )
    except Exception:
        logger.exception("job_failed job_id=%s", job_id)
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "failed",
                "progress": {},
                "error": {"code": "INTERNAL_ERROR", "message": "Document parsing failed"},
            },
        )
        raise
    finally:
        input_path.unlink(missing_ok=True)


@celery_app.task(name="linkparse.cleanup_expired_data", ignore_result=True)
def cleanup_expired_data() -> dict[str, int] | None:
    settings = get_effective_settings()
    redis_client = Redis.from_url(settings.redis_url)
    lock = redis_client.lock(
        "linkparse:maintenance:cleanup",
        timeout=max(300, settings.cleanup_interval_minutes * 120),
    )
    if not lock.acquire(blocking=False):
        logger.info("cleanup_skipped reason=lock_held")
        return None
    try:
        report = DataCleanup(settings).run()
        logger.info(
            "cleanup_completed expired_jobs=%s deleted_job_metadata=%s "
            "deleted_results=%s deleted_uploads=%s deleted_tmp_entries=%s",
            report["expired_jobs"],
            report["deleted_job_metadata"],
            report["deleted_results"],
            report["deleted_uploads"],
            report["deleted_tmp_entries"],
        )
        return report
    finally:
        try:
            lock.release()
        except LockError:
            logger.warning("cleanup_lock_release_failed")
