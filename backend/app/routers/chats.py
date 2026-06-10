"""Chat routes: list, create, update, members, mute, read."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Chat, ChatMember, Message, User
from ..schemas import (
    AddMembersRequest,
    ChatMemberOut,
    ChatOut,
    CreateChatRequest,
    UpdateChatRequest,
)
from ..permissions import get_effective_permissions
from ..security import get_current_user
from ..utils import random_color
from ..ws_manager import manager

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _member_out(u: User, is_chat_admin: bool) -> ChatMemberOut:
    return ChatMemberOut(
        id=u.id, username=u.username, email=u.email, full_name=u.full_name,
        avatar_color=u.avatar_color, avatar_url=u.avatar_url or "", bio=u.bio, role=u.role,
        is_active=u.is_active, is_online=manager.is_online(u.id),
        is_chat_admin=is_chat_admin, last_seen=u.last_seen, created_at=u.created_at,
    )


def _last_text(last_msg: Message | None):
    if not last_msg:
        return None
    if last_msg.attachment_kind == "image":
        return "📷 " + (last_msg.text or "Фото")
    if last_msg.attachment_kind == "file":
        return "📎 " + (last_msg.attachment_name or last_msg.text or "Файл")
    return last_msg.text


def _build_chat_out(chat: Chat, current_user: User, member_rows, last_msg, my_membership, unread: int) -> ChatOut:
    """Build a ChatOut from already-fetched data (no DB access). Used by the
    batched list_chats path for performance."""
    members = []
    display_name = chat.name
    display_color = chat.avatar_color
    display_avatar = chat.avatar_url or ""
    for u, cm in member_rows:
        members.append(_member_out(u, cm.is_admin))
        if chat.type == "private" and u.id != current_user.id:
            display_name = u.full_name or u.username
            display_color = u.avatar_color
            display_avatar = u.avatar_url or ""
    return ChatOut(
        id=chat.id,
        type=chat.type,
        name=display_name or "Без названия",
        description=chat.description or "",
        avatar_color=display_color,
        avatar_url=display_avatar,
        created_by=chat.created_by,
        last_message=_last_text(last_msg),
        last_message_at=(last_msg.created_at if last_msg else None),
        unread=unread,
        is_muted=(my_membership.is_muted if my_membership else False),
        members=members,
    )


async def _serialize_chat(db: AsyncSession, chat: Chat, current_user: User) -> ChatOut:
    rows = (
        await db.execute(
            select(User, ChatMember)
            .join(ChatMember, ChatMember.user_id == User.id)
            .where(ChatMember.chat_id == chat.id)
        )
    ).all()

    members = []
    display_name = chat.name
    display_color = chat.avatar_color
    display_avatar = chat.avatar_url or ""
    my_membership = None
    for u, cm in rows:
        members.append(_member_out(u, cm.is_admin))
        if cm.user_id == current_user.id:
            my_membership = cm
        if chat.type == "private" and u.id != current_user.id:
            display_name = u.full_name or u.username
            display_color = u.avatar_color
            display_avatar = u.avatar_url or ""

    last_msg = (
        await db.execute(
            select(Message)
            .where(Message.chat_id == chat.id, Message.is_deleted == False)  # noqa: E712
            .order_by(desc(Message.id))
            .limit(1)
        )
    ).scalar_one_or_none()

    # unread count
    unread = 0
    if my_membership:
        unread = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.chat_id == chat.id,
                    Message.id > (my_membership.last_read_message_id or 0),
                    Message.sender_id != current_user.id,
                    Message.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar() or 0

    last_text = None
    if last_msg:
        if last_msg.attachment_kind == "image":
            last_text = "📷 " + (last_msg.text or "Фото")
        elif last_msg.attachment_kind == "file":
            last_text = "📎 " + (last_msg.attachment_name or last_msg.text or "Файл")
        else:
            last_text = last_msg.text

    return ChatOut(
        id=chat.id,
        type=chat.type,
        name=display_name or "Без названия",
        description=chat.description or "",
        avatar_color=display_color,
        avatar_url=display_avatar,
        created_by=chat.created_by,
        last_message=last_text,
        last_message_at=(last_msg.created_at if last_msg else None),
        unread=unread,
        is_muted=(my_membership.is_muted if my_membership else False),
        members=members,
    )


async def _ensure_member(db: AsyncSession, chat_id: int, user_id: int) -> ChatMember:
    cm = (
        await db.execute(
            select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id)
        )
    ).scalar_one_or_none()
    if cm is None:
        raise HTTPException(status_code=403, detail="Нет доступа к этому чату")
    return cm


async def _members_ids(db: AsyncSession, chat_id: int) -> list[int]:
    return list((await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id))).scalars().all())


async def _system_message(db: AsyncSession, chat_id: int, text: str, actor: User) -> None:
    msg = Message(chat_id=chat_id, sender_id=actor.id, text=text, is_system=True)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    from .messages import _to_out  # local import to avoid cycle
    out = _to_out(msg, actor, [])
    members = await _members_ids(db, chat_id)
    await manager.send_to_users(members, {"type": "new_message", "message": out.model_dump(mode="json")})


@router.get("", response_model=list[ChatOut])
async def list_chats(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """List the current user's chats.

    Performance: instead of running 3 queries per chat (members, last message,
    unread) we batch all members, all last-messages and all unread counts into
    one query each — O(1) round-trips regardless of how many chats the user has.
    """
    # the user's memberships (one query) — gives chat_ids + last_read + mute
    my_memberships = (
        await db.execute(select(ChatMember).where(ChatMember.user_id == user.id))
    ).scalars().all()
    if not my_memberships:
        return []
    chat_ids = [m.chat_id for m in my_memberships]
    my_cm_by_chat = {m.chat_id: m for m in my_memberships}

    chats = (await db.execute(select(Chat).where(Chat.id.in_(chat_ids)))).scalars().all()

    # all members of all these chats (one query)
    member_rows = (
        await db.execute(
            select(User, ChatMember)
            .join(ChatMember, ChatMember.user_id == User.id)
            .where(ChatMember.chat_id.in_(chat_ids))
        )
    ).all()
    members_by_chat: dict[int, list] = {cid: [] for cid in chat_ids}
    for u, cm in member_rows:
        members_by_chat.setdefault(cm.chat_id, []).append((u, cm))

    # last (non-deleted) message per chat — one query using a window function
    # would be ideal, but a simple grouped-max + fetch keeps it portable.
    last_ids_rows = (
        await db.execute(
            select(Message.chat_id, func.max(Message.id))
            .where(Message.chat_id.in_(chat_ids), Message.is_deleted == False)  # noqa: E712
            .group_by(Message.chat_id)
        )
    ).all()
    last_msg_ids = [mid for _, mid in last_ids_rows if mid is not None]
    last_msgs_by_chat: dict[int, Message] = {}
    if last_msg_ids:
        last_msgs = (await db.execute(select(Message).where(Message.id.in_(last_msg_ids)))).scalars().all()
        last_msgs_by_chat = {m.chat_id: m for m in last_msgs}

    # unread per chat in ONE query: count messages newer than this chat's
    # last_read, not authored by me. We OR together a per-chat condition
    # (chat_id == cid AND id > last_read[cid]) so it's a single round-trip.
    from sqlalchemy import and_, or_
    unread_by_chat: dict[int, int] = {cid: 0 for cid in chat_ids}
    per_chat_conditions = [
        and_(Message.chat_id == cid, Message.id > (my_cm_by_chat[cid].last_read_message_id or 0))
        for cid in chat_ids
    ]
    if per_chat_conditions:
        unread_rows = (
            await db.execute(
                select(Message.chat_id, func.count())
                .where(
                    or_(*per_chat_conditions),
                    Message.sender_id != user.id,
                    Message.is_deleted == False,  # noqa: E712
                )
                .group_by(Message.chat_id)
            )
        ).all()
        for cid, cnt in unread_rows:
            unread_by_chat[cid] = cnt or 0

    result = [
        _build_chat_out(c, user, members_by_chat.get(c.id, []),
                        last_msgs_by_chat.get(c.id), my_cm_by_chat.get(c.id),
                        unread_by_chat.get(c.id, 0))
        for c in chats
    ]
    result.sort(key=lambda c: (c.last_message_at is not None, c.last_message_at or 0), reverse=True)
    return result


@router.post("", response_model=ChatOut)
async def create_chat(
    data: CreateChatRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    member_ids = set(data.member_ids) | {user.id}

    # ---- group permission enforcement ----
    perms = await get_effective_permissions(db, user)
    if data.type == "private" and not perms["can_create_private"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может создавать личные чаты")
    if data.type != "private" and not perms["can_create_groups"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может создавать группы")

    if data.type == "private":
        others = [mid for mid in member_ids if mid != user.id]
        if len(others) != 1:
            raise HTTPException(status_code=400, detail="Личный чат требует ровно одного собеседника")
        other_id = others[0]
        existing = (
            await db.execute(select(Chat.id).where(Chat.type == "private"))
        ).scalars().all()
        for cid in existing:
            mids = set((await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == cid))).scalars().all())
            if mids == {user.id, other_id}:
                chat = (await db.execute(select(Chat).where(Chat.id == cid))).scalar_one()
                return await _serialize_chat(db, chat, user)

    chat = Chat(
        type=data.type,
        name=data.name,
        description=data.description,
        avatar_color=random_color(),
        created_by=user.id,
    )
    db.add(chat)
    await db.flush()
    for mid in member_ids:
        db.add(ChatMember(chat_id=chat.id, user_id=mid, is_admin=(mid == user.id)))
    await db.commit()
    await db.refresh(chat)

    await manager.send_to_users([m for m in member_ids if m != user.id], {"type": "chat_created", "chat_id": chat.id})
    return await _serialize_chat(db, chat, user)


@router.get("/{chat_id}", response_model=ChatOut)
async def get_chat(chat_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _ensure_member(db, chat_id, user.id)
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return await _serialize_chat(db, chat, user)


@router.patch("/{chat_id}", response_model=ChatOut)
async def update_chat(
    chat_id: int,
    data: UpdateChatRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cm = await _ensure_member(db, chat_id, user.id)
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    if chat.type == "private":
        raise HTTPException(status_code=400, detail="Личный чат нельзя редактировать")
    if not cm.is_admin and user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор группы может менять её")

    changed = []
    if data.name is not None and data.name != chat.name:
        chat.name = data.name
        changed.append("название")
    if data.description is not None:
        chat.description = data.description
    if data.avatar_color is not None:
        chat.avatar_color = data.avatar_color
    await db.commit()
    await db.refresh(chat)

    if changed:
        await _system_message(db, chat_id, f"{user.full_name or user.username} изменил {', '.join(changed)}", user)

    serialized = await _serialize_chat(db, chat, user)
    members = await _members_ids(db, chat_id)
    await manager.send_to_users(members, {"type": "chat_updated", "chat_id": chat_id})
    return serialized


@router.post("/{chat_id}/members", response_model=ChatOut)
async def add_members(
    chat_id: int,
    data: AddMembersRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cm = await _ensure_member(db, chat_id, user.id)
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat or chat.type == "private":
        raise HTTPException(status_code=400, detail="Нельзя добавлять участников в этот чат")
    if not cm.is_admin and user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор группы может добавлять участников")

    existing = set(await _members_ids(db, chat_id))
    added_names = []
    for mid in data.member_ids:
        if mid in existing:
            continue
        u = (await db.execute(select(User).where(User.id == mid))).scalar_one_or_none()
        if u:
            db.add(ChatMember(chat_id=chat_id, user_id=mid))
            added_names.append(u.full_name or u.username)
    await db.commit()

    if added_names:
        await _system_message(db, chat_id, f"{user.full_name or user.username} добавил: {', '.join(added_names)}", user)
    members = await _members_ids(db, chat_id)
    await manager.send_to_users(members, {"type": "chat_updated", "chat_id": chat_id})
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one()
    return await _serialize_chat(db, chat, user)


@router.delete("/{chat_id}/members/{member_id}")
async def remove_member(
    chat_id: int,
    member_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cm = await _ensure_member(db, chat_id, user.id)
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat or chat.type == "private":
        raise HTTPException(status_code=400, detail="Недопустимо для этого чата")
    # leaving yourself is allowed; removing others needs admin
    if member_id != user.id and not cm.is_admin and user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор группы может удалять участников")

    target = (
        await db.execute(select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == member_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Участник не найден")
    tuser = (await db.execute(select(User).where(User.id == member_id))).scalar_one_or_none()
    await db.delete(target)
    await db.commit()

    label = (tuser.full_name or tuser.username) if tuser else "участник"
    if member_id == user.id:
        await _system_message(db, chat_id, f"{label} покинул(а) группу", user)
    else:
        await _system_message(db, chat_id, f"{user.full_name or user.username} удалил(а) {label}", user)
    members = await _members_ids(db, chat_id)
    await manager.send_to_users(members + [member_id], {"type": "chat_updated", "chat_id": chat_id})
    return {"ok": True}


@router.post("/{chat_id}/members/{member_id}/admin")
async def toggle_member_admin(
    chat_id: int,
    member_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cm = await _ensure_member(db, chat_id, user.id)
    if not cm.is_admin and user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор группы может назначать админов")
    target = (
        await db.execute(select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == member_id))
    ).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Участник не найден")
    target.is_admin = not target.is_admin
    await db.commit()
    members = await _members_ids(db, chat_id)
    await manager.send_to_users(members, {"type": "chat_updated", "chat_id": chat_id})
    return {"ok": True, "is_admin": target.is_admin}


@router.post("/{chat_id}/mute")
async def toggle_mute(chat_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cm = await _ensure_member(db, chat_id, user.id)
    cm.is_muted = not cm.is_muted
    await db.commit()
    return {"ok": True, "is_muted": cm.is_muted}


@router.post("/{chat_id}/read")
async def mark_read(chat_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cm = await _ensure_member(db, chat_id, user.id)
    last = (
        await db.execute(select(func.max(Message.id)).where(Message.chat_id == chat_id))
    ).scalar() or 0
    cm.last_read_message_id = last
    await db.commit()
    return {"ok": True, "last_read_message_id": last}


@router.delete("/{chat_id}")
async def delete_chat(chat_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cm = await _ensure_member(db, chat_id, user.id)
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    if chat.type != "private" and not cm.is_admin and user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор группы может удалить чат")
    members = await _members_ids(db, chat_id)
    await db.delete(chat)
    await db.commit()
    await manager.send_to_users(members, {"type": "chat_deleted", "chat_id": chat_id})
    return {"ok": True}
