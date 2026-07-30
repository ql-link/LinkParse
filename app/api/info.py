from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_effective_settings

router = APIRouter(tags=["info"])


@router.get("/v1/info")
def service_info(settings: Annotated[Settings, Depends(get_effective_settings)]) -> dict:
    return {
        "name": settings.app_name,
        "version": "0.2.0",
        "description": "CPU-first document parsing with OpenDataLoader and RapidOCR",
        "input_types": ["PDF", "PNG", "JPEG", "WebP", "TIFF"],
        "output_formats": ["text", "json", "markdown", "html"],
        "image_output": {
            "enabled_by": "include_images=true",
            "provider": "aliyun_oss",
            "configured": bool(
                settings.oss_endpoint
                and settings.oss_access_key_id
                and settings.oss_access_key_secret
                and settings.oss_bucket
            ),
            "url_ttl_hours": max(
                settings.oss_url_ttl_hours,
                settings.job_result_ttl_hours + 24,
            ) if not settings.oss_public_base_url else None,
            "url_mode": "public" if settings.oss_public_base_url else "signed",
        },
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pdf_pages": settings.max_pdf_pages,
            "default_dpi": settings.default_dpi,
            "max_dpi": settings.max_dpi,
            "result_ttl_hours": settings.job_result_ttl_hours,
        },
        "endpoints": {
            "sync": "/v1/parse",
            "async": "/v1/jobs",
            "health": "/health",
            "openapi": "/docs",
        },
    }
