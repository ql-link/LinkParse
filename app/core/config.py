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
    default_dpi: int = 200
    max_dpi: int = 300
    text_threshold: int = 50
    task_time_limit_seconds: int = 300
    ocr_max_concurrency: int = Field(default=1, ge=1, le=32)
    opendataloader_max_concurrency: int = Field(default=3, ge=1, le=32)
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

    def ensure_directories(self) -> None:
        for name in ("uploads", "jobs", "results", "tmp"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_effective_settings() -> Settings:
    from app.services.runtime_config import RuntimeConfigStore

    return RuntimeConfigStore(get_settings()).resolve()
