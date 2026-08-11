import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import EngineUnavailable, LinkParseError
from app.services.file_validate import validate_docx_package


@dataclass(frozen=True)
class LegacyDocConversion:
    path: Path
    work_dir: Path
    metadata: dict

    def cleanup(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)


class LegacyDocConverter:
    """Convert legacy OLE Word documents to validated DOCX packages."""

    def __init__(self, timeout_seconds: int, max_output_bytes: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    @staticmethod
    def available() -> bool:
        return shutil.which("soffice") is not None

    def convert(self, source: Path, temp_root: Path) -> LegacyDocConversion:
        executable = shutil.which("soffice")
        if executable is None:
            raise EngineUnavailable("libreoffice_doc")

        temp_root.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix="legacy_doc_", dir=temp_root))
        output_dir = work_dir / "output"
        profile_dir = work_dir / "profile"
        output_dir.mkdir()
        profile_dir.mkdir()
        command = [
            executable,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--convert-to",
            "docx:Office Open XML Text",
            "--outdir",
            str(output_dir),
            str(source.resolve()),
        ]
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                raise LinkParseError(
                    "DOC_CONVERSION_TIMEOUT",
                    "Legacy DOC conversion timed out",
                    504,
                ) from exc

            if process.returncode != 0:
                raise self._conversion_failed()

            outputs = [path for path in output_dir.iterdir() if path.suffix.lower() == ".docx"]
            if len(outputs) != 1 or not outputs[0].is_file():
                raise self._conversion_failed()
            converted = outputs[0]
            output_bytes = converted.stat().st_size
            if output_bytes <= 0 or output_bytes > self.max_output_bytes:
                raise LinkParseError(
                    "INVALID_WORD_DOCUMENT",
                    "Converted DOCX exceeds the resource limit",
                    422,
                )
            validate_docx_package(converted)
            return LegacyDocConversion(
                path=converted,
                work_dir=work_dir,
                metadata={
                    "converter": "libreoffice",
                    "source_format": "doc",
                    "target_format": "docx",
                    "input_bytes": source.stat().st_size,
                    "output_bytes": output_bytes,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
        except (EngineUnavailable, LinkParseError):
            shutil.rmtree(work_dir, ignore_errors=True)
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            raise self._conversion_failed() from exc

    @staticmethod
    def _conversion_failed() -> LinkParseError:
        return LinkParseError(
            "INVALID_WORD_DOCUMENT",
            "Legacy DOC file is damaged, encrypted, or cannot be converted",
            422,
        )
