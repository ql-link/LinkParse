from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    inspect,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from app.core.config import Settings, get_settings
from app.core.errors import LinkParseError

IdType = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="user")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="api_keys")


class ParseRecord(Base):
    __tablename__ = "parse_records"
    __table_args__ = (
        Index("ix_parse_records_user_created", "user_id", "created_at"),
        Index("ix_parse_records_job_id", "job_id"),
        Index("ix_parse_records_request_id", "request_id"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    api_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    engine: Mapped[str] = mapped_column(String(40), nullable=False)
    detected_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Database:
    def __init__(self, url: str):
        self.url = url
        self.engine = None
        self.session_factory = None
        if url:
            kwargs = {"pool_pre_ping": True}
            if url.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False}
            self.engine = create_engine(url, **kwargs)
            self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
        self._initialized = False

    @property
    def configured(self) -> bool:
        return self.engine is not None

    def initialize(self) -> None:
        if not self.configured:
            raise LinkParseError("DATABASE_UNAVAILABLE", "Database is not configured", 503)
        if not self._initialized:
            Base.metadata.create_all(self.engine)
            self._upgrade_legacy_schema()
            self._initialized = True

    def _upgrade_legacy_schema(self) -> None:
        """Bring databases created by earlier releases up to the current schema."""
        assert self.engine is not None
        columns = {column["name"] for column in inspect(self.engine).get_columns("users")}
        if "is_admin" not in columns:
            with self.engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0")
                )
                # Preserve the established dev administrator without promoting future sign-ups.
                connection.execute(
                    text("UPDATE users SET is_admin = 1 WHERE LOWER(username) = 'root'")
                )

        self._allow_duplicate_parse_request_ids()

    def _allow_duplicate_parse_request_ids(self) -> None:
        """Keep request IDs as trace metadata rather than an idempotency boundary."""
        assert self.engine is not None
        if self.engine.dialect.name != "mysql":
            return

        inspector = inspect(self.engine)
        matching_constraints = [
            constraint
            for constraint in inspector.get_unique_constraints("parse_records")
            if constraint.get("column_names") == ["request_id"] and constraint.get("name")
        ]
        indexes = {index.get("name") for index in inspector.get_indexes("parse_records")}
        if not matching_constraints and "ix_parse_records_request_id" in indexes:
            return

        preparer = self.engine.dialect.identifier_preparer
        table_name = preparer.quote("parse_records")
        with self.engine.begin() as connection:
            for constraint in matching_constraints:
                constraint_name = preparer.quote(str(constraint["name"]))
                connection.execute(text(f"ALTER TABLE {table_name} DROP INDEX {constraint_name}"))
            if "ix_parse_records_request_id" not in indexes:
                index_name = preparer.quote("ix_parse_records_request_id")
                column_name = preparer.quote("request_id")
                connection.execute(
                    text(f"CREATE INDEX {index_name} ON {table_name} ({column_name})")
                )

    def ping(self) -> bool:
        if not self.configured:
            return False
        assert self.engine is not None
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True

    def delete_expired_sessions(self) -> int:
        if not self.configured:
            return 0
        with self.session() as session:
            result = session.execute(delete(UserSession).where(UserSession.expires_at <= utcnow()))
            return result.rowcount or 0

    @contextmanager
    def session(self) -> Iterator[Session]:
        self.initialize()
        assert self.session_factory is not None
        database_session = self.session_factory()
        try:
            yield database_session
            database_session.commit()
        except Exception:
            database_session.rollback()
            raise
        finally:
            database_session.close()


@lru_cache(maxsize=8)
def database_for_url(url: str) -> Database:
    return Database(url)


def get_database(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Database:
    return database_for_url(settings.database_url)
