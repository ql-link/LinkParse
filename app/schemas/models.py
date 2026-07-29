from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

EngineName = Literal["auto", "opendataloader", "rapidocr"]
OcrMode = Literal["auto", "always", "never"]
JobStatus = Literal["queued", "processing", "succeeded", "failed", "expired"]


class ParseMeta(BaseModel):
    page_count: int
    duration_ms: int


class ParseResponse(BaseModel):
    request_id: str
    filename: str
    engine: str
    detected_type: str
    outputs: dict[str, Any]
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
    default_dpi: int = Field(ge=72, le=600)
    max_dpi: int = Field(ge=72, le=600)
    text_threshold: int = Field(ge=0, le=10_000)
    task_time_limit_seconds: int = Field(ge=30, le=3600)
    job_result_ttl_hours: int = Field(ge=1, le=720)
    ort_intra_op_num_threads: int = Field(ge=1, le=64)
    ort_inter_op_num_threads: int = Field(ge=1, le=64)

    @model_validator(mode="after")
    def validate_dpi_range(self) -> "RuntimeConfig":
        if self.default_dpi > self.max_dpi:
            raise ValueError("default_dpi cannot exceed max_dpi")
        return self
