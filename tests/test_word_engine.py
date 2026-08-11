from contextlib import contextmanager
from pathlib import Path

from app.core.config import Settings
from app.engines.word_engine import WordEngine
from app.services.file_validate import DOCX_MEDIA_TYPE, validate_docx_package
from app.services.parser import DocumentParser
from tests.docx_factory import write_docx


class _Limiter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    @contextmanager
    def slot(self, engine: str):
        self.calls.append(engine)
        yield


class _AssetStorage:
    def __init__(self) -> None:
        self.uploaded: list[Path] = []

    def upload_files(self, request_id, paths, *, kind, relative_to=None, pages=None):
        self.uploaded = list(paths)
        replacements = {
            path.relative_to(relative_to).as_posix(): f"https://assets.example/{path.name}"
            for path in paths
        }
        assets = [
            {
                "id": path.stem,
                "kind": kind,
                "filename": path.name,
                "media_type": "image/png",
                "size_bytes": path.stat().st_size,
                "url": replacements[path.relative_to(relative_to).as_posix()],
                "expires_at": None,
                "page": (pages or {}).get(path.relative_to(relative_to).as_posix()),
            }
            for path in paths
        ]
        return assets, replacements

    def rewrite_outputs(self, value, replacements):
        if isinstance(value, str):
            for source, target in replacements.items():
                value = value.replace(source, target)
            return value
        if isinstance(value, list):
            return [self.rewrite_outputs(item, replacements) for item in value]
        if isinstance(value, dict):
            return {key: self.rewrite_outputs(item, replacements) for key, item in value.items()}
        return value

    def delete_assets(self, assets):
        return 0


def test_word_engine_preserves_structure_and_preprocesses_formula(tmp_path):
    source = write_docx(tmp_path / "sample.docx", formula=True, page_break=True)
    validate_docx_package(source)

    result = WordEngine().parse(source, tmp_path / "output", include_images=False)

    assert "# 产品说明" in result.markdown
    assert "**重点**" in result.markdown
    assert "- 第一项" in result.markdown
    assert "子项" in result.markdown
    assert "| 字段 | 说明 |" in result.markdown
    assert "$x+1$" in result.markdown
    assert "<!-- WORD_PAGE:1 -->" in result.markdown
    assert "<!-- WORD_PAGE:2 -->" in result.markdown
    assert result.page_count == 2
    assert result.markdown.index("<!-- WORD_PAGE:2 -->") < result.markdown.index("第二页内容")
    assert result.metadata["formula_count"] == 1
    assert result.metadata["pagination_supported"] is True
    assert result.metadata["markdown_table_count"] == 1
    assert result.metadata["rag_text_table_count"] == 0
    assert result.metadata["rag_table_schema"] == "table-rag-v1"


def test_document_parser_uploads_word_images_and_rewrites_all_outputs(tmp_path):
    source = write_docx(tmp_path / "sample.docx", image=True)
    storage = _AssetStorage()
    limiter = _Limiter()
    parser = DocumentParser(
        Settings(data_dir=tmp_path, api_keys=["test"]),
        asset_storage=storage,
        concurrency_limiter=limiter,
    )

    result = parser.parse(
        source,
        "sample.docx",
        DOCX_MEDIA_TYPE,
        {"text", "json", "markdown", "html"},
        include_bbox=True,
        include_images=True,
        request_id="word-test",
    )

    assert result["engine"] == "mammoth_word"
    assert result["detected_type"] == "docx"
    assert result["meta"]["page_count"] == 1
    assert result["meta"]["word"]["bbox_supported"] is False
    assert set(result["outputs"]) == {"markdown"}
    assert limiter.calls == ["word"]
    assert len(storage.uploaded) == 1
    assert len(result["assets"]) == 1
    assert result["assets"][0]["page"] == 1
    assert "https://assets.example/" in result["outputs"]["markdown"]


def test_word_engine_uses_saved_rendered_page_breaks(tmp_path):
    source = write_docx(tmp_path / "rendered.docx", rendered_page_break=True)

    result = WordEngine().parse(source, tmp_path / "output", include_images=False)

    assert result.page_count == 2
    assert result.metadata["rendered_page_break_count"] == 1
    assert result.metadata["saved_page_break_count"] == 1
    assert "<!-- WORD_PAGE:2 -->\n\n第二页内容" in result.markdown
