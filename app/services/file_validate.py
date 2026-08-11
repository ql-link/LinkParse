import zipfile
from pathlib import Path, PurePosixPath

from defusedxml import ElementTree as ET

from app.core.errors import LinkParseError

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
}

DOC_MEDIA_TYPE = "application/msword"
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
OLE_COMPOUND_FILE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
DOCX_MAX_MEMBERS = 10_000
DOCX_MAX_ENTRY_BYTES = 128 * 1024 * 1024
DOCX_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
DOCX_MAX_COMPRESSION_RATIO = 500


def sniff_media_type(header: bytes, filename: str | None = None) -> str | None:
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
    if header.startswith(OLE_COMPOUND_FILE_MAGIC) and Path(filename or "").suffix.lower() == ".doc":
        return DOC_MEDIA_TYPE
    if header.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")) and Path(
        filename or ""
    ).suffix.lower() == ".docx":
        return DOCX_MEDIA_TYPE
    return None


def validate_file_header(header: bytes, filename: str | None) -> tuple[str, str]:
    media_type = sniff_media_type(header, filename)
    if media_type is None:
        raise LinkParseError("UNSUPPORTED_FILE_TYPE", "Unsupported or invalid file type", 415)
    safe_name = Path(filename or f"upload{ALLOWED_TYPES[media_type]}").name
    if safe_name in {"", ".", ".."}:
        safe_name = f"upload{ALLOWED_TYPES[media_type]}"
    return media_type, safe_name


def validate_saved_file(path: Path, media_type: str) -> None:
    if media_type == DOCX_MEDIA_TYPE:
        validate_docx_package(path)


def validate_docx_package(path: Path) -> None:
    """Validate that a ZIP is a bounded, non-encrypted WordprocessingML package."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > DOCX_MAX_MEMBERS:
                raise _invalid_docx("DOCX archive contains too many members")

            seen: set[str] = set()
            total_uncompressed = 0
            for info in members:
                name = info.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                if (
                    not name
                    or name.startswith("/")
                    or (len(name) > 1 and name[1] == ":")
                    or ".." in parts
                ):
                    raise _invalid_docx("DOCX archive contains an unsafe member path")
                if name in seen:
                    raise _invalid_docx("DOCX archive contains duplicate members")
                seen.add(name)
                if info.flag_bits & 0x1:
                    raise _invalid_docx("Encrypted DOCX files are not supported")
                if info.file_size > DOCX_MAX_ENTRY_BYTES:
                    raise _invalid_docx("DOCX archive member exceeds the resource limit")
                total_uncompressed += info.file_size
                if total_uncompressed > DOCX_MAX_UNCOMPRESSED_BYTES:
                    raise _invalid_docx("DOCX archive exceeds the uncompressed size limit")
                if info.file_size and (
                    not info.compress_size
                    or info.file_size / info.compress_size > DOCX_MAX_COMPRESSION_RATIO
                ):
                    raise _invalid_docx("DOCX archive has a suspicious compression ratio")

            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(seen):
                raise _invalid_docx("File is not a WordprocessingML document")
            content_types = archive.read("[Content_Types].xml")
            root = ET.fromstring(content_types)
            is_word_document = any(
                element.attrib.get("PartName") == "/word/document.xml"
                and element.attrib.get("ContentType") == DOCX_MAIN_CONTENT_TYPE
                for element in root.iter()
            )
            if not is_word_document:
                raise _invalid_docx("File is not a standard DOCX document")
    except LinkParseError:
        raise
    except (OSError, ET.ParseError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _invalid_docx("DOCX file is damaged or invalid") from exc


def _invalid_docx(message: str) -> LinkParseError:
    return LinkParseError("INVALID_WORD_DOCUMENT", message, 422)
