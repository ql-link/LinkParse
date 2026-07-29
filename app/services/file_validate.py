from pathlib import Path

from app.core.errors import LinkParseError

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
}


def sniff_media_type(header: bytes) -> str | None:
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_file_header(header: bytes, filename: str | None) -> tuple[str, str]:
    media_type = sniff_media_type(header)
    if media_type is None:
        raise LinkParseError("UNSUPPORTED_FILE_TYPE", "Unsupported or invalid file type", 415)
    safe_name = Path(filename or f"upload{ALLOWED_TYPES[media_type]}").name
    if safe_name in {"", ".", ".."}:
        safe_name = f"upload{ALLOWED_TYPES[media_type]}"
    return media_type, safe_name
