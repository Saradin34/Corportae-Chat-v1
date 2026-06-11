"""Group-based permission resolution & enforcement.

Every user belongs to at most one organisational Group (department). The group
carries a set of boolean permission flags (see models.GROUP_PERMISSIONS). Users
without a group fall back to the implicit "default" group ("Пользователи без
группы"). Admins always have every permission.

Use `require_permission("can_send_files")` as a FastAPI dependency, or
`user_can(db, user, "can_pin")` for ad-hoc checks.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import GROUP_PERMISSIONS, Group, User
from .security import get_current_user

# Human-readable names for nicer error messages.
PERMISSION_LABELS = {
    "can_send_messages": "отправлять сообщения",
    "can_create_private": "создавать личные чаты",
    "can_create_groups": "создавать группы",
    "can_send_files": "отправлять файлы",
    "can_send_images": "отправлять изображения",
    "can_forward": "пересылать сообщения",
    "can_pin": "закреплять сообщения",
    "can_edit_own": "редактировать сообщения",
    "can_delete_own": "удалять сообщения",
    "can_react": "ставить реакции",
}


async def get_effective_permissions(db: AsyncSession, user: User) -> dict[str, bool]:
    """Return the resolved permission map for a user (admins -> all True)."""
    if user.role == "admin":
        return {p: True for p in GROUP_PERMISSIONS}

    group: Group | None = None
    if user.group_id:
        group = (await db.execute(select(Group).where(Group.id == user.group_id))).scalar_one_or_none()
    if group is None:
        # fall back to the default group ("Пользователи без группы")
        group = (await db.execute(select(Group).where(Group.is_default == True))).scalar_one_or_none()  # noqa: E712

    if group is None:
        # No groups configured at all -> permissive defaults (avoid lock-out).
        return {p: True for p in GROUP_PERMISSIONS}
    return {p: bool(getattr(group, p, True)) for p in GROUP_PERMISSIONS}


async def user_can(db: AsyncSession, user: User, permission: str) -> bool:
    perms = await get_effective_permissions(db, user)
    return perms.get(permission, True)


def require_permission(permission: str):
    """FastAPI dependency factory: 403s if the current user lacks `permission`."""

    async def _dep(
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> User:
        if not await user_can(db, user, permission):
            label = PERMISSION_LABELS.get(permission, permission)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Ваша группа не имеет права: {label}",
            )
        return user

    return _dep
