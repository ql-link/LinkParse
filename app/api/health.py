import importlib.util
import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_effective_settings
from app.db import database_for_url
from app.services.assets import OssAssetStorage

router = APIRouter(tags=["health"])
logger = logging.getLogger("linkparse.health")


@router.get("/health")
def health(settings: Annotated[Settings, Depends(get_effective_settings)]) -> dict:
    engines = {
        "pymupdf": importlib.util.find_spec("fitz") is not None,
        "rapidocr": importlib.util.find_spec("rapidocr") is not None,
        "opendataloader": importlib.util.find_spec("opendataloader_pdf") is not None,
        "mammoth_word": importlib.util.find_spec("mammoth") is not None,
        "libreoffice_doc": shutil.which("soffice") is not None,
    }
    storage = OssAssetStorage(settings)
    database_status = {"configured": bool(settings.database_url), "available": False}
    if settings.database_url:
        try:
            database_status["available"] = database_for_url(settings.database_url).ping()
        except Exception:
            logger.exception("database_health_check_failed")
    dependencies_ready = all(engines.values()) and (
        not database_status["configured"] or database_status["available"]
    )
    return {
        "status": "ok" if dependencies_ready else "degraded",
        "engines": engines,
        "database": database_status,
        "storage": {
            "provider": "aliyun_oss",
            "configured": storage.configured,
            "bucket": settings.oss_bucket if storage.configured else None,
        },
    }
