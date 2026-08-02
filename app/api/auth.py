import re
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.errors import LinkParseError
from app.core.security import (
    AuthContext,
    bearer,
    build_session,
    create_session,
    password_hash,
    require_user,
    token_hash,
    verify_password,
)
from app.db import ApiKey, Database, ParseRecord, User, UserSession, get_database

router = APIRouter(prefix="/v1/auth", tags=["auth"])
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]{2,32}$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValueError("username contains unsupported characters")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("invalid email address")
        return value


class LoginRequest(BaseModel):
    account: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=1, max_length=128)


def auth_response(user: User, token: str, expires_at) -> dict:
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat() + "Z",
        "user": {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "is_admin": bool(user.is_admin),
            "created_at": user.created_at.isoformat() + "Z",
        },
    }


def is_bootstrap_admin(username: str, settings: Settings) -> bool:
    normalized = username.strip().casefold()
    return normalized in {item.casefold() for item in settings.bootstrap_admin_usernames}


@router.post("/register", status_code=201)
def register(
    payload: RegisterRequest,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=password_hash(payload.password),
        is_admin=is_bootstrap_admin(payload.username, settings),
    )
    try:
        with database.session() as session:
            session.add(user)
            session.flush()
            token, user_session = build_session(user.id, settings.session_ttl_hours)
            session.add(user_session)
            session.flush()
    except IntegrityError as exc:
        raise LinkParseError(
            "ACCOUNT_EXISTS", "Username or email is already registered", 409
        ) from exc
    return auth_response(user, token, user_session.expires_at)


@router.post("/login")
def login(
    payload: LoginRequest,
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    account = payload.account.strip().lower()
    with database.session() as session:
        user = session.scalar(
            select(User).where(
                (func.lower(User.email) == account) | (func.lower(User.username) == account)
            )
        )
        if (
            not user
            or user.status != "active"
            or not verify_password(payload.password, user.password_hash)
        ):
            raise LinkParseError("INVALID_CREDENTIALS", "Account or password is incorrect", 401)
        if not user.is_admin and is_bootstrap_admin(user.username, settings):
            user.is_admin = True
            session.flush()
    token, user_session = create_session(database, user.id, settings.session_ttl_hours)
    return auth_response(user, token, user_session.expires_at)


@router.post("/logout", status_code=204)
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    auth: Annotated[AuthContext, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
) -> None:
    del auth
    token = credentials.credentials if credentials else ""
    with database.session() as session:
        user_session = session.scalar(
            select(UserSession).where(UserSession.token_hash == token_hash(token))
        )
        if user_session:
            session.delete(user_session)


@router.get("/me")
def me(
    auth: Annotated[AuthContext, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
) -> dict:
    with database.session() as session:
        user = session.get(User, auth.user_id)
        active_keys = session.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(ApiKey.user_id == auth.user_id, ApiKey.status == "active")
        )
        parse_count = session.scalar(
            select(func.count()).select_from(ParseRecord).where(ParseRecord.user_id == auth.user_id)
        )
    if not user:
        raise LinkParseError("UNAUTHORIZED", "User no longer exists", 401)
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "is_admin": bool(user.is_admin),
        "created_at": user.created_at.isoformat() + "Z",
        "stats": {"active_keys": active_keys or 0, "parse_records": parse_count or 0},
    }
