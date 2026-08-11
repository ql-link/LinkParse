import json
import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.core.config import Settings, get_effective_settings
from app.core.errors import LinkParseError
from app.core.security import AuthContext, authenticate
from app.db import Database, get_database
from app.schemas.models import JobResponse, ParseResponse
from app.services.parse_records import create_parse_record, record_owned_by, update_parse_record
from app.services.parser import engine_for_media_type, parse_formats
from app.services.result_store import ResultStore
from app.services.uploads import save_upload

router = APIRouter(prefix="/v1/jobs", tags=["jobs"], dependencies=[Depends(authenticate)])
logger = logging.getLogger("linkparse.jobs")


@router.post("", response_model=JobResponse, status_code=202)
async def create_job(
    request: Request,
    auth: Annotated[AuthContext, Depends(authenticate)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
    file: Annotated[UploadFile, File()],
    output_formats: Annotated[str, Form()] = "text,json",
    include_bbox: Annotated[bool, Form()] = True,
    include_images: Annotated[bool, Form()] = False,
) -> dict:
    job_id = f"job_{uuid.uuid4().hex}"
    formats = parse_formats(output_formats)
    path = settings.data_dir / "uploads" / job_id
    path, media_type, filename, size = await save_upload(
        file, path, settings.max_upload_mb * 1024 * 1024
    )
    payload = {
        "job_id": job_id,
        "status": "queued",
        "progress": {"current_page": 0, "total_pages": 0},
    }
    store = ResultStore(settings)
    record_id = None
    try:
        record_id = create_parse_record(
            database,
            auth,
            request_id=getattr(request.state, "request_id", job_id),
            job_id=job_id,
            filename=filename,
            mode="async",
            engine=engine_for_media_type(media_type),
            status="queued",
        )
        store.write(job_id, payload)
        from app.workers.tasks import parse_document

        parse_document.delay(
            job_id,
            {
                "path": str(path),
                "filename": filename,
                "media_type": media_type,
                "formats": sorted(formats),
                "include_bbox": include_bbox,
                "include_images": include_images,
                "record_id": record_id,
            },
        )
    except LinkParseError:
        path.unlink(missing_ok=True)
        store.delete(job_id)
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        try:
            store.write(
                job_id,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "progress": {},
                    "error": {
                        "code": "ENGINE_UNAVAILABLE",
                        "message": "Task queue is unavailable",
                    },
                },
            )
        except Exception:
            logger.exception("job_failure_state_write_failed job_id=%s", job_id)
        update_parse_record(
            database,
            record_id,
            status="failed",
            error_code="ENGINE_UNAVAILABLE",
            error_message="Task queue is unavailable",
        )
        raise LinkParseError("ENGINE_UNAVAILABLE", "Task queue is unavailable", 503) from exc
    logger.info(
        "job_queued request_id=%s caller=%s job_id=%s media_type=%s size_bytes=%s formats=%s",
        getattr(request.state, "request_id", "unknown"),
        getattr(request.state, "api_key_id", "unknown"),
        job_id,
        media_type,
        size,
        ",".join(sorted(formats)),
    )
    return payload


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    auth: Annotated[AuthContext, Depends(authenticate)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> dict:
    if database.configured and not record_owned_by(database, job_id, auth):
        raise LinkParseError("JOB_NOT_FOUND", "Job not found", 404)
    payload = ResultStore(settings).read(job_id)
    if payload is None:
        raise LinkParseError("JOB_NOT_FOUND", "Job not found", 404)
    return payload


@router.get("/{job_id}/result", response_model=ParseResponse)
def get_job_result(
    job_id: str,
    auth: Annotated[AuthContext, Depends(authenticate)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
) -> dict:
    if database.configured and not record_owned_by(database, job_id, auth):
        raise LinkParseError("JOB_NOT_FOUND", "Job not found", 404)
    payload = ResultStore(settings).read(job_id)
    if payload is None:
        raise LinkParseError("JOB_NOT_FOUND", "Job not found", 404)
    if payload["status"] != "succeeded":
        raise LinkParseError("JOB_NOT_READY", f"Job status is {payload['status']}", 409)
    result_path = Path(payload["result_path"])
    if not result_path.exists():
        raise LinkParseError("JOB_RESULT_EXPIRED", "Job result is no longer available", 410)
    return json.loads(result_path.read_text(encoding="utf-8"))
