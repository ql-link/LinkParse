import json
import os
import time

from app.core.config import Settings
from app.services.assets import OssAssetStorage
from app.services.cleanup import DataCleanup


def _write_job(path, payload, updated_at):
    payload["updated_at"] = updated_at
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cleanup_expires_result_then_removes_metadata(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        api_keys=["test"],
        job_result_ttl_hours=1,
        cleanup_interval_minutes=30,
    )
    settings.ensure_directories()
    now = time.time()
    result_path = tmp_path / "results" / "job_old.json"
    result_path.write_text("{}", encoding="utf-8")
    job_path = tmp_path / "jobs" / "job_old.json"
    _write_job(
        job_path,
        {
            "job_id": "job_old",
            "status": "succeeded",
            "progress": {"current_page": 2, "total_pages": 2},
            "result_path": str(result_path),
        },
        now - 7200,
    )

    first = DataCleanup(settings).run(now=now)

    assert first["expired_jobs"] == 1
    assert first["deleted_results"] == 1
    assert not result_path.exists()
    expired = json.loads(job_path.read_text(encoding="utf-8"))
    assert expired["status"] == "expired"

    second = DataCleanup(settings).run(now=expired["updated_at"] + 3601)

    assert second["deleted_job_metadata"] == 1
    assert not job_path.exists()


def test_cleanup_removes_orphans_but_keeps_active_upload(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        api_keys=["test"],
        task_time_limit_seconds=300,
        job_result_ttl_hours=1,
        cleanup_interval_minutes=30,
    )
    settings.ensure_directories()
    now = time.time()
    old_time = now - 7200

    orphan_result = tmp_path / "results" / "job_orphan.json"
    orphan_result.write_text("{}", encoding="utf-8")
    orphan_upload = tmp_path / "uploads" / "req_orphan"
    orphan_upload.write_text("source", encoding="utf-8")
    active_upload = tmp_path / "uploads" / "job_active"
    active_upload.write_text("source", encoding="utf-8")
    active_job = tmp_path / "jobs" / "job_active.json"
    _write_job(
        active_job,
        {"job_id": "job_active", "status": "processing", "progress": {}},
        now,
    )
    stale_tmp = tmp_path / "tmp" / "abandoned"
    stale_tmp.mkdir()
    (stale_tmp / "page.png").write_bytes(b"image")
    for path in (orphan_result, orphan_upload, active_upload, stale_tmp):
        os.utime(path, (old_time, old_time))

    report = DataCleanup(settings).run(now=now)

    assert report["deleted_results"] == 1
    assert report["deleted_uploads"] == 1
    assert report["deleted_tmp_entries"] == 1
    assert not orphan_result.exists()
    assert not orphan_upload.exists()
    assert not stale_tmp.exists()
    assert active_upload.exists()


def test_cleanup_deletes_oss_assets_recorded_in_result(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path,
        api_keys=["test"],
        job_result_ttl_hours=1,
        cleanup_interval_minutes=30,
    )
    settings.ensure_directories()
    now = time.time()
    result_path = tmp_path / "results" / "asset_manifest.json"
    result_path.write_text(
        json.dumps({"assets": [{"id": "encoded-object-key"}]}),
        encoding="utf-8",
    )
    os.utime(result_path, (now - 7200, now - 7200))
    deleted: list[dict] = []

    def fake_delete_assets(_self, assets):
        deleted.extend(assets)
        return len(assets)

    monkeypatch.setattr(OssAssetStorage, "delete_assets", fake_delete_assets)

    report = DataCleanup(settings).run(now=now)

    assert report["deleted_results"] == 1
    assert report["deleted_assets"] == 1
    assert deleted == [{"id": "encoded-object-key"}]
    assert not result_path.exists()
