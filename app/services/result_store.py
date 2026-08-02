import json
import os
import re
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.services.assets import OssAssetStorage


class ResultStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _path(self, job_id: str) -> Path:
        if re.fullmatch(r"job_[A-Za-z0-9_-]{1,64}", job_id) is None:
            return self.settings.data_dir / "jobs" / "invalid-job-id.json"
        return self.settings.data_dir / "jobs" / f"{job_id}.json"

    def delete_result(self, result_path: object) -> tuple[bool, int]:
        if not isinstance(result_path, str):
            return False, 0
        candidate = Path(result_path).resolve()
        results_dir = (self.settings.data_dir / "results").resolve()
        if not candidate.is_relative_to(results_dir) or not candidate.is_file():
            return False, 0
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            payload = {}
        assets = payload.get("assets", []) if isinstance(payload, dict) else []
        expected_assets = sum(
            1
            for asset in assets
            if isinstance(asset, dict) and isinstance(asset.get("id"), str)
        )
        deleted_assets = OssAssetStorage(self.settings).delete_assets(assets)
        if deleted_assets < expected_assets:
            return False, deleted_assets
        candidate.unlink(missing_ok=True)
        return True, deleted_assets

    def write_asset_manifest(self, result: dict[str, Any]) -> Path:
        request_id = str(result.get("request_id", "unknown"))
        digest = sha256(request_id.encode()).hexdigest()[:24]
        path = self.settings.data_dir / "results" / f"asset_{digest}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "request_id": request_id,
            "assets": result.get("assets", []),
            "updated_at": int(time.time()),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        return path

    def write(self, job_id: str, payload: dict[str, Any]) -> None:
        path = self._path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload["updated_at"] = int(time.time())
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def delete(self, job_id: str) -> None:
        self._path(job_id).unlink(missing_ok=True)

    def read(self, job_id: str) -> dict[str, Any] | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - payload.get("updated_at", 0)
        if age > self.settings.job_result_ttl_hours * 3600:
            deleted_result, _ = self.delete_result(payload.get("result_path"))
            expired = {
                "job_id": job_id,
                "status": "expired",
                "progress": payload.get("progress", {}),
            }
            if not deleted_result and isinstance(payload.get("result_path"), str):
                expired["result_path"] = payload["result_path"]
            self.write(job_id, expired)
            return expired
        return payload
