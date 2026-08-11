import pytest

from app.core.errors import LinkParseError
from app.services.file_validate import (
    DOC_MEDIA_TYPE,
    DOCX_MEDIA_TYPE,
    OLE_COMPOUND_FILE_MAGIC,
    validate_docx_package,
    validate_file_header,
)
from tests.docx_factory import write_docx


@pytest.mark.parametrize(
    ("header", "media_type"),
    [
        (b"%PDF-1.7", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
    ],
)
def test_detects_content_by_magic_bytes(header, media_type):
    detected, filename = validate_file_header(header, "../unsafe.pdf")
    assert detected == media_type
    assert filename == "unsafe.pdf"


def test_rejects_unknown_binary():
    with pytest.raises(LinkParseError) as caught:
        validate_file_header(b"MZ executable", "payload.pdf")
    assert caught.value.code == "UNSUPPORTED_FILE_TYPE"


def test_docx_is_a_filename_gated_zip_candidate_and_then_container_validated(tmp_path):
    media_type, filename = validate_file_header(b"PK\x03\x04archive", "report.docx")
    assert media_type == DOCX_MEDIA_TYPE
    assert filename == "report.docx"

    source = write_docx(tmp_path / "report.docx")
    validate_docx_package(source)


def test_doc_is_an_extension_gated_ole_candidate():
    media_type, filename = validate_file_header(OLE_COMPOUND_FILE_MAGIC, "../report.DOC")
    assert media_type == DOC_MEDIA_TYPE
    assert filename == "report.DOC"


def test_other_ole_compound_files_are_not_accepted_as_doc():
    with pytest.raises(LinkParseError) as caught:
        validate_file_header(OLE_COMPOUND_FILE_MAGIC, "report.xls")
    assert caught.value.code == "UNSUPPORTED_FILE_TYPE"


def test_plain_zip_is_not_accepted_as_docx(tmp_path):
    source = tmp_path / "fake.docx"
    source.write_bytes(b"PK\x03\x04not-a-valid-archive")
    with pytest.raises(LinkParseError) as caught:
        validate_docx_package(source)
    assert caught.value.code == "INVALID_WORD_DOCUMENT"
