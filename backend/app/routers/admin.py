"""Admin routes: stats, user management, audit log, broadcast."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import AuditLog, Chat, ChatMember, Group, Message, User
from ..schemas import (
    AdminStats,
    AuditLogOut,
    BroadcastRequest,
    BulkUserAction,
    ChannelSubsRequest,
    CreateChannelRequest,
    TimePoint,
    UserOut,
)
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
    sso_users = (await db.execute(select(func.count()).select_from(User).where(User.auth_source == "sso"))).scalar() or 0
    channels = (await db.execute(select(func.count()).select_from(Chat).where(Chat.type == "channel"))).scalar() or 0

    # ---- time-series for charts: messages & new users per day (last 7 days) ----
    msgs_per_day: list[TimePoint] = []
    users_per_day: list[TimePoint] = []
    for i in range(6, -1, -1):
        start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        label = start.strftime("%d.%m")
        mc = (await db.execute(
            select(func.count()).select_from(Message)
            .where(Message.created_at >= start, Message.created_at < end)
        )).scalar() or 0
        uc = (await db.execute(
            select(func.count()).select_from(User)
            .where(User.created_at >= start, User.created_at < end)
        )).scalar() or 0
        msgs_per_day.append(TimePoint(label=label, value=mc))
        users_per_day.append(TimePoint(label=label, value=uc))

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
        sso_users=sso_users,
        channels=channels,
        messages_per_day=msgs_per_day,
        users_per_day=users_per_day,
    )


def _filtered_users_stmt(q: str = "", role: str = "", source: str = "", state: str = ""):
    """Build a User query with admin filters applied."""
    stmt = select(User)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(User.username.ilike(like) | User.email.ilike(like) | User.full_name.ilike(like))
    if role in ("user", "admin"):
        stmt = stmt.where(User.role == role)
    if source in ("local", "ldap", "sso"):
        stmt = stmt.where(User.auth_source == source)
    if state == "active":
        stmt = stmt.where(User.is_active == True)  # noqa: E712
    elif state == "banned":
        stmt = stmt.where(User.is_active == False)  # noqa: E712
    return stmt


@router.get("/users", response_model=list[UserOut])
async def all_users(
    q: str = "",
    role: str = "",
    source: str = "",
    state: str = "",
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    stmt = _filtered_users_stmt(q, role, source, state)
    rows = (await db.execute(stmt.order_by(User.id))).scalars().all()
    result = []
    for u in rows:
        out = UserOut.model_validate(u)
        out.is_online = manager.is_online(u.id)
        result.append(out)
    return result


@router.get("/users/export")
async def export_users(
    q: str = "", role: str = "", source: str = "", state: str = "",
    db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin),
):
    """Export the (filtered) user list as CSV."""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    stmt = _filtered_users_stmt(q, role, source, state)
    rows = (await db.execute(stmt.order_by(User.id))).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "username", "full_name", "email", "role", "auth_source",
                "is_active", "title", "office", "phone", "created_at"])
    for u in rows:
        w.writerow([u.id, u.username, u.full_name, u.email, u.role, u.auth_source,
                    "active" if u.is_active else "banned", u.title or "", u.office or "",
                    u.phone or "", u.created_at.isoformat() if u.created_at else ""])
    await _log(db, admin, "export_users", f"count={len(rows)}")
    buf.seek(0)
    from datetime import datetime as _dt
    fname = f"users_{_dt.now():%Y%m%d_%H%M}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


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


@router.post("/users/bulk")
async def bulk_user_action(data: BulkUserAction, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Apply an action to many users at once (ban/unban/delete/role/group)."""
    ids = [uid for uid in set(data.user_ids) if uid != admin.id]  # never act on self
    if not ids:
        raise HTTPException(status_code=400, detail="Не выбрано ни одного пользователя (себя выбрать нельзя)")
    users = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    affected = 0
    for u in users:
        if data.action == "ban":
            if u.is_active:
                u.is_active = False
                await manager.send_to_user(u.id, {"type": "force_logout", "reason": "Аккаунт заблокирован администратором"})
                affected += 1
        elif data.action == "unban":
            if not u.is_active:
                u.is_active = True
                affected += 1
        elif data.action == "make_admin":
            if u.role != "admin":
                u.role = "admin"
                affected += 1
        elif data.action == "remove_admin":
            if u.role != "user":
                u.role = "user"
                affected += 1
        elif data.action == "assign_group":
            u.group_id = data.group_id
            affected += 1
        elif data.action == "delete":
            await db.delete(u)
            affected += 1
    await db.commit()
    await _log(db, admin, f"bulk_{data.action}", f"count={affected} ids={ids[:50]}")
    return {"ok": True, "affected": affected}


@router.post("/users/{user_id}/impersonate")
async def impersonate(user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Issue a token to sign in AS another user (for troubleshooting).

    The action is recorded in the audit log. The target must be active.
    """
    from ..security import create_access_token
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if not u.is_active:
        raise HTTPException(status_code=400, detail="Нельзя войти под заблокированным пользователем")
    if u.id == admin.id:
        raise HTTPException(status_code=400, detail="Вы уже вошли под собой")
    token = create_access_token(u.id)
    await _log(db, admin, "impersonate", f"as={u.username} (id={u.id})")
    return {"access_token": token, "username": u.username, "full_name": u.full_name or u.username}


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


# ---------- Channels management ----------
@router.get("/channels")
async def list_channels(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    chans = (await db.execute(select(Chat).where(Chat.type == "channel").order_by(Chat.id.desc()))).scalars().all()
    result = []
    for c in chans:
        subs = (await db.execute(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == c.id))).scalar() or 0
        authors = (await db.execute(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == c.id, ChatMember.is_admin == True))).scalar() or 0  # noqa: E712
        msgs = (await db.execute(select(func.count()).select_from(Message).where(Message.chat_id == c.id))).scalar() or 0
        result.append({
            "id": c.id, "name": c.name or f"#{c.id}", "description": c.description or "",
            "subscribers": subs, "authors": authors, "messages": msgs,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return result


@router.post("/channels")
async def create_channel(data: CreateChannelRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Create an announcement channel. `add_all` adds every active user as a
    subscriber; otherwise `member_ids` are added. The creator is an author."""
    chat = Chat(type="channel", name=data.name, description=data.description,
                avatar_color=random_color(), created_by=admin.id)
    db.add(chat)
    await db.flush()
    # subscribers
    sub_ids: set[int] = set(data.member_ids or [])
    if data.add_all:
        all_ids = (await db.execute(select(User.id).where(User.is_active == True))).scalars().all()  # noqa: E712
        sub_ids.update(all_ids)
    sub_ids.add(admin.id)
    for uid in sub_ids:
        db.add(ChatMember(chat_id=chat.id, user_id=uid, is_admin=(uid == admin.id)))
    await db.commit()
    await db.refresh(chat)
    await _log(db, admin, "create_channel", f"name={chat.name} subs={len(sub_ids)}")
    await manager.send_to_users([u for u in sub_ids if u != admin.id], {"type": "chat_created", "chat_id": chat.id})
    return {"ok": True, "id": chat.id, "subscribers": len(sub_ids)}


@router.post("/channels/{chat_id}/subscribers")
async def add_channel_subscribers(chat_id: int, data: ChannelSubsRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id, Chat.type == "channel"))).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail="Канал не найден")
    existing = set((await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id))).scalars().all())
    ids: set[int] = set(data.member_ids or [])
    if data.add_all:
        ids.update((await db.execute(select(User.id).where(User.is_active == True))).scalars().all())  # noqa: E712
    added = 0
    for uid in ids:
        if uid not in existing:
            db.add(ChatMember(chat_id=chat_id, user_id=uid, is_admin=False))
            added += 1
    await db.commit()
    await _log(db, admin, "channel_add_subs", f"chat={chat_id} added={added}")
    if added:
        await manager.send_to_users(list(ids - existing), {"type": "chat_created", "chat_id": chat_id})
    return {"ok": True, "added": added}


@router.post("/channels/{chat_id}/authors/{user_id}")
async def toggle_channel_author(chat_id: int, user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Grant/revoke posting rights (author) for a channel subscriber."""
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id, Chat.type == "channel"))).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail="Канал не найден")
    cm = (await db.execute(select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id))).scalar_one_or_none()
    if not cm:
        raise HTTPException(status_code=404, detail="Пользователь не подписан на канал")
    cm.is_admin = not cm.is_admin
    await db.commit()
    await _log(db, admin, "channel_author", f"chat={chat_id} user={user_id} author={cm.is_admin}")
    await manager.send_to_users([user_id], {"type": "chat_updated", "chat_id": chat_id})
    return {"ok": True, "is_author": cm.is_admin}


@router.get("/audit", response_model=list[AuditLogOut])
async def audit_log(
    limit: int = 100,
    q: str = "",
    action: str = "",
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(AuditLog.actor_name.ilike(like) | AuditLog.details.ilike(like))
    rows = (await db.execute(stmt.order_by(AuditLog.id.desc()).limit(min(limit, 500)))).scalars().all()
    return [AuditLogOut.model_validate(r) for r in rows]


@router.get("/audit/actions")
async def audit_actions(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    """Distinct action names — used to populate the audit filter dropdown."""
    rows = (await db.execute(select(AuditLog.action).distinct())).scalars().all()
    return sorted([a for a in rows if a])


@router.get("/audit/export")
async def export_audit(
    q: str = "", action: str = "",
    db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin),
):
    """Export the (filtered) audit log as CSV."""
    import csv
    import io
    from datetime import datetime as _dt
    from fastapi.responses import StreamingResponse

    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(AuditLog.actor_name.ilike(like) | AuditLog.details.ilike(like))
    rows = (await db.execute(stmt.order_by(AuditLog.id.desc()).limit(5000))).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "actor", "action", "details"])
    for r in rows:
        w.writerow([r.id, r.created_at.isoformat() if r.created_at else "",
                    r.actor_name, r.action, r.details])
    buf.seek(0)
    fname = f"audit_{_dt.now():%Y%m%d_%H%M}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/retention/run")
async def run_retention(db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Manually trigger a retention purge now (uses the saved retention_days)."""
    from ..retention import _purge_once
    count = await _purge_once()
    await _log(db, admin, "retention_run", f"deleted={count}")
    return {"ok": True, "deleted": count}


@router.post("/broadcast")
async def broadcast(data: BroadcastRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    """Send a system announcement to every online user as a toast/notification."""
    online = manager.online_user_ids()
    await manager.send_to_users(online, {"type": "broadcast", "text": data.text, "from": admin.username})
    await _log(db, admin, "broadcast", data.text[:100])
    return {"ok": True, "delivered": len(online)}
