from pathlib import Path

import pytest

from app.core.errors import LinkParseError
from app.engines.opendataloader_engine import OpenDataLoaderEngine


def test_output_resource_limits_are_enforced(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "one.txt").write_text("1234", encoding="utf-8")
    (output_dir / "two.txt").write_text("5678", encoding="utf-8")

    engine = OpenDataLoaderEngine(max_output_files=1, max_output_bytes=100)

    with pytest.raises(LinkParseError) as captured:
        engine._enforce_output_limits(output_dir)
    assert captured.value.code == "PDF_RESOURCE_LIMIT"
    assert captured.value.status_code == 413


def test_worker_timeout_terminates_the_isolated_process(monkeypatch, tmp_path: Path):
    class NeverEndingProcess:
        pid = 12345

        @staticmethod
        def poll():
            return None

    process = NeverEndingProcess()
    terminated = []
    ticks = iter((0.0, 2.0))
    engine = OpenDataLoaderEngine(timeout_seconds=1)
    monkeypatch.setattr(engine, "available", lambda: True)
    monkeypatch.setattr(
        "app.engines.opendataloader_engine.subprocess.Popen", lambda *a, **k: process
    )
    monkeypatch.setattr("app.engines.opendataloader_engine.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr(engine, "_terminate", lambda current: terminated.append(current))

    with pytest.raises(LinkParseError) as captured:
        engine.parse(tmp_path / "source.pdf", tmp_path / "output", {"markdown"})

    assert captured.value.code == "ENGINE_TIMEOUT"
    assert captured.value.status_code == 504
    assert terminated == [process]
