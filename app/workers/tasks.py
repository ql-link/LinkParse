import json
import logging
import os
from pathlib import Path

from celery.exceptions import SoftTimeLimitExceeded
from redis import Redis
from redis.exceptions import LockError

from app.core.config import get_effective_settings
from app.core.errors import ConcurrencyLimitReached, LinkParseError
from app.db import database_for_url
from app.services.assets import OssAssetStorage
from app.services.cleanup import DataCleanup
from app.services.parse_records import update_parse_record
from app.services.parser import DocumentParser
from app.services.result_store import ResultStore
from app.workers.celery_app import celery_app

logger = logging.getLogger("linkparse.worker")


@celery_app.task(name="linkparse.parse_document", max_retries=None)
def parse_document(job_id: str, arguments: dict) -> None:
    settings = get_effective_settings()
    worker_settings = settings.model_copy(update={"concurrency_wait_seconds": 0})
    store = ResultStore(settings)
    database = database_for_url(settings.database_url)
    record_id = arguments.get("record_id")
    input_path = Path(arguments["path"])
    result: dict | None = None
    delete_input = True

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
    update_parse_record(database, record_id, status="processing")
    try:
        result = DocumentParser(worker_settings).parse(
            path=input_path,
            filename=arguments["filename"],
            media_type=arguments["media_type"],
            formats=set(arguments["formats"]),
            include_bbox=arguments["include_bbox"],
            include_images=arguments.get("include_images", False),
            request_id=job_id,
            progress=progress,
        )
        result_path = settings.data_dir / "results" / f"{job_id}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_result = result_path.with_suffix(".tmp")
        temporary_result.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary_result, result_path)
        completed_units = result["meta"]["page_count"] or 1
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "succeeded",
                "progress": {
                    "current_page": completed_units,
                    "total_pages": completed_units,
                },
                "result_path": str(result_path),
            },
        )
        update_parse_record(
            database,
            record_id,
            status="succeeded",
            engine=result["engine"],
            detected_type=result["detected_type"],
            page_count=result["meta"]["page_count"],
            duration_ms=result["meta"]["duration_ms"],
        )
    except SoftTimeLimitExceeded:
        if result:
            OssAssetStorage(settings).delete_assets(result.get("assets", []))
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "failed",
                "progress": {},
                "error": {"code": "PARSE_TIMEOUT", "message": "Document parsing timed out"},
            },
        )
        update_parse_record(
            database,
            record_id,
            status="failed",
            error_code="PARSE_TIMEOUT",
            error_message="Document parsing timed out",
        )
    except ConcurrencyLimitReached as exc:
        delete_input = False
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "queued",
                "progress": {"current_page": 0, "total_pages": 0},
            },
        )
        update_parse_record(database, record_id, status="queued")
        raise parse_document.retry(exc=exc, countdown=1) from exc
    except LinkParseError as exc:
        if result:
            OssAssetStorage(settings).delete_assets(result.get("assets", []))
        store.write(
            job_id,
            {
                "job_id": job_id,
                "status": "failed",
                "progress": {},
                "error": {"code": exc.code, "message": exc.message},
            },
        )
        update_parse_record(
            database,
            record_id,
            status="failed",
            error_code=exc.code,
            error_message=exc.message,
        )
    except Exception:
        if result:
            OssAssetStorage(settings).delete_assets(result.get("assets", []))
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
        update_parse_record(
            database,
            record_id,
            status="failed",
            error_code="INTERNAL_ERROR",
            error_message="Document parsing failed",
        )
        raise
    finally:
        if delete_input:
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
        report["deleted_sessions"] = database_for_url(
            settings.database_url
        ).delete_expired_sessions()
        logger.info(
            "cleanup_completed expired_jobs=%s deleted_job_metadata=%s "
            "deleted_results=%s deleted_assets=%s deleted_uploads=%s "
            "deleted_tmp_entries=%s deleted_sessions=%s",
            report["expired_jobs"],
            report["deleted_job_metadata"],
            report["deleted_results"],
            report["deleted_assets"],
            report["deleted_uploads"],
            report["deleted_tmp_entries"],
            report["deleted_sessions"],
        )
        return report
    finally:
        try:
            lock.release()
        except LockError:
            logger.warning("cleanup_lock_release_failed")
