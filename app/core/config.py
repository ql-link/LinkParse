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
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("data")
    max_upload_mb: int = 50
    max_pdf_pages: int = 50
    default_dpi: int = 200
    max_dpi: int = 300
    text_threshold: int = 50
    task_time_limit_seconds: int = 300
    job_result_ttl_hours: int = 24
    cleanup_interval_minutes: int = Field(default=60, ge=5, le=1440)
    log_level: str = "INFO"
    ort_intra_op_num_threads: int = 3
    ort_inter_op_num_threads: int = 1

    @field_validator("api_keys", mode="before")
    @classmethod
    def split_api_keys(cls, value: object) -> object:
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
