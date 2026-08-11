from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LINKPARSE_", case_sensitive=False, extra="ignore"
    )

    app_name: str = "LinkParse"
    api_keys: Annotated[list[str], NoDecode] = ["change-me"]
    bootstrap_admin_usernames: Annotated[list[str], NoDecode] = []
    database_url: str = ""
    session_ttl_hours: int = Field(default=168, ge=1, le=2160)
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("data")
    max_upload_mb: int = 50
    max_pdf_pages: int = 50
    task_time_limit_seconds: int = 300
    ocr_max_concurrency: int = Field(default=1, ge=1, le=32)
    opendataloader_max_concurrency: int = Field(default=3, ge=1, le=32)
    opendataloader_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    opendataloader_table_method: str = "default"
    opendataloader_markdown_with_html: bool = False
    opendataloader_max_output_files: int = Field(default=2000, ge=10, le=100_000)
    opendataloader_max_output_mb: int = Field(default=512, ge=10, le=10_240)
    pdf_quality_min_effective_text_chars: int = Field(default=20, ge=1, le=1000)
    pdf_quality_image_only_max_text_chars: int = Field(default=8, ge=0, le=200)
    pdf_quality_image_only_min_coverage_ratio: float = Field(default=0.6, ge=0, le=1)
    pdf_quality_min_ocr_confidence: float = Field(default=0.8, ge=0, le=1)
    pdf_quality_min_text_retention_ratio: float = Field(default=0.97, ge=0, le=1)
    pdf_fallback_render_dpi: int = Field(default=280, ge=250, le=300)
    concurrency_wait_seconds: int = Field(default=30, ge=0, le=600)
    job_result_ttl_hours: int = 24
    cleanup_interval_minutes: int = Field(default=60, ge=5, le=1440)
    oss_endpoint: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_bucket: str = ""
    oss_object_prefix: str = "linkparse-assets"
    oss_public_base_url: str = ""
    oss_url_ttl_hours: int = Field(default=744, ge=1, le=8760)
    log_level: str = "INFO"
    ort_intra_op_num_threads: int = 3
    ort_inter_op_num_threads: int = 1

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if isinstance(value, str) and value.startswith("mysql://"):
            return value.replace("mysql://", "mysql+pymysql://", 1)
        return value

    @field_validator("api_keys", mode="before")
    @classmethod
    def split_api_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("bootstrap_admin_usernames", mode="before")
    @classmethod
    def split_bootstrap_admin_usernames(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("opendataloader_table_method")
    @classmethod
    def validate_opendataloader_table_method(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"default", "cluster"}:
            raise ValueError("opendataloader_table_method must be default or cluster")
        return normalized

    def ensure_directories(self) -> None:
        for name in ("uploads", "jobs", "results", "tmp"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_effective_settings() -> Settings:
    from app.services.runtime_config import RuntimeConfigStore

    return RuntimeConfigStore(get_settings()).resolve()
