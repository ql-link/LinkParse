from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "processing", "succeeded", "failed", "expired"]
AssetKind = Literal["embedded_image", "page_image", "source_image"]


class ParseMeta(BaseModel):
    page_count: int
    duration_ms: int
    pdf: dict[str, Any] | None = None
    word: dict[str, Any] | None = None


class ParseAsset(BaseModel):
    id: str
    kind: AssetKind
    filename: str
    media_type: str
    size_bytes: int
    url: str
    expires_at: str | None = None
    page: int | None = None


class ParseResponse(BaseModel):
    request_id: str
    filename: str
    engine: str
    detected_type: str
    outputs: dict[str, Any]
    assets: list[ParseAsset] = Field(default_factory=list)
    meta: ParseMeta


class JobProgress(BaseModel):
    current_page: int = 0
    total_pages: int = 0


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: JobProgress = Field(default_factory=JobProgress)
    error: dict[str, str] | None = None


class RuntimeConfig(BaseModel):
    max_upload_mb: int = Field(ge=1, le=500)
    max_pdf_pages: int = Field(ge=1, le=500)
    task_time_limit_seconds: int = Field(ge=30, le=3600)
    ocr_max_concurrency: int = Field(ge=1, le=32)
    opendataloader_max_concurrency: int = Field(ge=1, le=32)
    word_max_concurrency: int = Field(ge=1, le=32)
    concurrency_wait_seconds: int = Field(ge=0, le=600)
    job_result_ttl_hours: int = Field(ge=1, le=720)
    ort_intra_op_num_threads: int = Field(ge=1, le=64)
    ort_inter_op_num_threads: int = Field(ge=1, le=64)
