import logging
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, health, info, jobs, parse
from app.core.config import get_settings
from app.core.errors import LinkParseError
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
settings.ensure_directories()
logger = logging.getLogger("linkparse")

app = FastAPI(
    title="LinkParse Document Parsing API",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
)
app.include_router(health.router)
app.include_router(info.router)
app.include_router(parse.router)
app.include_router(jobs.router)
app.include_router(admin.router)

WEB_DIR = Path(__file__).parent / "web"
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/", include_in_schema=False)
def console() -> FileResponse:
    return FileResponse(
        WEB_DIR / "index.html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; connect-src 'self'; "
                "img-src 'self' data: https://*.aliyuncs.com; "
                "style-src 'self'; script-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@app.get("/docs", include_in_schema=False)
def api_documentation() -> FileResponse:
    return FileResponse(
        WEB_DIR / "docs.html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; connect-src 'self'; "
                "img-src 'self' data: https://*.aliyuncs.com; "
                "style-src 'self'; script-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def request_id_for(request: Request) -> str:
    return getattr(request.state, "request_id", f"req_{uuid.uuid4().hex}")


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied_request_id = request.headers.get("x-request-id", "")
    request_id = (
        supplied_request_id
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else f"req_{uuid.uuid4().hex}"
    )
    request.state.request_id = request_id
    started = time.monotonic()
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    logger.info(
        "request_complete request_id=%s caller=%s method=%s path=%s status=%s duration_ms=%s",
        request_id,
        getattr(request.state, "api_key_id", "anonymous"),
        request.method,
        request.url.path,
        response.status_code,
        round((time.monotonic() - started) * 1000),
    )
    return response


@app.exception_handler(LinkParseError)
async def linkparse_error_handler(request: Request, exc: LinkParseError) -> JSONResponse:
    request_id = request_id_for(request)
    logger.warning(
        "request_failed request_id=%s caller=%s code=%s path=%s",
        request_id,
        getattr(request.state, "api_key_id", "anonymous"),
        exc.code,
        request.url.path,
    )
    headers = {"WWW-Authenticate": "Bearer"} if exc.code == "UNAUTHORIZED" else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "request_id": request_id}},
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = request_id_for(request)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_ARGUMENT",
                "message": "Request parameters are invalid",
                "request_id": request_id,
            }
        },
    )


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_for(request)
    logger.exception("unhandled_error request_id=%s path=%s", request_id, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Internal server error",
                "request_id": request_id,
            }
        },
    )
