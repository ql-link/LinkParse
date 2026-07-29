import pytest

from app.core.errors import LinkParseError
from app.services.file_validate import validate_file_header


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
