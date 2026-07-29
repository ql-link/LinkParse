import json
import os
import re
import time
from pathlib import Path
from typing import Any

from app.core.config import Settings


class ResultStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _path(self, job_id: str) -> Path:
        if re.fullmatch(r"job_[A-Za-z0-9_-]{1,64}", job_id) is None:
            return self.settings.data_dir / "jobs" / "invalid-job-id.json"
        return self.settings.data_dir / "jobs" / f"{job_id}.json"

    def _delete_result(self, result_path: object) -> None:
        if not isinstance(result_path, str):
            return
        candidate = Path(result_path).resolve()
        results_dir = (self.settings.data_dir / "results").resolve()
        if candidate.is_relative_to(results_dir):
            candidate.unlink(missing_ok=True)

    def write(self, job_id: str, payload: dict[str, Any]) -> None:
        path = self._path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = int(time.time())
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def read(self, job_id: str) -> dict[str, Any] | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - payload.get("updated_at", 0)
        if age > self.settings.job_result_ttl_hours * 3600:
            self._delete_result(payload.get("result_path"))
            expired = {
                "job_id": job_id,
                "status": "expired",
                "progress": payload.get("progress", {}),
            }
            self.write(job_id, expired)
            return expired
        return payload
