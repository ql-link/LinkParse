from pathlib import Path

from fastapi import UploadFile

from app.core.errors import LinkParseError
from app.services.file_validate import ALLOWED_TYPES, validate_file_header


async def save_upload(
    upload: UploadFile, destination: Path, max_bytes: int
) -> tuple[Path, str, str, int]:
    header = await upload.read(16)
    media_type, safe_name = validate_file_header(header, upload.filename)
    if not destination.suffix:
        destination = destination.with_suffix(ALLOWED_TYPES[media_type])
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with destination.open("wb") as target:
            if header:
                target.write(header)
                size += len(header)
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise LinkParseError(
                        "FILE_TOO_LARGE",
                        f"Uploaded file exceeds {max_bytes // 1024 // 1024}MB limit",
                        413,
                    )
                target.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return destination, media_type, safe_name, size
