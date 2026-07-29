import json
import logging
import os
from datetime import UTC, datetime

from app.core.config import Settings
from app.schemas.models import RuntimeConfig

logger = logging.getLogger("linkparse.runtime_config")

RUNTIME_FIELDS = tuple(RuntimeConfig.model_fields)


class RuntimeConfigStore:
    def __init__(self, base_settings: Settings) -> None:
        self.base_settings = base_settings
        self.path = base_settings.data_dir / "config" / "runtime.json"

    def defaults(self) -> RuntimeConfig:
        return RuntimeConfig.model_validate(
            {name: getattr(self.base_settings, name) for name in RUNTIME_FIELDS}
        )

    def read(self) -> RuntimeConfig | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return RuntimeConfig.model_validate(payload["values"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.error("runtime_config_invalid path=%s error=%s", self.path, exc)
            return None

    def resolve(self) -> Settings:
        runtime = self.read()
        if runtime is None:
            return self.base_settings
        return self.base_settings.model_copy(update=runtime.model_dump())

    def describe(self) -> dict:
        runtime = self.read()
        updated_at = None
        if runtime is not None:
            updated_at = datetime.fromtimestamp(self.path.stat().st_mtime, UTC).isoformat()
        return {
            "values": (runtime or self.defaults()).model_dump(),
            "defaults": self.defaults().model_dump(),
            "source": "runtime" if runtime is not None else "environment",
            "updated_at": updated_at,
        }

    def write(self, config: RuntimeConfig) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "values": config.model_dump(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        return self.describe()

    def reset(self) -> dict:
        self.path.unlink(missing_ok=True)
        return self.describe()
