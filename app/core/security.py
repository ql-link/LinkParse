from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.errors import LinkParseError
from app.db import ApiKey, Database, User, UserSession, get_database, utcnow

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    kind: str
    caller_id: str
    user_id: int | None = None
    api_key_id: int | None = None
    is_admin: bool = False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected)),
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (TypeError, ValueError):
        return False


def new_session_token() -> str:
    return f"lps_{secrets.token_urlsafe(36)}"


def new_api_key() -> str:
    return f"lpk_{secrets.token_urlsafe(36)}"


def _bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    return ""


def resolve_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
) -> AuthContext:
    token = _bearer_token(credentials)
    if not token:
        raise LinkParseError("UNAUTHORIZED", "Invalid or missing access token", 401)

    for configured_key in settings.api_keys:
        if secrets.compare_digest(token, configured_key):
            caller_id = hashlib.sha256(token.encode()).hexdigest()[:12]
            context = AuthContext("system_key", caller_id, is_admin=True)
            request.state.api_key_id = caller_id
            request.state.auth = context
            return context

    if not database.configured:
        raise LinkParseError("UNAUTHORIZED", "Invalid or missing access token", 401)

    now = utcnow()
    digest = token_hash(token)
    with database.session() as session:
        if token.startswith("lps_"):
            user_session = session.scalar(
                select(UserSession).where(
                    UserSession.token_hash == digest,
                    UserSession.expires_at > now,
                )
            )
            if user_session:
                user = session.get(User, user_session.user_id)
                if user and user.status == "active":
                    user_session.last_used_at = now
                    context = AuthContext(
                        "session",
                        f"user:{user.id}",
                        user_id=user.id,
                        is_admin=bool(user.is_admin),
                    )
                    request.state.api_key_id = context.caller_id
                    request.state.auth = context
                    return context
        elif token.startswith("lpk_"):
            api_key = session.scalar(
                select(ApiKey).where(ApiKey.key_hash == digest, ApiKey.status == "active")
            )
            if api_key and api_key.user.status == "active":
                api_key.last_used_at = now
                context = AuthContext(
                    "api_key",
                    api_key.prefix,
                    user_id=api_key.user_id,
                    api_key_id=api_key.id,
                    is_admin=bool(api_key.user.is_admin),
                )
                request.state.api_key_id = context.caller_id
                request.state.auth = context
                return context

    raise LinkParseError("UNAUTHORIZED", "Invalid or missing access token", 401)


def authenticate(context: Annotated[AuthContext, Depends(resolve_auth)]) -> AuthContext:
    return context


def require_user(context: Annotated[AuthContext, Depends(resolve_auth)]) -> AuthContext:
    if context.kind != "session" or context.user_id is None:
        raise LinkParseError("SESSION_REQUIRED", "Please sign in to continue", 401)
    return context


def require_admin(context: Annotated[AuthContext, Depends(resolve_auth)]) -> AuthContext:
    if not context.is_admin:
        raise LinkParseError("ADMIN_REQUIRED", "Administrator access is required", 403)
    return context


def build_session(user_id: int, ttl_hours: int) -> tuple[str, UserSession]:
    token = new_session_token()
    session_record = UserSession(
        user_id=user_id,
        token_hash=token_hash(token),
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    return token, session_record


def create_session(database: Database, user_id: int, ttl_hours: int) -> tuple[str, UserSession]:
    token, session_record = build_session(user_id, ttl_hours)
    with database.session() as session:
        session.add(session_record)
        session.flush()
    return token, session_record
