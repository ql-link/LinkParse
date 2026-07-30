import logging
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile

from app.core.config import Settings, get_effective_settings
from app.core.security import authenticate
from app.schemas.models import EngineName, OcrMode, ParseResponse
from app.services.assets import OssAssetStorage
from app.services.parser import DocumentParser, parse_formats
from app.services.result_store import ResultStore
from app.services.uploads import save_upload

router = APIRouter(prefix="/v1", tags=["parse"], dependencies=[Depends(authenticate)])
logger = logging.getLogger("linkparse.parse")


@router.post("/parse", response_model=ParseResponse)
async def parse_document(
    request: Request,
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
    try:
        result = DocumentParser(settings).parse(
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
        return result
    finally:
        Path(temporary).unlink(missing_ok=True)
