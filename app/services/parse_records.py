import logging
from typing import Any

from sqlalchemy import select

from app.core.errors import LinkParseError
from app.core.security import AuthContext
from app.db import Database, ParseRecord, utcnow

logger = logging.getLogger("linkparse.records")


def create_parse_record(
    database: Database,
    auth: AuthContext,
    *,
    request_id: str,
    job_id: str | None,
    filename: str,
    mode: str,
    engine: str,
    status: str = "processing",
) -> int | None:
    if not database.configured:
        return None
    try:
        with database.session() as session:
            record = ParseRecord(
                user_id=auth.user_id,
                api_key_id=auth.api_key_id,
                request_id=request_id,
                job_id=job_id,
                filename=filename[:512],
                mode=mode,
                status=status,
                engine=engine,
            )
            session.add(record)
            session.flush()
            return record.id
    except Exception as exc:
        logger.exception("parse_record_create_failed request_id=%s", request_id)
        raise LinkParseError(
            "DATABASE_UNAVAILABLE",
            "Unable to create parse record; retry with a new request ID",
            503,
        ) from exc


def update_parse_record(database: Database, record_id: int | None, **values: Any) -> None:
    if not record_id or not database.configured:
        return
    try:
        with database.session() as session:
            record = session.get(ParseRecord, record_id)
            if not record:
                return
            for name, value in values.items():
                setattr(record, name, value)
            if values.get("status") in {"succeeded", "failed", "expired"}:
                record.completed_at = utcnow()
    except Exception:
        logger.exception("parse_record_update_failed record_id=%s", record_id)


def record_owned_by(database: Database, job_id: str, auth: AuthContext) -> bool:
    if auth.is_admin:
        return True
    if auth.user_id is None or not database.configured:
        return False
    with database.session() as session:
        return (
            session.scalar(
                select(ParseRecord.id).where(
                    ParseRecord.job_id == job_id,
                    ParseRecord.user_id == auth.user_id,
                )
            )
            is not None
        )
