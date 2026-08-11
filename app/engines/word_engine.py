from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import mammoth
import mammoth.images
from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET
from PIL import Image, UnidentifiedImageError

from app.core.errors import LinkParseError
from app.engines.docx_math.preprocess import WORD_PAGE_BREAK_SENTINEL, preprocess_docx
from app.services.html_document import HtmlMarkdownRenderer, clean_semantic_html

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
IMAGE_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/webp": "webp",
    "image/tiff": "tiff",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000

LIST_STYLE_MAP = "\n".join(
    [
        "p.ListBullet => ul > li:fresh",
        "p.ListBullet2 => ul|ol > li > ul > li:fresh",
        "p.ListBullet3 => ul|ol > li > ul|ol > li > ul > li:fresh",
        "p.ListNumber => ol > li:fresh",
        "p.ListNumber2 => ol|ul > li > ol > li:fresh",
        "p.ListNumber3 => ol|ul > li > ol|ul > li > ol > li:fresh",
        "p[style-name='List Bullet'] => ul > li:fresh",
        "p[style-name='List Bullet 2'] => ul|ol > li > ul > li:fresh",
        "p[style-name='List Bullet 3'] => ul|ol > li > ul|ol > li > ul > li:fresh",
        "p[style-name='List Number'] => ol > li:fresh",
        "p[style-name='List Number 2'] => ol|ul > li > ol > li:fresh",
        "p[style-name='List Number 3'] => ol|ul > li > ol|ul > li > ol > li:fresh",
    ]
)


@dataclass(slots=True)
class WordParseResult:
    markdown: str
    page_count: int
    image_paths: list[Path]
    image_pages: dict[str, int]
    metadata: dict[str, Any]


class WordEngine:
    name = "mammoth_word"

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("mammoth") is not None

    def parse(self, source: Path, output_dir: Path, include_images: bool) -> WordParseResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        warnings: list[str] = []
        image_paths: dict[str, Path] = {}
        omitted_images = 0

        (
            processed_source,
            formula_count,
            saved_page_break_count,
            preprocess_warnings,
        ) = preprocess_docx(source, output_dir / "formula-preprocessed.docx")
        warnings.extend(preprocess_warnings)

        def convert_image(image: Any) -> dict[str, str]:
            nonlocal omitted_images
            if not include_images:
                omitted_images += 1
                return {"src": ""}
            content_type = (image.content_type or "").lower()
            extension = IMAGE_EXTENSIONS.get(content_type)
            if extension is None:
                omitted_images += 1
                warnings.append(f"Unsupported Word image type: {content_type or 'unknown'}")
                return {"src": ""}
            try:
                with image.open() as stream:
                    data = stream.read(MAX_IMAGE_BYTES + 1)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("embedded image exceeds 20MB")
                self._validate_image(data)
                digest = hashlib.sha256(data).hexdigest()
                relative = f"word-assets/{digest}.{extension}"
                target = output_dir / relative
                if digest not in image_paths:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    image_paths[digest] = target
                return {"src": relative}
            except Exception as exc:
                omitted_images += 1
                warnings.append(f"Word embedded image skipped: {exc}")
                return {"src": ""}

        try:
            with processed_source.open("rb") as source_file:
                result = mammoth.convert_to_html(
                    source_file,
                    convert_image=mammoth.images.img_element(convert_image),
                    style_map=LIST_STYLE_MAP,
                    include_embedded_style_map=False,
                )
        except Exception as exc:
            raise LinkParseError(
                "WORD_PARSE_FAILED", f"Mammoth conversion failed: {exc}", 422
            ) from exc

        mammoth_warnings = [
            f"{getattr(message, 'type', 'warning')}: {getattr(message, 'message', str(message))}"
            for message in result.messages
        ]
        warnings.extend(mammoth_warnings)
        _, cleaned_html, comment_count = clean_semantic_html(result.value or "")
        page_fragments = cleaned_html.split(WORD_PAGE_BREAK_SENTINEL)
        markdown_pages: list[str] = []
        image_pages: dict[str, int] = {}
        table_count = 0
        markdown_table_count = 0
        html_table_count = 0
        table_failure_count = 0

        for page_number, fragment in enumerate(page_fragments, start=1):
            soup = BeautifulSoup(fragment, "lxml")
            for paragraph in soup.find_all("p"):
                if not paragraph.get_text(" ", strip=True) and not paragraph.find("img"):
                    paragraph.decompose()
            root = soup.body or soup
            for image in root.find_all("img"):
                source_ref = str(image.get("src", ""))
                if source_ref:
                    image_pages.setdefault(source_ref, page_number)
            renderer = HtmlMarkdownRenderer(table_id_start=table_count + 1)
            page_markdown = renderer.render_children(root).strip()
            markdown_pages.append(
                f"<!-- WORD_PAGE:{page_number} -->"
                + (f"\n\n{page_markdown}" if page_markdown else "")
            )
            table_count += renderer.table_count
            markdown_table_count += renderer.markdown_table_count
            html_table_count += renderer.html_table_count
            table_failure_count += renderer.table_failure_count
            warnings.extend(renderer.warnings)

        markdown = "\n\n".join(markdown_pages)
        if not any(page.partition("\n\n")[2].strip() for page in markdown_pages):
            raise LinkParseError("WORD_PARSE_FAILED", "Word document has no effective content", 422)
        layout = self._read_layout_metadata(source)
        detected_page_break_count = max(0, len(page_fragments) - 1)
        if detected_page_break_count != saved_page_break_count:
            warnings.append(
                "Saved Word page-break markers were not preserved one-to-one by Mammoth: "
                f"source={saved_page_break_count}, output={detected_page_break_count}"
            )
        metadata = {
            "pipeline": "mammoth_html_markdown",
            "pagination_supported": True,
            "pagination_source": "saved_docx_page_breaks",
            "page_count": len(page_fragments),
            "bbox_supported": False,
            "section_count": layout["section_count"],
            "explicit_page_break_count": layout["explicit_page_break_count"],
            "rendered_page_break_count": layout["rendered_page_break_count"],
            "saved_page_break_count": saved_page_break_count,
            "table_count": table_count,
            "table_ir_version": 1,
            "markdown_table_count": markdown_table_count,
            "html_table_count": html_table_count,
            "table_failure_count": table_failure_count,
            "image_count": len(image_paths),
            "omitted_image_count": omitted_images,
            "formula_count": formula_count,
            "comment_removed_count": comment_count,
            "mammoth_warning_count": len(mammoth_warnings),
            "warnings": warnings,
        }
        return WordParseResult(
            markdown=markdown,
            page_count=len(page_fragments),
            image_paths=sorted(image_paths.values()),
            image_pages=image_pages,
            metadata=metadata,
        )

    @staticmethod
    def _validate_image(data: bytes) -> None:
        try:
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("embedded image dimensions exceed the resource limit")
                image.verify()
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise ValueError("embedded image is invalid") from exc

    @staticmethod
    def _read_layout_metadata(source: Path) -> dict[str, int]:
        try:
            with zipfile.ZipFile(source) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            explicit = sum(
                1
                for element in root.iter(f"{{{WORD_NS}}}br")
                if element.attrib.get(f"{{{WORD_NS}}}type") == "page"
            )
            rendered = sum(1 for _ in root.iter(f"{{{WORD_NS}}}lastRenderedPageBreak"))
            sections = sum(1 for _ in root.iter(f"{{{WORD_NS}}}sectPr"))
            return {
                "section_count": max(1, sections),
                "explicit_page_break_count": explicit,
                "rendered_page_break_count": rendered,
            }
        except Exception:
            return {
                "section_count": 1,
                "explicit_page_break_count": 0,
                "rendered_page_break_count": 0,
            }
