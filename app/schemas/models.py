import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JobStatus = Literal["queued", "processing", "succeeded", "failed", "expired"]
AssetKind = Literal["embedded_image", "page_image", "source_image"]
PdfLayoutRole = Literal[
    "heading",
    "paragraph",
    "ordered_list_item",
    "bullet_list_item",
]
PdfLayoutRoleSource = Literal["opendataloader", "visual_inference", "ocr"]
UnitCoordinate = Annotated[float, Field(ge=0, le=1)]


class PdfLayoutBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    source_order: int = Field(ge=0)
    source_page: int = Field(ge=1)
    role: PdfLayoutRole
    heading_level: Literal[1, 2] | None = None
    text: str = Field(min_length=1, max_length=20_000)
    bbox: tuple[UnitCoordinate, UnitCoordinate, UnitCoordinate, UnitCoordinate]
    row_id: str | None = None
    continuation_of: str | None = None
    join_with: Literal["", " "] = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    role_source: PdfLayoutRoleSource

    @field_validator("text")
    @classmethod
    def text_is_one_line(cls, value: str) -> str:
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("layout block text must be a non-empty single line")
        return value

    @field_validator("bbox")
    @classmethod
    def bbox_is_non_degenerate(cls, value: tuple[float, float, float, float]):
        if not all(math.isfinite(coordinate) for coordinate in value):
            raise ValueError("layout block bbox must contain finite coordinates")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("layout block bbox must have positive width and height")
        return value

    @model_validator(mode="after")
    def heading_level_matches_role(self):
        if self.role == "heading" and self.heading_level not in {1, 2}:
            raise ValueError("heading blocks require heading_level 1 or 2")
        if self.role != "heading" and self.heading_level is not None:
            raise ValueError("non-heading blocks cannot have heading_level")
        if self.continuation_of is None and self.join_with != "":
            raise ValueError("join_with is only set on continuation blocks")
        return self


class PdfLayoutQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "degraded"]
    source_line_count: int = Field(ge=0)
    output_block_count: int = Field(ge=0)
    heading_count: int = Field(ge=0)
    row_group_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class PdfLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    blocks: list[PdfLayoutBlock] = Field(default_factory=list)
    quality: PdfLayoutQuality


class PdfMeta(BaseModel):
    """Compatibility envelope for PDF metadata plus the optional layout DTO."""

    # Pipeline/quality/structure metadata predates layout and may gain fields
    # independently. Preserve those fields while making ``layout`` visible in
    # the public ParseResponse/OpenAPI schema.
    model_config = ConfigDict(extra="allow")

    # Keep the additive layout schema visible in OpenAPI without changing the
    # JSON shape for callers that do not opt in. Pydantic applies exclude_if
    # during FastAPI response serialization as well as direct model dumps.
    layout: PdfLayout | None = Field(default=None, exclude_if=lambda value: value is None)


class ParseMeta(BaseModel):
    page_count: int
    duration_ms: int
    pdf: PdfMeta | None = None
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
