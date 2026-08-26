import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import LinkParseError
from app.engines import opendataloader_worker
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


@pytest.mark.parametrize("keep_line_breaks", [False, True])
def test_worker_timeout_terminates_the_isolated_process(
    monkeypatch, tmp_path: Path, keep_line_breaks: bool
):
    class NeverEndingProcess:
        pid = 12345

        @staticmethod
        def poll():
            return None

    process = NeverEndingProcess()
    terminated = []
    launched = {}
    ticks = iter((0.0, 2.0))
    engine = OpenDataLoaderEngine(timeout_seconds=1)
    monkeypatch.setattr(engine, "available", lambda: True)

    def launch(command, **_kwargs):
        launched["command"] = command
        return process

    monkeypatch.setattr("app.engines.opendataloader_engine.subprocess.Popen", launch)
    monkeypatch.setattr("app.engines.opendataloader_engine.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr(engine, "_terminate", lambda current: terminated.append(current))

    with pytest.raises(LinkParseError) as captured:
        engine.parse(
            tmp_path / "source.pdf",
            tmp_path / "output",
            {"markdown"},
            keep_line_breaks=keep_line_breaks,
        )

    assert captured.value.code == "ENGINE_TIMEOUT"
    assert captured.value.status_code == 504
    assert terminated == [process]
    assert ("--keep-line-breaks" in launched["command"]) is keep_line_breaks


@pytest.mark.parametrize("keep_line_breaks", [False, True])
def test_opendataloader_worker_forwards_opt_in_line_breaks(
    monkeypatch, tmp_path: Path, keep_line_breaks: bool
):
    captured = {}
    fake_module = SimpleNamespace(convert=lambda **kwargs: captured.update(kwargs))
    monkeypatch.setitem(sys.modules, "opendataloader_pdf", fake_module)
    arguments = [
        "opendataloader_worker",
        "--input",
        str(tmp_path / "source.pdf"),
        "--output-dir",
        str(tmp_path / "output"),
        "--image-dir",
        str(tmp_path / "images"),
        "--formats",
        "json,markdown",
    ]
    if keep_line_breaks:
        arguments.append("--keep-line-breaks")
    monkeypatch.setattr(sys, "argv", arguments)

    assert opendataloader_worker.main() == 0
    assert captured["keep_line_breaks"] is keep_line_breaks
