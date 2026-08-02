import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_effective_settings
from app.core.errors import LinkParseError
from app.core.security import AuthContext, authenticate
from app.db import Database, get_database
from app.schemas.models import EngineName, OcrMode, ParseResponse
from app.services.assets import OssAssetStorage
from app.services.parse_records import create_parse_record, update_parse_record
from app.services.parser import DocumentParser, parse_formats
from app.services.result_store import ResultStore
from app.services.uploads import save_upload

router = APIRouter(prefix="/v1", tags=["parse"], dependencies=[Depends(authenticate)])
logger = logging.getLogger("linkparse.parse")


@router.post("/parse", response_model=ParseResponse)
async def parse_document(
    request: Request,
    auth: Annotated[AuthContext, Depends(authenticate)],
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_effective_settings)],
    file: Annotated[UploadFile, File()],
    engine: Annotated[EngineName, Form()] = "auto",
    output_formats: Annotated[str, Form()] = "text,json",
    ocr: Annotated[OcrMode, Form()] = "auto",
    dpi: Annotated[int | None, Form()] = None,
    include_bbox: Annotated[bool, Form()] = True,
    include_images: Annotated[bool, Form()] = False,
) -> dict:
    request_id = getattr(request.state, "request_id", f"req_{uuid.uuid4().hex}")
    effective_dpi = dpi if dpi is not None else settings.default_dpi
    temporary = settings.data_dir / "uploads" / request_id
    temporary, media_type, filename, size = await save_upload(
        file, temporary, settings.max_upload_mb * 1024 * 1024
    )
    record_id = None
    try:
        record_id = create_parse_record(
            database,
            auth,
            request_id=request_id,
            job_id=None,
            filename=filename,
            mode="sync",
            engine=engine,
        )
        result = await run_in_threadpool(
            DocumentParser(settings).parse,
            temporary,
            filename,
            media_type,
            engine,
            parse_formats(output_formats),
            ocr,
            effective_dpi,
            include_bbox,
            include_images,
            request_id,
        )
        if result["assets"]:
            try:
                ResultStore(settings).write_asset_manifest(result)
            except Exception:
                OssAssetStorage(settings).delete_assets(result["assets"])
                raise
        logger.info(
            "parse_succeeded request_id=%s caller=%s media_type=%s size_bytes=%s "
            "pages=%s engine=%s formats=%s assets=%s duration_ms=%s",
            request_id,
            getattr(request.state, "api_key_id", "unknown"),
            media_type,
            size,
            result["meta"]["page_count"],
            result["engine"],
            ",".join(sorted(result["outputs"])),
            len(result["assets"]),
            result["meta"]["duration_ms"],
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
        return result
    except LinkParseError as exc:
        update_parse_record(
            database,
            record_id,
            status="failed",
            error_code=exc.code,
            error_message=exc.message,
        )
        raise
    except Exception:
        update_parse_record(
            database,
            record_id,
            status="failed",
            error_code="INTERNAL_ERROR",
            error_message="Document parsing failed",
        )
        raise
    finally:
        Path(temporary).unlink(missing_ok=True)
