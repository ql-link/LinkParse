from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_effective_settings

router = APIRouter(tags=["info"])


@router.get("/v1/info")
def service_info(settings: Annotated[Settings, Depends(get_effective_settings)]) -> dict:
    return {
        "name": settings.app_name,
        "version": "0.2.0",
        "description": "CPU-first document parsing for PDF, Word and images",
        "pdf_pipeline": {
            "name": "opendataloader_ocr",
            "primary": "opendataloader",
            "fallback": "page_level_rapidocr",
            "quality_gate": True,
        },
        "word_pipeline": {
            "name": "mammoth_word",
            "primary": "mammoth",
            "intermediate": "semantic_html",
            "formula_preprocessing": "omml_to_latex",
            "table_strategy": "simple_markdown_complex_html",
            "output_formats": ["markdown"],
            "pagination_supported": True,
            "pagination_source": "saved_docx_page_breaks",
            "bbox_supported": False,
        },
        "input_types": ["PDF", "DOCX", "PNG", "JPEG", "WebP", "TIFF"],
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
            )
            if not settings.oss_public_base_url
            else None,
            "url_mode": "public" if settings.oss_public_base_url else "signed",
        },
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pdf_pages": settings.max_pdf_pages,
            "result_ttl_hours": settings.job_result_ttl_hours,
            "concurrency": {
                "rapidocr": settings.ocr_max_concurrency,
                "opendataloader": settings.opendataloader_max_concurrency,
                "word": settings.word_max_concurrency,
                "wait_seconds": settings.concurrency_wait_seconds,
            },
            "opendataloader": {
                "timeout_seconds": settings.opendataloader_timeout_seconds,
                "table_method": settings.opendataloader_table_method,
                "markdown_with_html": settings.opendataloader_markdown_with_html,
                "max_output_files": settings.opendataloader_max_output_files,
                "max_output_mb": settings.opendataloader_max_output_mb,
            },
            "pdf_quality": {
                "fallback_render_dpi": settings.pdf_fallback_render_dpi,
                "min_effective_text_chars": settings.pdf_quality_min_effective_text_chars,
                "image_only_max_text_chars": settings.pdf_quality_image_only_max_text_chars,
                "image_only_min_coverage_ratio": (
                    settings.pdf_quality_image_only_min_coverage_ratio
                ),
                "min_ocr_confidence": settings.pdf_quality_min_ocr_confidence,
                "min_text_retention_ratio": (settings.pdf_quality_min_text_retention_ratio),
            },
        },
        "endpoints": {
            "sync": "/v1/parse",
            "async": "/v1/jobs",
            "health": "/health",
            "openapi": "/docs",
        },
    }
