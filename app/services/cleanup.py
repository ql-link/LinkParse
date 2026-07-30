import json
import shutil
import time
from pathlib import Path

from app.core.config import Settings
from app.services.result_store import ResultStore


class DataCleanup:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = ResultStore(settings)

    def run(self, now: float | None = None) -> dict[str, int]:
        self.settings.ensure_directories()
        current_time = time.time() if now is None else now
        ttl_seconds = self.settings.job_result_ttl_hours * 3600
        stale_seconds = max(
            3600,
            self.settings.task_time_limit_seconds * 2,
            self.settings.cleanup_interval_minutes * 120,
        )
        report = {
            "expired_jobs": 0,
            "deleted_job_metadata": 0,
            "deleted_results": 0,
            "deleted_assets": 0,
            "deleted_uploads": 0,
            "deleted_tmp_entries": 0,
        }

        self._cleanup_jobs(current_time, ttl_seconds, report)
        self._cleanup_orphan_results(current_time, ttl_seconds, report)
        self._cleanup_uploads(current_time, stale_seconds, report)
        self._cleanup_tmp(current_time, stale_seconds, report)
        return report

    def _cleanup_jobs(
        self, current_time: float, ttl_seconds: int, report: dict[str, int]
    ) -> None:
        jobs_dir = self.settings.data_dir / "jobs"
        for job_path in jobs_dir.glob("job_*.json"):
            payload = self._read_json(job_path)
            if payload is None:
                if self._age(job_path, current_time) > ttl_seconds:
                    job_path.unlink(missing_ok=True)
                    report["deleted_job_metadata"] += 1
                continue

            updated_at = payload.get("updated_at")
            age = (
                current_time - float(updated_at)
                if isinstance(updated_at, (int, float))
                else self._age(job_path, current_time)
            )
            if age <= ttl_seconds:
                continue

            if payload.get("status") == "expired":
                result_path = payload.get("result_path")
                if isinstance(result_path, str) and Path(result_path).is_file():
                    deleted_result, deleted_assets = self.store.delete_result(result_path)
                    report["deleted_assets"] += deleted_assets
                    if not deleted_result:
                        continue
                    report["deleted_results"] += 1
                job_path.unlink(missing_ok=True)
                report["deleted_job_metadata"] += 1
                continue

            deleted_result, deleted_assets = self.store.delete_result(
                payload.get("result_path")
            )
            if deleted_result:
                report["deleted_results"] += 1
            report["deleted_assets"] += deleted_assets
            self.store.write(
                payload.get("job_id", job_path.stem),
                {
                    "job_id": payload.get("job_id", job_path.stem),
                    "status": "expired",
                    "progress": payload.get("progress", {}),
                },
            )
            report["expired_jobs"] += 1

    def _cleanup_orphan_results(
        self, current_time: float, ttl_seconds: int, report: dict[str, int]
    ) -> None:
        results_dir = self.settings.data_dir / "results"
        jobs_dir = self.settings.data_dir / "jobs"
        for result_path in results_dir.iterdir():
            if not result_path.is_file() or self._age(result_path, current_time) <= ttl_seconds:
                continue
            if not (jobs_dir / f"{result_path.stem}.json").exists():
                deleted_result, deleted_assets = self.store.delete_result(str(result_path))
                if deleted_result:
                    report["deleted_results"] += 1
                report["deleted_assets"] += deleted_assets

    def _cleanup_uploads(
        self, current_time: float, stale_seconds: int, report: dict[str, int]
    ) -> None:
        uploads_dir = self.settings.data_dir / "uploads"
        jobs_dir = self.settings.data_dir / "jobs"
        for upload_path in uploads_dir.iterdir():
            if not upload_path.is_file() or self._age(upload_path, current_time) <= stale_seconds:
                continue
            job_payload = self._read_json(jobs_dir / f"{upload_path.name}.json")
            if job_payload and job_payload.get("status") in {"queued", "processing"}:
                continue
            upload_path.unlink(missing_ok=True)
            report["deleted_uploads"] += 1

    def _cleanup_tmp(
        self, current_time: float, stale_seconds: int, report: dict[str, int]
    ) -> None:
        tmp_dir = self.settings.data_dir / "tmp"
        for entry in tmp_dir.iterdir():
            if self._age(entry, current_time) <= stale_seconds:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            report["deleted_tmp_entries"] += 1

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _age(path: Path, current_time: float) -> float:
        try:
            return current_time - path.stat().st_mtime
        except FileNotFoundError:
            return 0
