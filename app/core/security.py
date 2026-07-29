import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.errors import LinkParseError

bearer = HTTPBearer(auto_error=False)


def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    token = (
        credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
    )
    if not token or not any(secrets.compare_digest(token, key) for key in settings.api_keys):
        raise LinkParseError("UNAUTHORIZED", "Invalid or missing API key", 401)
    api_key_id = hashlib.sha256(token.encode()).hexdigest()[:12]
    request.state.api_key_id = api_key_id
    return api_key_id
