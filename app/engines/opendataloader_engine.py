import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.core.errors import EngineUnavailable, LinkParseError
from app.services.pdf_structure import image_page_map

SUFFIXES = {"text": ".txt", "markdown": ".md", "html": ".html", "json": ".json"}


class OpenDataLoaderEngine:
    name = "opendataloader"
    max_log_bytes = 10 * 1024 * 1024

    def __init__(
        self,
        *,
        timeout_seconds: float = 300,
        table_method: str = "default",
        markdown_with_html: bool = False,
        max_output_files: int = 2000,
        max_output_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if table_method not in {"default", "cluster"}:
            raise ValueError("table_method must be default or cluster")
        if max_output_files < 1 or max_output_bytes < 1:
            raise ValueError("OpenDataLoader output limits must be positive")
        self.timeout_seconds = timeout_seconds
        self.table_method = table_method
        self.markdown_with_html = markdown_with_html
        self.max_output_files = max_output_files
        self.max_output_bytes = max_output_bytes
        self.last_metadata: dict[str, Any] = {}
        self.image_pages: dict[str, int] = {}

    def available(self) -> bool:
        try:
            import opendataloader_pdf  # noqa: F401

            return True
        except ImportError:
            return False

    def parse(
        self,
        path: Path,
        output_dir: Path,
        formats: set[str],
        include_images: bool = False,
    ) -> tuple[dict[str, Any], list[Path]]:
        self.last_metadata = {}
        self.image_pages = {}
        if not self.available():
            raise EngineUnavailable(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        image_dir = output_dir / "images"
        command = [
            sys.executable,
            "-m",
            "app.engines.opendataloader_worker",
            "--input",
            str(path.resolve()),
            "--output-dir",
            str(output_dir.resolve()),
            "--image-dir",
            str(image_dir.resolve()),
            "--formats",
            ",".join(sorted(formats)),
            "--table-method",
            self.table_method,
        ]
        if self.markdown_with_html:
            command.append("--markdown-with-html")
        if include_images:
            command.append("--include-images")
        started = time.monotonic()
        try:
            with tempfile.TemporaryFile(mode="w+b") as stderr_file:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                    start_new_session=True,
                )
                try:
                    while process.poll() is None:
                        if time.monotonic() - started >= self.timeout_seconds:
                            raise LinkParseError(
                                "ENGINE_TIMEOUT",
                                f"OpenDataLoader exceeded {self.timeout_seconds:g} seconds",
                                504,
                            )
                        self._enforce_output_limits(output_dir)
                        if os.fstat(stderr_file.fileno()).st_size > self.max_log_bytes:
                            raise LinkParseError(
                                "PDF_RESOURCE_LIMIT",
                                "OpenDataLoader logs exceeded the resource limit",
                                413,
                            )
                        time.sleep(0.1)
                    self._enforce_output_limits(output_dir)
                except Exception:
                    self._terminate(process)
                    raise
                stderr_size = stderr_file.seek(0, os.SEEK_END)
                stderr_file.seek(max(0, stderr_size - 2000))
                stderr = stderr_file.read(2000).decode("utf-8", errors="replace")
                if process.returncode:
                    raise LinkParseError(
                        "ENGINE_UNAVAILABLE",
                        f"OpenDataLoader failed: {stderr.strip() or 'worker exited unexpectedly'}",
                        503,
                    )
        except LinkParseError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise LinkParseError(
                "ENGINE_UNAVAILABLE", f"OpenDataLoader failed: {exc}", 503
            ) from exc

        outputs: dict[str, Any] = {}
        files = list(output_dir.rglob("*"))
        for name, suffix in SUFFIXES.items():
            if name not in formats:
                continue
            candidate = next(
                (item for item in files if item.is_file() and item.suffix.lower() == suffix), None
            )
            if candidate is None:
                continue
            content = candidate.read_text(encoding="utf-8")
            outputs[name] = json.loads(content) if name == "json" else content
        if not outputs:
            raise LinkParseError(
                "ENGINE_UNAVAILABLE", "OpenDataLoader produced no readable output", 503
            )
        images = sorted(item for item in image_dir.rglob("*") if item.is_file())
        markdown = outputs.get("markdown")
        self.image_pages = image_page_map(markdown) if isinstance(markdown, str) else {}
        self.last_metadata = {
            "duration_ms": round((time.monotonic() - started) * 1000),
            "table_method": self.table_method,
            "markdown_with_html": self.markdown_with_html,
            "output_file_count": sum(item.is_file() for item in output_dir.rglob("*")),
            "output_bytes": sum(
                item.stat().st_size for item in output_dir.rglob("*") if item.is_file()
            ),
        }
        return outputs, images

    def _enforce_output_limits(self, output_dir: Path) -> None:
        file_count = 0
        total_bytes = 0
        for item in output_dir.rglob("*"):
            if not item.is_file():
                continue
            file_count += 1
            total_bytes += item.stat().st_size
            if file_count > self.max_output_files or total_bytes > self.max_output_bytes:
                raise LinkParseError(
                    "PDF_RESOURCE_LIMIT",
                    "OpenDataLoader output exceeded the configured resource limits",
                    413,
                )

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            process.wait()
