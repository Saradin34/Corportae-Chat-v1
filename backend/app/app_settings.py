"""Runtime-editable server settings, backed by the app_settings KV table.

Values fall back to the static `config.settings` (env vars) when not yet
overridden in the database. Cached in-memory and refreshed on write.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings as env_settings
from .models import AppSetting

# key -> (python type, default-from-env-callable)
_SPEC = {
    "max_upload_mb": (int, lambda: env_settings.MAX_UPLOAD_MB),
    "max_avatar_mb": (int, lambda: env_settings.MAX_AVATAR_MB),
    "password_min_length": (int, lambda: 6),
    "allow_local_auth": (bool, lambda: env_settings.ALLOW_LOCAL_AUTH),
    "ldap_enabled": (bool, lambda: env_settings.LDAP_ENABLED),
    "app_title": (str, lambda: env_settings.APP_NAME),
    "brand_color": (str, lambda: "#3390ec"),
    # ---- Retention policy (compliance) ----
    # 0 = keep forever. Otherwise messages older than N days are purged daily.
    "retention_days": (int, lambda: 0),
    # purge attachments belonging to purged messages too
    "retention_purge_attachments": (bool, lambda: True),
}

_cache: dict[str, object] = {}


def _coerce(typ, raw: str):
    if typ is bool:
        return str(raw).lower() in ("1", "true", "yes", "on")
    if typ is int:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
    return raw


async def load_all(db: AsyncSession) -> dict[str, object]:
    """Return the full settings map (db override or env default)."""
    rows = (await db.execute(select(AppSetting))).scalars().all()
    db_map = {r.key: r.value for r in rows}
    out: dict[str, object] = {}
    for key, (typ, default_fn) in _SPEC.items():
        if key in db_map:
            out[key] = _coerce(typ, db_map[key])
        else:
            out[key] = default_fn()
    _cache.clear()
    _cache.update(out)
    return out


async def get(db: AsyncSession, key: str):
    if key in _cache:
        return _cache[key]
    await load_all(db)
    return _cache.get(key)


async def set_many(db: AsyncSession, values: dict[str, object]) -> dict[str, object]:
    for key, val in values.items():
        if key not in _SPEC or val is None:
            continue
        existing = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
        sval = str(val)
        if existing:
            existing.value = sval
        else:
            db.add(AppSetting(key=key, value=sval))
    await db.commit()
    return await load_all(db)
