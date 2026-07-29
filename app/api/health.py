import importlib.util

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    engines = {
        "pymupdf": importlib.util.find_spec("fitz") is not None,
        "rapidocr": importlib.util.find_spec("rapidocr") is not None,
        "opendataloader": importlib.util.find_spec("opendataloader_pdf") is not None,
    }
    return {"status": "ok" if all(engines.values()) else "degraded", "engines": engines}
