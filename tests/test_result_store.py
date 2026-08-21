import json
import time

from app.core.config import Settings
from app.services.result_store import ResultStore
from app.services.runtime_config import RuntimeConfigStore


def test_result_store_round_trip(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["test"])
    settings.ensure_directories()
    store = ResultStore(settings)
    store.write("job_1", {"job_id": "job_1", "status": "queued", "progress": {}})
    assert store.read("job_1")["status"] == "queued"


def test_expired_result_is_removed(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["test"], job_result_ttl_hours=1)
    settings.ensure_directories()
    result_path = tmp_path / "results" / "job_old.json"
    result_path.write_text("{}", encoding="utf-8")
    job_path = tmp_path / "jobs" / "job_old.json"
    job_path.write_text(
        json.dumps(
            {
                "job_id": "job_old",
                "status": "succeeded",
                "progress": {},
                "result_path": str(result_path),
                "updated_at": int(time.time()) - 7200,
            }
        ),
        encoding="utf-8",
    )

    assert ResultStore(settings).read("job_old")["status"] == "expired"
    assert not result_path.exists()


def test_asset_manifests_are_independent_for_duplicate_request_ids(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["test"])
    settings.ensure_directories()
    store = ResultStore(settings)
    result = {"request_id": "reused-request", "assets": []}

    first = store.write_asset_manifest(result)
    second = store.write_asset_manifest(result)

    assert first != second
    assert first.exists()
    assert second.exists()


def test_runtime_config_is_atomic_and_resolves_over_environment(tmp_path):
    settings = Settings(data_dir=tmp_path, api_keys=["test"], max_upload_mb=50)
    store = RuntimeConfigStore(settings)
    config = store.defaults().model_copy(update={"max_upload_mb": 80})

    description = store.write(config)

    assert description["source"] == "runtime"
    assert store.resolve().max_upload_mb == 80
    assert (tmp_path / "config" / "runtime.json").stat().st_mode & 0o777 == 0o600
