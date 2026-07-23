"""Admin routes: stats, user management, audit log, broadcast."""
import os
import socket
import subprocess
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import AuditLog, CallEvent, Chat, ChatMember, DownloadEvent, Group, Message, User
from ..schemas import AdminStats, AuditLogOut, BroadcastRequest, TokenResponse, UserOut
from ..security import create_access_token, get_current_admin, hash_password
from ..utils import random_color
from ..ws_manager import manager

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def _log(db: AsyncSession, actor: User, action: str, details: str = "") -> None:
    db.add(AuditLog(actor_id=actor.id, actor_name=actor.username, action=action, details=details))
    await db.commit()


# ---------- AD / SSO diagnostics ----------
def _public_settings() -> dict:
    keys = [
        "LDAP_ENABLED", "LDAP_SERVERS", "LDAP_USE_SSL", "LDAP_START_TLS", "LDAP_BASE_DN",
        "LDAP_DOMAIN", "LDAP_NETBIOS", "LDAP_LOGIN_ATTR", "LDAP_BIND_DN", "LDAP_ADMIN_GROUP",
        "LDAP_REQUIRED_GROUP", "LDAP_TIMEOUT", "SSO_ENABLED", "SSO_ALLOW_PROXY",
        "SSO_ALLOW_NEGOTIATE", "SSO_SERVICE_NAME", "SSO_KEYTAB_PATH", "KRB5_CONFIG",
    ]
    out = {}
    for k in keys:
        if k == "KRB5_CONFIG":
            out[k] = os.environ.get("KRB5_CONFIG", "/etc/krb5.conf")
        else:
            out[k] = getattr(settings, k, "")
    out["LDAP_BIND_PASSWORD"] = "***" if settings.LDAP_BIND_PASSWORD else ""
    return out


def _spn_from_settings() -> str:
    raw = (settings.SSO_SERVICE_NAME or "").strip()
    if raw:
        no_realm = raw.split("@", 1)[0]
        return no_realm
    return "HTTP/<host>"


def _run_cmd(cmd: list[str], timeout: int = 6) -> dict:
    try:
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return {"ok": p.returncode == 0, "returncode": p.returncode, "stdout": p.stdout[-5000:], "stderr": p.stderr[-5000:]}
    except FileNotFoundError:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": f"Команда не найдена: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -2, "stdout": "", "stderr": "Timeout"}


@router.get("/diagnostics/config")
async def diagnostics_config(_: User = Depends(get_current_admin)):
    kt = settings.SSO_KEYTAB_PATH or os.environ.get("KRB5_KTNAME", "")
    if kt.startswith("FILE:"):
        kt_path = kt[5:]
    else:
        kt_path = kt
    return {
        "settings": _public_settings(),
        "spn": _spn_from_settings(),
        "keytab": {
            "path": kt_path,
            "exists": bool(kt_path and os.path.exists(kt_path)),
            "readable": bool(kt_path and os.path.exists(kt_path) and os.access(kt_path, os.R_OK)),
            "size": os.path.getsize(kt_path) if kt_path and os.path.exists(kt_path) else 0,
        },
        "hints": [
            "Для Kerberos открывайте сайт по FQDN, на который выписан SPN, например http://chat.kupava.by.",
            "Если браузер отправляет NTLM — добавьте сайт в Local Intranet / AuthServerAllowlist и проверьте klist get HTTP/host.",
            "В keytab должен быть principal HTTP/host@REALM с актуальным KVNO и AES enctype.",
        ],
    }


@router.get("/diagnostics/keytab")
async def diagnostics_keytab(_: User = Depends(get_current_admin)):
    kt = settings.SSO_KEYTAB_PATH or "/etc/krb5.keytab"
    return _run_cmd(["klist", "-k", "-e", kt])


@router.post("/diagnostics/ldap-bind")
async def diagnostics_ldap_bind(_: User = Depends(get_current_admin)):
    from .. import ldap_auth
    try:
        conn = ldap_auth._open_search_connection()
        try:
            return {"ok": True, "server": str(getattr(conn, "server", "")), "bound": bool(conn.bound), "result": conn.result}
        finally:
            try: conn.unbind()
            except Exception: pass
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@router.get("/diagnostics/ldap-user")
async def diagnostics_ldap_user(q: str, _: User = Depends(get_current_admin)):
    from .. import ldap_auth
    if not q.strip():
        raise HTTPException(status_code=400, detail="Введите логин/ФИО")
    try:
        conn = ldap_auth._open_search_connection()
        try:
            qq = q.strip().replace("*", "")
            flt = f"(&(objectCategory=person)(objectClass=user)(|(sAMAccountName=*{qq}*)(displayName=*{qq}*)(mail=*{qq}*)))"
            attrs = ["sAMAccountName", "displayName", "mail", "title", "memberOf"]
            conn.search(settings.LDAP_BASE_DN, flt, attributes=attrs, size_limit=10)
            return {"ok": True, "count": len(conn.entries), "entries": [e.entry_attributes_as_dict | {"dn": str(e.entry_dn)} for e in conn.entries]}
        finally:
            try: conn.unbind()
            except Exception: pass
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@router.get("/diagnostics/ldap-group")
async def diagnostics_ldap_group(q: str, _: User = Depends(get_current_admin)):
    from .. import ldap_auth
    try:
        groups = ldap_auth.search_groups(q, limit=20)
        return {"ok": True, "count": len(groups), "groups": [{"dn": g.dn, "name": g.name, "description": g.description, "member_count": g.member_count} for g in groups]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "type": type(e).__name__}


@router.get("/diagnostics/spn")
async def diagnostics_spn(_: User = Depends(get_current_admin)):
    from .. import ldap_auth
    spn = _spn_from_settings()
    try:
        conn = ldap_auth._open_search_connection()
        try:
            flt = f"(servicePrincipalName={spn})"
            attrs = ["sAMAccountName", "distinguishedName", "servicePrincipalName", "msDS-SupportedEncryptionTypes"]
            conn.search(settings.LDAP_BASE_DN, flt, attributes=attrs, size_limit=20)
            return {"ok": True, "spn": spn, "count": len(conn.entries), "entries": [e.entry_attributes_as_dict | {"dn": str(e.entry_dn)} for e in conn.entries]}
        finally:
            try: conn.unbind()
            except Exception: pass
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "spn": spn, "error": str(e), "type": type(e).__name__}


@router.delete("/cleanup")
async def cleanup_history(
    target: str,
    period: str = "all",
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Delete selected history records for the requested recent period.

    target: audit | calls | downloads | all
    period: day | 7d | month | year | all
    """
    models = {
        "audit": AuditLog,
        "calls": CallEvent,
        "downloads": DownloadEvent,
    }
    if target not in (*models.keys(), "all"):
        raise HTTPException(status_code=400, detail="Недопустимый раздел очистки")
    now = datetime.now(timezone.utc)
    cutoffs = {
        "day": now - timedelta(days=1),
        "7d": now - timedelta(days=7),
        "month": now - timedelta(days=30),
        "year": now - timedelta(days=365),
        "all": None,
    }
    if period not in cutoffs:
        raise HTTPException(status_code=400, detail="Недопустимый период")
    selected = list(models.values()) if target == "all" else [models[target]]
    deleted: dict[str, int] = {}
    for model in selected:
        stmt = delete(model)
        cutoff = cutoffs[period]
        if cutoff is not None:
            stmt = stmt.where(model.created_at >= cutoff)
        res = await db.execute(stmt)
        deleted[model.__tablename__] = res.rowcount or 0
    db.add(AuditLog(
        actor_id=admin.id,
        actor_name=admin.username,
        action="cleanup_history",
        details=f"target={target} period={period} deleted={deleted}",
    ))
    await db.commit()
    return {"ok": True, "target": target, "period": period, "deleted": deleted}


@router.get("/system/health")
async def system_health(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    """Operational health dashboard: DB, Redis, LDAP, AMI, uploads."""
    result = {
        "db": {"ok": False},
        "redis": {"ok": False},
        "ldap": {"ok": False, "enabled": settings.LDAP_ENABLED},
        "sso": {"ok": False, "enabled": settings.SSO_ENABLED},
        "ami": {"ok": False, "enabled": settings.AMI_ENABLED},
        "uploads": {"ok": False},
        "app": {"version": settings.APP_VERSION, "debug": settings.DEBUG},
    }

    # DB
    try:
        cnt = (await db.execute(select(func.count()).select_from(User))).scalar() or 0
        result["db"] = {"ok": True, "users": cnt}
    except Exception as e:  # noqa: BLE001
        result["db"] = {"ok": False, "error": str(e)}

    # Redis
    try:
        import redis.asyncio as redis
        r = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        pong = await r.ping()
        await r.aclose()
        result["redis"] = {"ok": bool(pong), "url": settings.REDIS_URL.split("@")[-1]}
    except Exception as e:  # noqa: BLE001
        result["redis"] = {"ok": False, "error": str(e)}

    # LDAP service bind
    if settings.LDAP_ENABLED:
        try:
            from .. import ldap_auth
            conn = ldap_auth._open_search_connection()
            try:
                result["ldap"] = {"ok": True, "enabled": True, "server": str(getattr(conn, "server", "")), "base_dn": settings.LDAP_BASE_DN}
            finally:
                try: conn.unbind()
                except Exception: pass
        except Exception as e:  # noqa: BLE001
            result["ldap"] = {"ok": False, "enabled": True, "error": str(e)}
    else:
        result["ldap"] = {"ok": True, "enabled": False, "note": "disabled"}

    # SSO keytab/SPN basic checks
    if settings.SSO_ENABLED:
        kt = settings.SSO_KEYTAB_PATH or os.environ.get("KRB5_KTNAME", "")
        kt_path = kt[5:] if kt.startswith("FILE:") else kt
        result["sso"] = {
            "ok": bool(kt_path and os.path.exists(kt_path) and os.access(kt_path, os.R_OK)),
            "enabled": True,
            "service": settings.SSO_SERVICE_NAME,
            "keytab_exists": bool(kt_path and os.path.exists(kt_path)),
            "keytab_readable": bool(kt_path and os.path.exists(kt_path) and os.access(kt_path, os.R_OK)),
            "keytab_size": os.path.getsize(kt_path) if kt_path and os.path.exists(kt_path) else 0,
        }
    else:
        result["sso"] = {"ok": True, "enabled": False, "note": "disabled"}

    # AMI TCP check (doesn't log in here to avoid disrupting main listener)
    if settings.AMI_ENABLED:
        try:
            with socket.create_connection((settings.AMI_HOST, settings.AMI_PORT), timeout=2) as sock:
                sock.settimeout(2)
                banner = sock.recv(200).decode("utf-8", errors="replace")[:120]
            result["ami"] = {"ok": True, "enabled": True, "host": settings.AMI_HOST, "port": settings.AMI_PORT, "banner": banner.strip()}
        except Exception as e:  # noqa: BLE001
            result["ami"] = {"ok": False, "enabled": True, "host": settings.AMI_HOST, "port": settings.AMI_PORT, "error": str(e)}
    else:
        result["ami"] = {"ok": True, "enabled": False, "note": "disabled"}

    # Uploads disk usage
    try:
        root = settings.UPLOAD_DIR
        total_size = 0
        files = 0
        for base, _, names in os.walk(root):
            for name in names:
                try:
                    files += 1
                    total_size += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass
        result["uploads"] = {"ok": os.path.isdir(root), "path": root, "files": files, "bytes": total_size}
    except Exception as e:  # noqa: BLE001
        result["uploads"] = {"ok": False, "error": str(e)}

    result["ok"] = all(v.get("ok", False) for k, v in result.items() if isinstance(v, dict) and k != "app")
    return result


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


@router.get("/analytics")
async def analytics(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    """Small admin analytics payload for built-in charts (no external libs)."""
    now = datetime.now(timezone.utc)
    days = 14
    start = now - timedelta(days=days - 1)
    labels = [(start + timedelta(days=i)).date().isoformat() for i in range(days)]
    msg_by_day = {d: 0 for d in labels}
    user_by_day = {d: 0 for d in labels}

    msg_rows = (await db.execute(select(Message.created_at).where(Message.created_at >= start))).scalars().all()
    for dt in msg_rows:
        if dt:
            key = dt.date().isoformat()
            if key in msg_by_day:
                msg_by_day[key] += 1

    user_rows = (await db.execute(select(User.created_at).where(User.created_at >= start))).scalars().all()
    for dt in user_rows:
        if dt:
            key = dt.date().isoformat()
            if key in user_by_day:
                user_by_day[key] += 1

    type_rows = (await db.execute(select(Chat.type, func.count()).group_by(Chat.type))).all()
    chat_types = {typ or "unknown": cnt or 0 for typ, cnt in type_rows}
    ad_linked_groups = (await db.execute(select(func.count()).select_from(Group).where(Group.ad_group_dn != ""))).scalar() or 0
    users_without_group = (await db.execute(select(func.count()).select_from(User).where(User.group_id == None))).scalar() or 0  # noqa: E711

    user_msg_count = func.count(Message.id).label("cnt")
    top_users_rows = (
        await db.execute(
            select(User.id, User.username, User.full_name, user_msg_count)
            .join(Message, Message.sender_id == User.id)
            .where(Message.created_at >= start)
            .group_by(User.id, User.username, User.full_name)
            .order_by(user_msg_count.desc())
            .limit(8)
        )
    ).all()
    chat_msg_count = func.count(Message.id).label("cnt")
    top_chats_rows = (
        await db.execute(
            select(Chat.id, Chat.name, Chat.type, chat_msg_count)
            .join(Message, Message.chat_id == Chat.id)
            .where(Message.created_at >= start)
            .group_by(Chat.id, Chat.name, Chat.type)
            .order_by(chat_msg_count.desc())
            .limit(8)
        )
    ).all()

    return {
        "days": labels,
        "messages_by_day": [msg_by_day[d] for d in labels],
        "users_by_day": [user_by_day[d] for d in labels],
        "chat_types": chat_types,
        "ad_linked_groups": ad_linked_groups,
        "users_without_group": users_without_group,
        "top_users": [
            {"id": uid, "name": full_name or username, "username": username, "messages": cnt or 0}
            for uid, username, full_name, cnt in top_users_rows
        ],
        "top_chats": [
            {"id": cid, "name": name or f"#{cid}", "type": typ, "messages": cnt or 0}
            for cid, name, typ, cnt in top_chats_rows
        ],
    }


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


@router.post("/users/{user_id}/impersonate", response_model=TokenResponse)
async def impersonate_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Issue a normal JWT for another account.

    This is an explicit admin support tool ("войти как пользователь") and every
    use is written to the audit log. The frontend keeps the original admin token
    locally so the admin can return back without re-login.
    """
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if not target.is_active:
        raise HTTPException(status_code=400, detail="Нельзя войти под заблокированным пользователем")
    token = create_access_token(target.id)
    await _log(db, admin, "impersonate", f"as={target.username} role={target.role}")
    return TokenResponse(access_token=token, user=UserOut.model_validate(target))


@router.post("/users/{user_id}/force-logout")
async def force_logout_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Нельзя завершить свою текущую сессию из админки")
    await manager.send_to_user(target.id, {
        "type": "force_logout",
        "reason": "Сессия завершена администратором",
    })
    await _log(db, admin, "force_logout", f"user={target.username}")
    return {"ok": True}


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
    # If a user becomes a global administrator, make them an admin member of
    # every announcement channel so "all administrators can publish" remains
    # true for channels that already existed.
    if role == "admin":
        channels = (await db.execute(select(Chat).where(Chat.type == "channel"))).scalars().all()
        for ch in channels:
            cm = (await db.execute(
                select(ChatMember).where(ChatMember.chat_id == ch.id, ChatMember.user_id == u.id)
            )).scalar_one_or_none()
            if cm:
                cm.is_admin = True
            else:
                db.add(ChatMember(chat_id=ch.id, user_id=u.id, is_admin=True))
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
