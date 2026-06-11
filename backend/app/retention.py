"""Message retention policy (compliance).

A background task runs daily. If `retention_days` (admin setting) is > 0,
messages older than that many days are permanently deleted. Optionally the
attachment files on disk are removed too.

retention_days = 0  -> keep everything (disabled, default).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from . import app_settings
from .config import settings as env_settings
from .database import async_session_maker
from .models import AuditLog, Message

logger = logging.getLogger("corporate-chat")

_CHECK_INTERVAL = 6 * 60 * 60  # re-evaluate every 6 hours


async def _purge_once() -> int:
    """Delete messages older than the configured retention window.
    Returns the number of messages removed."""
    async with async_session_maker() as db:
        days = int(await app_settings.get(db, "retention_days") or 0)
        if days <= 0:
            return 0
        purge_files = bool(await app_settings.get(db, "retention_purge_attachments"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # collect attachment paths before deleting (best-effort file cleanup)
        old = (await db.execute(
            select(Message).where(Message.created_at < cutoff)
        )).scalars().all()
        if not old:
            return 0

        if purge_files:
            upload_dir = env_settings.UPLOAD_DIR
            for m in old:
                for rel in (m.attachment_url, m.attachment_thumb):
                    if rel and rel.startswith("/uploads/"):
                        path = os.path.join(upload_dir, rel[len("/uploads/"):])
                        try:
                            if os.path.isfile(path):
                                os.remove(path)
                        except OSError:
                            pass

        count = len(old)
        await db.execute(
            delete(Message).where(Message.created_at < cutoff)
            .execution_options(synchronize_session=False)
        )
        db.add(AuditLog(
            actor_id=None, actor_name="system", action="retention_purge",
            details=f"deleted {count} messages older than {days} days",
        ))
        await db.commit()
        logger.info("Retention: purged %d messages older than %d days", count, days)
        return count


async def retention_loop() -> None:
    """Background loop; never raises out (so it can't crash the app)."""
    # small initial delay so startup finishes first
    await asyncio.sleep(60)
    while True:
        try:
            await _purge_once()
        except Exception as e:  # noqa: BLE001
            logger.warning("Retention purge failed: %s", e)
        await asyncio.sleep(_CHECK_INTERVAL)
