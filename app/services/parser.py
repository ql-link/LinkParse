import shutil
import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import Settings
from app.core.errors import EngineUnavailable, LinkParseError
from app.engines.opendataloader_engine import OpenDataLoaderEngine
from app.engines.rapidocr_engine import RapidOCREngine
from app.services.assets import OssAssetStorage
from app.services.format_convert import fill_missing_outputs, outputs_from_pages
from app.services.pdf import PdfInfo, inspect_pdf, render_pdf

VALID_FORMATS = {"text", "json", "markdown", "html"}


def parse_formats(value: str) -> set[str]:
    formats = {item.strip().lower() for item in value.split(",") if item.strip()}
    invalid = formats - VALID_FORMATS
    if not formats or invalid:
        raise LinkParseError("INVALID_ARGUMENT", f"Invalid output formats: {sorted(invalid)}", 422)
    return formats


class DocumentParser:
    def __init__(
        self, settings: Settings, asset_storage: OssAssetStorage | None = None
    ) -> None:
        self.settings = settings
        self.ocr = RapidOCREngine(
            intra_op_num_threads=settings.ort_intra_op_num_threads,
            inter_op_num_threads=settings.ort_inter_op_num_threads,
        )
        self.structured = OpenDataLoaderEngine()
        self.asset_storage = asset_storage or OssAssetStorage(settings)

    def parse(
        self,
        path: Path,
        filename: str,
        media_type: str,
        engine: str,
        formats: set[str],
        ocr_mode: str,
        dpi: int,
        include_bbox: bool,
        include_images: bool,
        request_id: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        started = time.monotonic()
        assets: list[dict] = []
        if dpi < 72 or dpi > self.settings.max_dpi:
            raise LinkParseError(
                "INVALID_ARGUMENT", f"dpi must be between 72 and {self.settings.max_dpi}", 422
            )
        if media_type.startswith("image/"):
            if ocr_mode == "never" or engine == "opendataloader":
                raise LinkParseError("INVALID_ARGUMENT", "Images require RapidOCR", 422)
            pages = [self._ocr_image(path, 1, include_bbox)]
            outputs = outputs_from_pages(pages, formats)
            if include_images:
                assets, _ = self.asset_storage.upload_files(
                    request_id, [path], kind="source_image", pages={path.name: 1}
                )
            selected, detected, page_count = "rapidocr", "image", 1
        else:
            info = inspect_pdf(path, self.settings.max_pdf_pages, self.settings.text_threshold)
            selected = self._select_pdf_engine(engine, ocr_mode, info)
            detected, page_count = info.detected_type, info.page_count
            if selected == "rapidocr":
                outputs, assets = self._ocr_pdf(
                    path,
                    formats,
                    dpi,
                    include_bbox,
                    progress,
                    include_images,
                    request_id,
                )
            else:
                ocr_pages = None
                if engine == "auto" and info.detected_type == "mixed_pdf":
                    ocr_pages = {
                        index + 1
                        for index, length in enumerate(info.page_text_lengths)
                        if length < self.settings.text_threshold
                    }
                outputs, assets = self._parse_structured_with_fallback(
                    path,
                    formats,
                    dpi,
                    include_bbox,
                    engine == "auto",
                    progress,
                    ocr_pages,
                    include_images,
                    request_id,
                )
                selected = outputs.pop("_engine")
        return {
            "request_id": request_id,
            "filename": filename,
            "engine": selected,
            "detected_type": detected,
            "outputs": outputs,
            "assets": assets,
            "meta": {
                "page_count": page_count,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        }

    def _select_pdf_engine(self, engine: str, ocr_mode: str, info: PdfInfo) -> str:
        if engine != "auto":
            return engine
        if ocr_mode == "always":
            return "rapidocr"
        if ocr_mode == "never":
            return "opendataloader"
        return "rapidocr" if info.detected_type == "scanned_pdf" else "opendataloader"

    def _ocr_image(self, path: Path, page: int, include_bbox: bool) -> dict:
        if not self.ocr.available():
            raise EngineUnavailable("rapidocr")
        return self.ocr.parse_image(path, page, include_bbox)

    def _ocr_pdf(
        self,
        path: Path,
        formats: set[str],
        dpi: int,
        include_bbox: bool,
        progress: Callable[[int, int], None] | None,
        include_images: bool,
        request_id: str,
        page_numbers: set[int] | None = None,
    ) -> tuple[dict, list[dict]]:
        render_dir = self.settings.data_dir / "tmp" / path.stem
        try:
            images = render_pdf(path, render_dir, dpi, page_numbers)
            pages = []
            for index, image_path in enumerate(images):
                page_number = int(image_path.stem.removeprefix("page_"))
                pages.append(self._ocr_image(image_path, page_number, include_bbox))
                if progress:
                    progress(index + 1, len(images))
            outputs = outputs_from_pages(pages, formats)
            assets: list[dict] = []
            if include_images:
                page_map = {
                    image_path.name: int(image_path.stem.removeprefix("page_"))
                    for image_path in images
                }
                assets, _ = self.asset_storage.upload_files(
                    request_id,
                    images,
                    kind="page_image",
                    pages=page_map,
                )
            return outputs, assets
        finally:
            shutil.rmtree(render_dir, ignore_errors=True)

    def _parse_structured_with_fallback(
        self,
        path: Path,
        formats: set[str],
        dpi: int,
        include_bbox: bool,
        allow_fallback: bool,
        progress: Callable[[int, int], None] | None,
        ocr_page_numbers: set[int] | None = None,
        include_images: bool = False,
        request_id: str = "unknown",
    ) -> tuple[dict, list[dict]]:
        output_dir = self.settings.data_dir / "tmp" / f"{path.stem}_odl"
        assets: list[dict] = []
        try:
            outputs, image_paths = self.structured.parse(
                path, output_dir, formats, include_images
            )
            outputs = fill_missing_outputs(outputs, formats)
            if include_images:
                image_pages = self._image_pages(outputs.get("json"))
                assets, replacements = self.asset_storage.upload_files(
                    request_id,
                    image_paths,
                    kind="embedded_image",
                    relative_to=output_dir,
                    pages=image_pages,
                )
                outputs = self.asset_storage.rewrite_outputs(outputs, replacements)
            if ocr_page_numbers:
                ocr_outputs, ocr_assets = self._ocr_pdf(
                    path,
                    formats,
                    dpi,
                    include_bbox,
                    progress,
                    include_images,
                    request_id,
                    ocr_page_numbers,
                )
                outputs = self._merge_ocr_fallback(outputs, ocr_outputs)
                assets.extend(ocr_assets)
            outputs["_engine"] = "opendataloader"
            return outputs, assets
        except LinkParseError:
            self.asset_storage.delete_assets(assets)
            if not allow_fallback:
                raise
            outputs, assets = self._ocr_pdf(
                path,
                formats,
                dpi,
                include_bbox,
                progress,
                include_images,
                request_id,
            )
            outputs["_engine"] = "rapidocr"
            return outputs, assets
        except Exception:
            self.asset_storage.delete_assets(assets)
            raise
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    @staticmethod
    def _merge_ocr_fallback(structured: dict, ocr: dict) -> dict:
        for name in ("text", "markdown", "html"):
            if ocr.get(name):
                separator = "\n\n" if name != "html" else ""
                structured[name] = f"{structured.get(name, '')}{separator}{ocr[name]}".strip()
        if "json" in ocr:
            fallback_pages = ocr["json"].get("pages", [])
            if isinstance(structured.get("json"), dict):
                structured["json"]["ocr_fallback_pages"] = fallback_pages
            else:
                structured["json"] = {
                    "opendataloader": structured.get("json"),
                    "ocr_fallback_pages": fallback_pages,
                }
        return structured

    @staticmethod
    def _image_pages(value: object) -> dict[str, int]:
        pages: dict[str, int] = {}

        def visit(item: object) -> None:
            if isinstance(item, dict):
                source = item.get("source")
                page = item.get("page number")
                if (
                    item.get("type") == "image"
                    and isinstance(source, str)
                    and isinstance(page, int)
                ):
                    pages[source] = page
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        return pages
