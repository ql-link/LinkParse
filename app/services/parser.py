import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from app.core.config import Settings
from app.core.errors import EngineUnavailable, LinkParseError
from app.engines.opendataloader_engine import OpenDataLoaderEngine
from app.engines.rapidocr_engine import RapidOCREngine
from app.engines.word_engine import WordEngine
from app.services.assets import OssAssetStorage
from app.services.concurrency import ConcurrencyLimiter
from app.services.file_validate import DOC_MEDIA_TYPE, DOCX_MEDIA_TYPE
from app.services.format_convert import fill_missing_outputs, outputs_from_pages
from app.services.legacy_doc import LegacyDocConversion, LegacyDocConverter
from app.services.pdf import render_pdf, validate_pdf
from app.services.pdf_quality import analyze_pdf_quality
from app.services.pdf_structure import analyze_pdf_markdown

VALID_FORMATS = {"text", "json", "markdown", "html"}


def engine_for_media_type(media_type: str) -> str:
    if media_type.startswith("image/"):
        return "rapidocr"
    if media_type == "application/pdf":
        return "opendataloader_ocr"
    if media_type in {DOC_MEDIA_TYPE, DOCX_MEDIA_TYPE}:
        return "mammoth_word"
    return "unsupported"


def parse_formats(value: str) -> set[str]:
    formats = {item.strip().lower() for item in value.split(",") if item.strip()}
    invalid = formats - VALID_FORMATS
    if not formats or invalid:
        raise LinkParseError("INVALID_ARGUMENT", f"Invalid output formats: {sorted(invalid)}", 422)
    return formats


class DocumentParser:
    def __init__(
        self,
        settings: Settings,
        asset_storage: OssAssetStorage | None = None,
        concurrency_limiter: ConcurrencyLimiter | None = None,
        legacy_doc_converter: LegacyDocConverter | None = None,
    ) -> None:
        self.settings = settings
        self._ocr: RapidOCREngine | None = None
        self._structured: OpenDataLoaderEngine | None = None
        self._word: WordEngine | None = None
        self._legacy_doc_converter = legacy_doc_converter
        self.asset_storage = asset_storage or OssAssetStorage(settings)
        self.concurrency_limiter = concurrency_limiter or ConcurrencyLimiter(settings)

    @property
    def ocr(self) -> RapidOCREngine:
        if self._ocr is None:
            self._ocr = RapidOCREngine(
                intra_op_num_threads=self.settings.ort_intra_op_num_threads,
                inter_op_num_threads=self.settings.ort_inter_op_num_threads,
            )
        return self._ocr

    @property
    def structured(self) -> OpenDataLoaderEngine:
        if self._structured is None:
            self._structured = OpenDataLoaderEngine(
                timeout_seconds=self.settings.opendataloader_timeout_seconds,
                table_method=self.settings.opendataloader_table_method,
                markdown_with_html=self.settings.opendataloader_markdown_with_html,
                max_output_files=self.settings.opendataloader_max_output_files,
                max_output_bytes=self.settings.opendataloader_max_output_mb * 1024 * 1024,
            )
        return self._structured

    @property
    def word(self) -> WordEngine:
        if self._word is None:
            self._word = WordEngine()
        return self._word

    @property
    def legacy_doc_converter(self) -> LegacyDocConverter:
        if self._legacy_doc_converter is None:
            self._legacy_doc_converter = LegacyDocConverter(
                timeout_seconds=self.settings.doc_conversion_timeout_seconds,
                max_output_bytes=self.settings.max_upload_mb * 1024 * 1024,
            )
        return self._legacy_doc_converter

    def parse(
        self,
        path: Path,
        filename: str,
        media_type: str,
        formats: set[str],
        include_bbox: bool,
        include_images: bool,
        request_id: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        started = time.monotonic()
        assets: list[dict] = []
        pdf_metadata: dict | None = None
        word_metadata: dict | None = None
        if media_type.startswith("image/"):
            pages = [self._ocr_image(path, 1, include_bbox)]
            outputs = outputs_from_pages(pages, formats)
            if include_images:
                assets, _ = self.asset_storage.upload_files(
                    request_id, [path], kind="source_image", pages={path.name: 1}
                )
            selected, detected, page_count = "rapidocr", "image", 1
        elif media_type == "application/pdf":
            page_count = validate_pdf(path, self.settings.max_pdf_pages)
            outputs, assets = self._parse_pdf_pipeline(
                path,
                formats,
                include_bbox,
                progress,
                include_images,
                request_id,
                page_count=page_count,
            )
            selected = "opendataloader_ocr"
            pdf_metadata = outputs.pop("_pdf_metadata")
            ocr_page_count = len(pdf_metadata["ocr_pages"])
            if ocr_page_count == 0:
                detected = "text_pdf"
            elif ocr_page_count == page_count:
                detected = "scanned_pdf"
            else:
                detected = "mixed_pdf"
        elif media_type in {DOC_MEDIA_TYPE, DOCX_MEDIA_TYPE}:
            outputs, assets, word_metadata = self._parse_word_pipeline(
                path,
                include_images,
                request_id,
                legacy_doc=media_type == DOC_MEDIA_TYPE,
            )
            if progress:
                progress(word_metadata["page_count"], word_metadata["page_count"])
            selected = "mammoth_word"
            detected = "doc" if media_type == DOC_MEDIA_TYPE else "docx"
            page_count = word_metadata["page_count"]
        else:
            raise LinkParseError("UNSUPPORTED_FILE_TYPE", "Unsupported file type", 415)
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
                "pdf": pdf_metadata,
                "word": word_metadata,
            },
        }

    def _parse_word_pipeline(
        self,
        path: Path,
        include_images: bool,
        request_id: str,
        legacy_doc: bool = False,
    ) -> tuple[dict, list[dict], dict]:
        output_dir = self.settings.data_dir / "tmp" / f"{path.stem}_word"
        assets: list[dict] = []
        conversion: LegacyDocConversion | None = None
        try:
            with self.concurrency_limiter.slot("word"):
                if not self.word.available():
                    raise EngineUnavailable("mammoth_word")
                source = path
                if legacy_doc:
                    conversion = self.legacy_doc_converter.convert(
                        path, self.settings.data_dir / "tmp"
                    )
                    source = conversion.path
                parsed = self.word.parse(source, output_dir, include_images)
            outputs = {"markdown": parsed.markdown}
            if include_images and parsed.image_paths:
                assets, replacements = self.asset_storage.upload_files(
                    request_id,
                    parsed.image_paths,
                    kind="embedded_image",
                    relative_to=output_dir,
                    pages=parsed.image_pages,
                )
                outputs = self.asset_storage.rewrite_outputs(outputs, replacements)
                parsed_metadata = self.asset_storage.rewrite_outputs(
                    parsed.metadata, replacements
                )
            else:
                parsed_metadata = parsed.metadata
            metadata = {
                **parsed_metadata,
                "source_format": "doc" if legacy_doc else "docx",
                "conversion": conversion.metadata if conversion else None,
            }
            return outputs, assets, metadata
        except Exception:
            self.asset_storage.delete_assets(assets)
            raise
        finally:
            if conversion:
                conversion.cleanup()
            shutil.rmtree(output_dir, ignore_errors=True)

    def _ocr_image(self, path: Path, page: int, include_bbox: bool) -> dict:
        with self.concurrency_limiter.slot("rapidocr"):
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

    def _parse_pdf_pipeline(
        self,
        path: Path,
        formats: set[str],
        include_bbox: bool,
        progress: Callable[[int, int], None] | None,
        include_images: bool = False,
        request_id: str = "unknown",
        page_count: int | None = None,
    ) -> tuple[dict, list[dict]]:
        output_dir = self.settings.data_dir / "tmp" / f"{path.stem}_odl"
        assets: list[dict] = []
        requested_formats = set(formats)
        pipeline_formats = formats | {"markdown", "json"}
        try:
            with self.concurrency_limiter.slot("opendataloader"):
                outputs, image_paths = self.structured.parse(
                    path, output_dir, pipeline_formats, include_images
                )
            outputs = fill_missing_outputs(outputs, pipeline_formats)
            markdown = outputs.get("markdown")
            if not isinstance(markdown, str) or not markdown.strip():
                raise LinkParseError(
                    "PDF_QUALITY_FAILED", "OpenDataLoader produced no Markdown", 422
                )
            initial_quality = self._analyze_quality(path, markdown)
            if not initial_quality["page_provenance_valid"]:
                raise LinkParseError(
                    "PDF_PAGE_PROVENANCE_INVALID",
                    "OpenDataLoader page markers do not match the source PDF",
                    422,
                )
            if include_images:
                image_pages = getattr(self.structured, "image_pages", {}) or self._image_pages(
                    outputs.get("json")
                )
                assets, replacements = self.asset_storage.upload_files(
                    request_id,
                    image_paths,
                    kind="embedded_image",
                    relative_to=output_dir,
                    pages=image_pages,
                )
                outputs = self.asset_storage.rewrite_outputs(outputs, replacements)
                markdown = outputs["markdown"]
            ocr_page_numbers = set(initial_quality["ocr_required_pages"])
            ocr_pages: dict[int, dict] = {}
            if ocr_page_numbers:
                ocr_outputs, ocr_assets = self._ocr_pdf(
                    path,
                    pipeline_formats,
                    self.settings.pdf_fallback_render_dpi,
                    include_bbox,
                    progress,
                    include_images,
                    request_id,
                    ocr_page_numbers,
                )
                for page in ocr_outputs.get("json", {}).get("pages", []):
                    if isinstance(page, dict) and isinstance(page.get("page"), int):
                        ocr_pages[page["page"]] = page
                outputs = self._merge_page_fallback(outputs, ocr_outputs)
                assets.extend(ocr_assets)
            markdown = outputs.get("markdown")
            final_quality = self._analyze_quality(path, markdown, ocr_pages=ocr_pages)
            if final_quality["status"] != "PASSED":
                raise LinkParseError(
                    "PDF_QUALITY_FAILED",
                    f"PDF quality gate failed: {final_quality['status']}",
                    422,
                )
            structure = analyze_pdf_markdown(markdown, page_count or 0)
            if "text" in requested_formats:
                outputs["text"] = self._markdown_to_text(markdown)
            for name in tuple(outputs):
                if name not in requested_formats:
                    outputs.pop(name, None)
            outputs["_pdf_metadata"] = {
                "pipeline": "opendataloader_ocr",
                "opendataloader": getattr(self.structured, "last_metadata", {}),
                "initial_quality": initial_quality,
                "final_quality": final_quality,
                "ocr_pages": sorted(ocr_pages),
                "structure": structure,
                "page_provenance_complete": structure["page_provenance_complete"],
                "warnings": structure["warnings"],
            }
            return outputs, assets
        except Exception:
            self.asset_storage.delete_assets(assets)
            raise
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    @staticmethod
    def _merge_page_fallback(structured: dict, ocr: dict) -> dict:
        fallback_pages = ocr.get("json", {}).get("pages", [])
        page_text = {
            page["page"]: page.get("text", "")
            for page in fallback_pages
            if isinstance(page, dict) and isinstance(page.get("page"), int)
        }
        for name in ("text", "markdown", "html"):
            if ocr.get(name):
                current = structured.get(name, "")
                if page_text and "ODL_PAGE:" in current:
                    structured[name] = DocumentParser._append_marked_pages(current, page_text, name)
                else:
                    separator = "\n\n" if name != "html" else ""
                    structured[name] = f"{current}{separator}{ocr[name]}".strip()
        if "json" in ocr:
            if isinstance(structured.get("json"), dict):
                structured["json"]["ocr_fallback_pages"] = fallback_pages
            else:
                structured["json"] = {
                    "opendataloader": structured.get("json"),
                    "ocr_fallback_pages": fallback_pages,
                }
        return structured

    @staticmethod
    def _append_marked_pages(content: str, pages: dict[int, str], output_format: str) -> str:
        marker = re.compile(r"<!--\s*ODL_PAGE:(\d+)\s*-->")
        matches = list(marker.finditer(content))
        if not matches:
            return content
        chunks = [content[: matches[0].start()]]
        for index, match in enumerate(matches):
            page = int(match.group(1))
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            if page not in pages:
                chunks.append(content[match.start() : end])
                continue
            text = pages[page]
            if output_format == "markdown":
                original = content[match.start() : end].strip()
                replacement = f"{original}\n\n<!-- PAGE_FALLBACK:OCR -->\n\n{text}\n\n"
            elif output_format == "html":
                import html

                original = content[match.start() : end].strip()
                replacement = (
                    f'{original}<section data-page="{page}" data-source="ocr">'
                    f"<p>{html.escape(text).replace(chr(10), '<br>')}</p></section>"
                )
            else:
                original = content[match.start() : end].strip()
                replacement = f"{original}\n\n{text}\n\n"
            chunks.append(replacement)
        return "".join(chunks).strip()

    def _analyze_quality(
        self,
        path: Path,
        markdown: str,
        *,
        ocr_pages: dict[int, dict] | None = None,
    ) -> dict:
        return analyze_pdf_quality(
            path,
            markdown,
            ocr_pages=ocr_pages,
            min_effective_text_chars=self.settings.pdf_quality_min_effective_text_chars,
            image_only_max_text_chars=self.settings.pdf_quality_image_only_max_text_chars,
            image_only_min_coverage_ratio=(self.settings.pdf_quality_image_only_min_coverage_ratio),
            min_ocr_confidence=self.settings.pdf_quality_min_ocr_confidence,
            min_text_retention_ratio=self.settings.pdf_quality_min_text_retention_ratio,
        )

    @staticmethod
    def _markdown_to_text(markdown: str) -> str:
        value = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
        value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", value)
        value = re.sub(r"<[^>]+>", "", value)
        value = re.sub(r"[#*_`>|~-]", "", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()

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
