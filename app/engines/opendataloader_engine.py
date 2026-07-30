import json
from pathlib import Path
from typing import Any

from app.core.errors import EngineUnavailable, LinkParseError

SUFFIXES = {"text": ".txt", "markdown": ".md", "html": ".html", "json": ".json"}


class OpenDataLoaderEngine:
    name = "opendataloader"

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
        if not self.available():
            raise EngineUnavailable(self.name)
        output_dir.mkdir(parents=True, exist_ok=True)
        image_dir = output_dir / "images"
        try:
            import opendataloader_pdf

            opendataloader_pdf.convert(
                input_path=[str(path)],
                output_dir=str(output_dir),
                format=",".join(sorted(formats)),
                image_output="external" if include_images else "off",
                image_format="png",
                image_dir=str(image_dir) if include_images else None,
            )
        except Exception as exc:
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
        return outputs, images
