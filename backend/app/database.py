"""Async SQLAlchemy database setup."""
import asyncio
import logging

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from .config import settings

logger = logging.getLogger("corporate-chat")


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncSession:
    async with async_session_maker() as session:
        yield session


async def init_db(max_retries: int = 30, delay: float = 2.0) -> None:
    """Create all tables, retrying until the database is reachable.

    The database container may report 'healthy' (pg_isready) a moment before
    it accepts authenticated connections to the app database. Instead of
    crashing the whole process on a transient connection error (which makes
    the container exit and Docker mark it 'unhealthy'), we retry with backoff.
    """
    from . import models  # noqa: F401  ensure models are imported

    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.run_sync(_run_light_migrations)
            logger.info("Database ready (tables ensured) on attempt %d", attempt)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "Database not ready (attempt %d/%d): %s — retry in %.1fs",
                attempt, max_retries, e.__class__.__name__, delay,
            )
            await asyncio.sleep(delay)
    # Exhausted retries — re-raise the last error so the failure is visible.
    raise RuntimeError(f"Could not connect to database after {max_retries} attempts") from last_err


def _run_light_migrations(connection) -> None:
    """Add columns that may be missing on databases created by older versions.

    create_all() only creates missing *tables*, never missing *columns*, so we
    add new columns idempotently here. Works on PostgreSQL and SQLite.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())

    # column additions: table -> {column: SQL type with default}
    additions = {
        "users": {
            "auth_source": "VARCHAR(16) DEFAULT 'local'",
            "avatar_url": "VARCHAR(255) DEFAULT ''",
            "group_id": "INTEGER",
            "title": "VARCHAR(128) DEFAULT ''",
            "phone": "VARCHAR(64) DEFAULT ''",
            "office": "VARCHAR(128) DEFAULT ''",
        },
        "groups": {
            "description": "TEXT DEFAULT ''",
            "is_default": "BOOLEAN DEFAULT FALSE",
            "ad_group_dn": "VARCHAR(512) DEFAULT ''",
            "can_send_messages": "BOOLEAN DEFAULT TRUE",
            "can_create_private": "BOOLEAN DEFAULT TRUE",
            "can_create_groups": "BOOLEAN DEFAULT TRUE",
            "can_send_files": "BOOLEAN DEFAULT TRUE",
            "can_send_images": "BOOLEAN DEFAULT TRUE",
            "can_forward": "BOOLEAN DEFAULT TRUE",
            "can_pin": "BOOLEAN DEFAULT TRUE",
            "can_edit_own": "BOOLEAN DEFAULT TRUE",
            "can_delete_own": "BOOLEAN DEFAULT TRUE",
            "can_react": "BOOLEAN DEFAULT TRUE",
        },
        "chats": {
            "description": "TEXT DEFAULT ''",
            "avatar_url": "VARCHAR(255) DEFAULT ''",
            "ad_group_dn": "VARCHAR(512) DEFAULT ''",
        },
        "chat_members": {
            "is_muted": "BOOLEAN DEFAULT FALSE",
            "last_read_message_id": "INTEGER DEFAULT 0",
        },
        "messages": {
            "forwarded_from_name": "VARCHAR(128) DEFAULT ''",
            "is_pinned": "BOOLEAN DEFAULT FALSE",
            "is_system": "BOOLEAN DEFAULT FALSE",
            "attachment_kind": "VARCHAR(16) DEFAULT ''",
            "attachment_url": "VARCHAR(255) DEFAULT ''",
            "attachment_thumb": "VARCHAR(255) DEFAULT ''",
            "attachment_name": "VARCHAR(255) DEFAULT ''",
            "attachment_size": "INTEGER DEFAULT 0",
            "attachment_w": "INTEGER DEFAULT 0",
            "attachment_h": "INTEGER DEFAULT 0",
        },
    }

    for table, cols in additions.items():
        if table not in existing_tables:
            continue
        present = {c["name"] for c in inspector.get_columns(table)}
        for col, ddl in cols.items():
            if col not in present:
                connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}'))
                logger.info("Migration: added %s.%s", table, col)

    # ---- performance indexes (idempotent) ----
    # These speed up the hottest queries: chat membership lookups, message
    # listing per chat, and the unread/last-message counts. CREATE INDEX IF NOT
    # EXISTS works on both PostgreSQL and SQLite.
    indexes = {
        "ix_chat_members_user_id": "chat_members (user_id)",
        "ix_chat_members_chat_id": "chat_members (chat_id)",
        "ix_messages_chat_id_id": "messages (chat_id, id)",
        "ix_users_group_id": "users (group_id)",
        "ix_reactions_message_id": "reactions (message_id)",
    }
    for name, target in indexes.items():
        table = target.split(" ", 1)[0]
        if table not in existing_tables:
            continue
        try:
            connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON {target}"))
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not create index %s: %s", name, e)
