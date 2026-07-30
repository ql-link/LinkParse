import importlib.util
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_effective_settings
from app.services.assets import OssAssetStorage

router = APIRouter(tags=["health"])


@router.get("/health")
def health(settings: Annotated[Settings, Depends(get_effective_settings)]) -> dict:
    engines = {
        "pymupdf": importlib.util.find_spec("fitz") is not None,
        "rapidocr": importlib.util.find_spec("rapidocr") is not None,
        "opendataloader": importlib.util.find_spec("opendataloader_pdf") is not None,
    }
    storage = OssAssetStorage(settings)
    return {
        "status": "ok" if all(engines.values()) else "degraded",
        "engines": engines,
        "storage": {
            "provider": "aliyun_oss",
            "configured": storage.configured,
            "bucket": settings.oss_bucket if storage.configured else None,
        },
    }
