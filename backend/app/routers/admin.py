"""Admin routes: stats, user management, audit log, broadcast."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuditLog, Chat, ChatMember, Group, Message, User
from ..schemas import AdminStats, AuditLogOut, BroadcastRequest, UserOut
from ..security import get_current_admin, hash_password
from ..utils import random_color
from ..ws_manager import manager

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def _log(db: AsyncSession, actor: User, action: str, details: str = "") -> None:
    db.add(AuditLog(actor_id=actor.id, actor_name=actor.username, action=action, details=details))
    await db.commit()


@router.get("/stats", response_model=AdminStats)
async def stats(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    now = datetime.now(timezone.utc)
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
    total_chats = (await db.execute(select(func.count()).select_from(Chat))).scalar() or 0
    private_chats = (await db.execute(select(func.count()).select_from(Chat).where(Chat.type == "private"))).scalar() or 0
    group_chats = (await db.execute(select(func.count()).select_from(Chat).where(Chat.type != "private"))).scalar() or 0
    total_messages = (await db.execute(select(func.count()).select_from(Message))).scalar() or 0
    messages_today = (await db.execute(select(func.count()).select_from(Message).where(Message.created_at >= day_ago))).scalar() or 0
    new_users_week = (await db.execute(select(func.count()).select_from(User).where(User.created_at >= week_ago))).scalar() or 0
    admins = (await db.execute(select(func.count()).select_from(User).where(User.role == "admin"))).scalar() or 0
    banned = (await db.execute(select(func.count()).select_from(User).where(User.is_active == False))).scalar() or 0  # noqa: E712
    groups_count = (await db.execute(select(func.count()).select_from(Group))).scalar() or 0
    ldap_users = (await db.execute(select(func.count()).select_from(User).where(User.auth_source == "ldap"))).scalar() or 0

    return AdminStats(
        total_users=total_users,
        online_users=len(manager.online_user_ids()),
        total_chats=total_chats,
        private_chats=private_chats,
        group_chats=group_chats,
        total_messages=total_messages,
        messages_today=messages_today,
        new_users_week=new_users_week,
        admins=admins,
        banned_users=banned,
        groups=groups_count,
        ldap_users=ldap_users,
    )


@router.get("/users", response_model=list[UserOut])
async def all_users(q: str = "", db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    stmt = select(User)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(User.username.ilike(like) | User.email.ilike(like) | User.full_name.ilike(like))
    rows = (await db.execute(stmt.order_by(User.id))).scalars().all()
    result = []
    for u in rows:
        out = UserOut.model_validate(u)
        out.is_online = manager.is_online(u.id)
        result.append(out)
    return result


@router.post("/users/{user_id}/toggle-active", response_model=UserOut)
async def toggle_active(user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя заблокировать самого себя")
    u.is_active = not u.is_active
    await db.commit()
    await db.refresh(u)
    await _log(db, admin, "ban" if not u.is_active else "unban", f"user={u.username}")
    if not u.is_active:
        await manager.send_to_user(u.id, {"type": "force_logout", "reason": "Аккаунт заблокирован администратором"})
    return UserOut.model_validate(u)


@router.post("/users/{user_id}/role", response_model=UserOut)
async def set_role(user_id: int, role: str, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Недопустимая роль")
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя менять свою роль")
    u.role = role
    await db.commit()
    await db.refresh(u)
    await _log(db, admin, "set_role", f"user={u.username} role={role}")
    return UserOut.model_validate(u)


@router.post("/users/{user_id}/reset-password")
async def reset_password(user_id: int, new_password: str, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Пароль слишком короткий (мин. 6)")
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if u.auth_source == "ldap":
        raise HTTPException(status_code=400, detail="Пароль AD-пользователя управляется в Active Directory")
    u.password_hash = hash_password(new_password)
    await db.commit()
    await _log(db, admin, "reset_password", f"user={u.username}")
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя удалить самого себя")
    uname = u.username
    await db.delete(u)
    await db.commit()
    await _log(db, admin, "delete_user", f"user={uname}")
    return {"ok": True}


@router.get("/chats")
async def all_chats(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    chats = (await db.execute(select(Chat).order_by(Chat.id.desc()))).scalars().all()
    result = []
    for c in chats:
        member_count = (await db.execute(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == c.id))).scalar() or 0
        msg_count = (await db.execute(select(func.count()).select_from(Message).where(Message.chat_id == c.id))).scalar() or 0
        result.append({
            "id": c.id, "type": c.type, "name": c.name or f"#{c.id}",
            "members": member_count, "messages": msg_count,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return result


@router.delete("/chats/{chat_id}")
async def admin_delete_chat(chat_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    c = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Чат не найден")
    members = list((await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id))).scalars().all())
    await db.delete(c)
    await db.commit()
    await _log(db, admin, "delete_chat", f"chat={chat_id}")
    await manager.send_to_users(members, {"type": "chat_deleted", "chat_id": chat_id})
    return {"ok": True}


@router.get("/audit", response_model=list[AuditLogOut])
async def audit_log(limit: int = 100, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 300)))).scalars().all()
    return [AuditLogOut.model_validate(r) for r in rows]


@router.post("/broadcast")
async def broadcast(data: BroadcastRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Send a system announcement to every online user as a toast/notification."""
    online = manager.online_user_ids()
    await manager.send_to_users(online, {"type": "broadcast", "text": data.text, "from": admin.username})
    await _log(db, admin, "broadcast", data.text[:100])
    return {"ok": True, "delivered": len(online)}
