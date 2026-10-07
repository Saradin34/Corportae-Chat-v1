"""Server settings: read & update runtime-editable configuration (admin only).

These map to the app_settings KV store and override the static env defaults
without requiring a container restart.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .. import app_settings
from ..database import get_db
from ..models import AuditLog, User
from ..schemas import ServerSettingsOut, ServerSettingsUpdate
from ..security import get_current_admin, get_current_user

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


@router.get("", response_model=ServerSettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    data = await app_settings.load_all(db)
    return ServerSettingsOut(**data)


@router.patch("", response_model=ServerSettingsOut)
async def update_settings(
    data: ServerSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    patch = {k: v for k, v in data.model_dump().items() if v is not None}
    merged = await app_settings.set_many(db, patch)
    db.add(AuditLog(
        actor_id=admin.id, actor_name=admin.username,
        action="update_settings", details=", ".join(f"{k}={v}" for k, v in patch.items())[:300],
    ))
    await db.commit()
    return ServerSettingsOut(**merged)


public_router = APIRouter(prefix="/api/config", tags=["settings"])


@public_router.get("")
async def client_config(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Public (logged-in) config used by web and desktop clients."""
    data = await app_settings.load_all(db)
    sec = int(data.get("notify_duration_sec") or 5)
    if sec < 1:
        sec = 1
    if sec > 30:
        sec = 30
    return {"notify_duration_sec": sec}
