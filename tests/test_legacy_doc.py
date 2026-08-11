import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.errors import EngineUnavailable, LinkParseError
from app.services.legacy_doc import LegacyDocConverter
from tests.docx_factory import write_docx


def test_legacy_doc_converter_reports_missing_libreoffice(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    converter = LegacyDocConverter(timeout_seconds=30, max_output_bytes=1024 * 1024)

    with pytest.raises(EngineUnavailable) as caught:
        converter.convert(tmp_path / "sample.doc", tmp_path / "temp")

    assert caught.value.code == "ENGINE_UNAVAILABLE"
    assert "libreoffice_doc" in caught.value.message


def test_legacy_doc_converter_kills_timed_out_process_group(tmp_path, monkeypatch):
    class TimedOutProcess:
        pid = 4242
        returncode = None

        def __init__(self, *args, **kwargs):
            self.communicate_calls = 0

        def communicate(self, timeout=None):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired("soffice", timeout)
            return b"", b""

    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/soffice")
    monkeypatch.setattr(subprocess, "Popen", TimedOutProcess)
    monkeypatch.setattr(
        "app.services.legacy_doc.os.killpg",
        lambda pid, sig: killed.append((pid, sig)),
    )
    converter = LegacyDocConverter(timeout_seconds=10, max_output_bytes=1024 * 1024)

    with pytest.raises(LinkParseError) as caught:
        converter.convert(tmp_path / "sample.doc", tmp_path / "temp")

    assert caught.value.code == "DOC_CONVERSION_TIMEOUT"
    assert caught.value.status_code == 504
    assert killed == [(4242, 9)]
    assert not any((tmp_path / "temp").iterdir())


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed")
def test_real_libreoffice_doc_round_trip_produces_valid_docx(tmp_path):
    source_docx = write_docx(tmp_path / "source.docx", formula=True, page_break=True)
    source_profile = tmp_path / "source-profile"
    source_profile.mkdir()
    subprocess.run(
        [
            shutil.which("soffice") or "soffice",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={source_profile.resolve().as_uri()}",
            "--convert-to",
            "doc:MS Word 97",
            "--outdir",
            str(tmp_path),
            str(source_docx),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    source_doc = Path(tmp_path / "source.doc")
    assert source_doc.is_file()

    conversion = LegacyDocConverter(
        timeout_seconds=60,
        max_output_bytes=10 * 1024 * 1024,
    ).convert(source_doc, tmp_path / "convert-temp")
    try:
        assert conversion.path.suffix == ".docx"
        assert conversion.metadata["source_format"] == "doc"
        assert conversion.metadata["target_format"] == "docx"
        assert conversion.metadata["output_bytes"] > 0
    finally:
        conversion.cleanup()
