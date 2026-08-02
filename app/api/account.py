from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func, select

from app.core.errors import LinkParseError
from app.core.security import AuthContext, new_api_key, require_user, token_hash
from app.db import ApiKey, Database, ParseRecord, User, get_database

router = APIRouter(prefix="/v1/account", tags=["account"])


class CreateApiKeyRequest(BaseModel):
    name: str = Field(default="默认 Key", min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("API key name cannot be empty")
        return value


def key_payload(key: ApiKey) -> dict:
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.prefix,
        "status": key.status,
        "created_at": key.created_at.isoformat() + "Z",
        "last_used_at": key.last_used_at.isoformat() + "Z" if key.last_used_at else None,
    }


@router.get("/keys")
def list_keys(
    auth: Annotated[AuthContext, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
) -> dict:
    with database.session() as session:
        keys = session.scalars(
            select(ApiKey).where(ApiKey.user_id == auth.user_id).order_by(desc(ApiKey.created_at))
        ).all()
        return {"items": [key_payload(key) for key in keys]}


@router.post("/keys", status_code=201)
def create_key(
    payload: CreateApiKeyRequest,
    auth: Annotated[AuthContext, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
) -> dict:
    plaintext = new_api_key()
    with database.session() as session:
        # Serialize key creation per user so concurrent requests cannot bypass the limit.
        session.execute(select(User.id).where(User.id == auth.user_id).with_for_update())
        active_count = session.scalar(
            select(func.count())
            .select_from(ApiKey)
            .where(ApiKey.user_id == auth.user_id, ApiKey.status == "active")
        )
        if active_count and active_count >= 10:
            raise LinkParseError("KEY_LIMIT_REACHED", "At most 10 active API keys are allowed", 409)
        key = ApiKey(
            user_id=auth.user_id,
            name=payload.name,
            prefix=plaintext[:12],
            key_hash=token_hash(plaintext),
        )
        session.add(key)
        session.flush()
        result = key_payload(key)
    result["key"] = plaintext
    return result


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(
    key_id: int,
    auth: Annotated[AuthContext, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
) -> None:
    with database.session() as session:
        key = session.scalar(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == auth.user_id)
        )
        if not key:
            raise LinkParseError("KEY_NOT_FOUND", "API key not found", 404)
        key.status = "revoked"


def record_payload(record: ParseRecord) -> dict:
    return {
        "id": str(record.id),
        "request_id": record.request_id,
        "job_id": record.job_id,
        "filename": record.filename,
        "mode": record.mode,
        "status": record.status,
        "engine": record.engine,
        "detected_type": record.detected_type,
        "page_count": record.page_count,
        "duration_ms": record.duration_ms,
        "error": (
            {"code": record.error_code, "message": record.error_message}
            if record.error_code
            else None
        ),
        "created_at": record.created_at.isoformat() + "Z",
        "completed_at": record.completed_at.isoformat() + "Z" if record.completed_at else None,
    }


@router.get("/records")
def list_records(
    auth: Annotated[AuthContext, Depends(require_user)],
    database: Annotated[Database, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    with database.session() as session:
        total = session.scalar(
            select(func.count()).select_from(ParseRecord).where(ParseRecord.user_id == auth.user_id)
        )
        records = session.scalars(
            select(ParseRecord)
            .where(ParseRecord.user_id == auth.user_id)
            .order_by(desc(ParseRecord.created_at))
            .limit(limit)
            .offset(offset)
        ).all()
        return {"items": [record_payload(record) for record in records], "total": total or 0}
