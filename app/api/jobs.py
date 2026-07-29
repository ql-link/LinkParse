import json
import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.core.config import Settings, get_effective_settings
from app.core.errors import LinkParseError
from app.core.security import authenticate
from app.schemas.models import EngineName, JobResponse, OcrMode, ParseResponse
from app.services.parser import parse_formats
from app.services.result_store import ResultStore
from app.services.uploads import save_upload

router = APIRouter(prefix="/v1/jobs", tags=["jobs"], dependencies=[Depends(authenticate)])
logger = logging.getLogger("linkparse.jobs")


@router.post("", response_model=JobResponse, status_code=202)
async def create_job(
    request: Request,
    settings: Annotated[Settings, Depends(get_effective_settings)],
    file: Annotated[UploadFile, File()],
    engine: Annotated[EngineName, Form()] = "auto",
    output_formats: Annotated[str, Form()] = "text,json",
    ocr: Annotated[OcrMode, Form()] = "auto",
    dpi: Annotated[int | None, Form()] = None,
    include_bbox: Annotated[bool, Form()] = True,
) -> dict:
    job_id = f"job_{uuid.uuid4().hex}"
    effective_dpi = dpi if dpi is not None else settings.default_dpi
    if effective_dpi < 72 or effective_dpi > settings.max_dpi:
        raise LinkParseError(
            "INVALID_ARGUMENT", f"dpi must be between 72 and {settings.max_dpi}", 422
        )
    path = settings.data_dir / "uploads" / job_id
    path, media_type, filename, size = await save_upload(
        file, path, settings.max_upload_mb * 1024 * 1024
    )
    formats = parse_formats(output_formats)
    payload = {
        "job_id": job_id,
        "status": "queued",
        "progress": {"current_page": 0, "total_pages": 0},
    }
    ResultStore(settings).write(job_id, payload)
    try:
        from app.workers.tasks import parse_document

        parse_document.delay(
            job_id,
            {
                "path": str(path),
                "filename": filename,
                "media_type": media_type,
                "engine": engine,
                "formats": sorted(formats),
                "ocr_mode": ocr,
                "dpi": effective_dpi,
                "include_bbox": include_bbox,
            },
        )
    except Exception as exc:
        path.unlink(missing_ok=True)
        ResultStore(settings).write(
            job_id,
            {
                "job_id": job_id,
                "status": "failed",
                "progress": {},
                "error": {"code": "ENGINE_UNAVAILABLE", "message": "Task queue is unavailable"},
            },
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
def get_job(job_id: str, settings: Annotated[Settings, Depends(get_effective_settings)]) -> dict:
    payload = ResultStore(settings).read(job_id)
    if payload is None:
        raise LinkParseError("JOB_NOT_FOUND", "Job not found", 404)
    return payload


@router.get("/{job_id}/result", response_model=ParseResponse)
def get_job_result(
    job_id: str, settings: Annotated[Settings, Depends(get_effective_settings)]
) -> dict:
    payload = ResultStore(settings).read(job_id)
    if payload is None:
        raise LinkParseError("JOB_NOT_FOUND", "Job not found", 404)
    if payload["status"] != "succeeded":
        raise LinkParseError("JOB_NOT_READY", f"Job status is {payload['status']}", 409)
    result_path = Path(payload["result_path"])
    if not result_path.exists():
        raise LinkParseError("JOB_RESULT_EXPIRED", "Job result is no longer available", 410)
    return json.loads(result_path.read_text(encoding="utf-8"))
