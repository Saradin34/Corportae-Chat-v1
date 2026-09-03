"""Chat routes: list, create, update, members, mute, read."""
import asyncio
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
    UserLiteOut,
)
from ..permissions import get_effective_permissions
from ..security import get_current_user
from ..utils import random_color
from ..ws_manager import manager

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _member_out(u: User, cm_or_admin) -> ChatMemberOut:
    # Accept either ChatMember (preferred) or bool for backward compatibility.
    is_admin = bool(getattr(cm_or_admin, "is_admin", cm_or_admin))
    last_read = int(getattr(cm_or_admin, "last_read_message_id", 0) or 0)
    return ChatMemberOut(
        id=u.id, username=u.username, email=u.email, full_name=u.full_name,
        avatar_color=u.avatar_color, avatar_url=u.avatar_url or "", bio=u.bio,
        title=u.title or "", phone=u.phone or "", office=u.office or "", role=u.role,
        is_active=u.is_active, is_online=manager.is_online(u.id), status=manager.get_status(u.id),
        is_chat_admin=is_admin, last_read_message_id=last_read, last_seen=u.last_seen, created_at=u.created_at,
    )


def _last_text(last_msg: Message | None):
    if not last_msg:
        return None
    if last_msg.attachment_kind == "image":
        return "📷 " + (last_msg.text or "Фото")
    if last_msg.attachment_kind == "file":
        return "📎 " + (last_msg.attachment_name or last_msg.text or "Файл")
    return last_msg.text


def _last_read_state(last_msg: Message | None, current_user: User, member_rows) -> tuple[bool, bool]:
    """Return (is_mine, read_by_others) for the chat list Telegram-like ticks."""
    if not last_msg or last_msg.sender_id != current_user.id:
        return False, False
    others = [cm for u, cm in member_rows if u.id != current_user.id and getattr(u, "is_active", True)]
    if len(member_rows) > 12 or len(others) > 12:
        return True, False
    if not others:
        return True, False
    return True, all((cm.last_read_message_id or 0) >= last_msg.id for cm in others)


def _build_chat_out(chat: Chat, current_user: User, member_rows, last_msg, my_membership, unread: int) -> ChatOut:
    """Build a ChatOut from already-fetched data (no DB access). Used by the
    batched list_chats path for performance."""
    members = []
    display_name = chat.name
    display_color = chat.avatar_color
    display_avatar = chat.avatar_url or ""
    has_other_private_member = False
    member_count = 0
    online_count = 0
    for u, cm in member_rows:
        member_count += 1
        if u.is_active and manager.is_online(u.id):
            online_count += 1
        # List payload: private chats need the peer; groups/channels only need
        # the current user (admin flag). Full roster comes from GET /chats/:id.
        if chat.type == "private" or u.id == current_user.id:
            members.append(_member_out(u, cm))
        if chat.type == "private" and u.id != current_user.id:
            has_other_private_member = True
            display_name = u.full_name or u.username
            display_color = u.avatar_color
            display_avatar = u.avatar_url or ""
    if chat.type == "private" and not has_other_private_member and chat.created_by == current_user.id:
        display_name = "Избранное"
        display_color = "#8b5cf6"
        display_avatar = ""
    last_is_mine, last_read = _last_read_state(last_msg, current_user, member_rows)
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
        last_message_sender_id=(last_msg.sender_id if last_msg else None),
        last_message_is_mine=last_is_mine,
        last_message_read=last_read,
        unread=unread,
        is_muted=(my_membership.is_muted if my_membership else False),
        last_read_message_id=(my_membership.last_read_message_id if my_membership else 0),
        member_count=member_count,
        online_count=online_count,
        members=members,
    )


async def _serialize_chat(db: AsyncSession, chat: Chat, current_user: User) -> ChatOut:
    my_membership = (
        await db.execute(
            select(ChatMember).where(ChatMember.chat_id == chat.id, ChatMember.user_id == current_user.id)
        )
    ).scalar_one_or_none()
    member_count = (
        await db.execute(select(func.count()).select_from(ChatMember).where(ChatMember.chat_id == chat.id))
    ).scalar() or 0

    members = []
    display_name = chat.name
    display_color = chat.avatar_color
    display_avatar = chat.avatar_url or ""
    has_other_private_member = False
    rows = []
    online_count = 0
    if chat.type == "private":
        cms = (
            await db.execute(select(ChatMember).where(ChatMember.chat_id == chat.id))
        ).scalars().all()
        uid_list = [cm.user_id for cm in cms]
        users = (await db.execute(select(User).where(User.id.in_(uid_list)))).scalars().all() if uid_list else []
        by_id = {u.id: u for u in users}
        for cm in cms:
            u = by_id.get(cm.user_id)
            if not u:
                continue
            rows.append((u, cm))
            members.append(_member_out(u, cm))
            if manager.is_online(u.id):
                online_count += 1
            if u.id != current_user.id:
                has_other_private_member = True
                display_name = u.full_name or u.username
                display_color = u.avatar_color
                display_avatar = u.avatar_url or ""
    else:
        uids = (
            await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat.id))
        ).scalars().all()
        online_count = sum(1 for uid in uids if manager.is_online(uid))
        if my_membership:
            members.append(_member_out(current_user, my_membership))
            rows.append((current_user, my_membership))
    if chat.type == "private" and not has_other_private_member and chat.created_by == current_user.id:
        display_name = "Избранное"
        display_color = "#8b5cf6"
        display_avatar = ""

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

    last_is_mine, last_read = _last_read_state(last_msg, current_user, rows)

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
        last_message_sender_id=(last_msg.sender_id if last_msg else None),
        last_message_is_mine=last_is_mine,
        last_message_read=last_read,
        unread=unread,
        is_muted=(my_membership.is_muted if my_membership else False),
        last_read_message_id=(my_membership.last_read_message_id if my_membership else 0),
        member_count=member_count,
        online_count=online_count,
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


async def _get_or_create_saved_chat(db: AsyncSession, user: User) -> Chat:
    """Telegram-like Saved Messages: a private chat with only the current user.

    It is ensured during chat list load so it is always visible in the chat list,
    not hidden behind a menu.
    """
    # One query: private chat created by me that has exactly one member — me.
    member_counts = (
        select(ChatMember.chat_id, func.count().label("n"), func.min(ChatMember.user_id).label("uid"))
        .group_by(ChatMember.chat_id)
        .having(func.count() == 1)
        .subquery()
    )
    saved = (
        await db.execute(
            select(Chat)
            .join(member_counts, member_counts.c.chat_id == Chat.id)
            .where(
                Chat.type == "private",
                Chat.created_by == user.id,
                member_counts.c.uid == user.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if saved:
        changed = False
        if saved.name != "Избранное":
            saved.name = "Избранное"; changed = True
        if not saved.description:
            saved.description = "Сохранённые сообщения"; changed = True
        if changed:
            await db.commit(); await db.refresh(saved)
        return saved
    chat = Chat(type="private", name="Избранное", description="Сохранённые сообщения", avatar_color="#8b5cf6", created_by=user.id)
    db.add(chat)
    await db.flush()
    db.add(ChatMember(chat_id=chat.id, user_id=user.id, is_admin=True))
    await db.commit()
    await db.refresh(chat)
    return chat


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
    # Ensure Telegram-like Saved Messages exists and is visible directly in the
    # chat list. This is cheap and prevents hiding it behind burger/menu.
    await _get_or_create_saved_chat(db, user)

    # the user's memberships (one query) — gives chat_ids + last_read + mute
    my_memberships = (
        await db.execute(select(ChatMember).where(ChatMember.user_id == user.id))
    ).scalars().all()
    if not my_memberships:
        return []
    chat_ids = [m.chat_id for m in my_memberships]
    my_cm_by_chat = {m.chat_id: m for m in my_memberships}

    chats = (await db.execute(select(Chat).where(Chat.id.in_(chat_ids)))).scalars().all()

    # Dialog list must NOT load the 120-member roster of every group.
    # Counts are aggregated; full User rows only for private-chat peers.
    count_rows = (
        await db.execute(
            select(ChatMember.chat_id, func.count())
            .where(ChatMember.chat_id.in_(chat_ids))
            .group_by(ChatMember.chat_id)
        )
    ).all()
    member_count_by = {cid: int(n or 0) for cid, n in count_rows}

    private_ids = [c.id for c in chats if c.type == "private"]
    private_cms = []
    if private_ids:
        private_cms = (
            await db.execute(select(ChatMember).where(ChatMember.chat_id.in_(private_ids)))
        ).scalars().all()
    cms_by_private: dict[int, list] = {cid: [] for cid in private_ids}
    need_user_ids = {user.id}
    for cm in private_cms:
        cms_by_private.setdefault(cm.chat_id, []).append(cm)
        need_user_ids.add(cm.user_id)
    users_by_id = {user.id: user}
    extra = (await db.execute(select(User).where(User.id.in_(list(need_user_ids))))).scalars().all()
    for u in extra:
        users_by_id[u.id] = u

    members_by_chat: dict[int, list] = {cid: [] for cid in chat_ids}
    counts_by_chat: dict[int, tuple] = {}
    group_ids = [c.id for c in chats if c.type != "private"]
    group_uids: dict[int, list[int]] = {cid: [] for cid in group_ids}
    if group_ids:
        for cid, uid in (
            await db.execute(
                select(ChatMember.chat_id, ChatMember.user_id).where(ChatMember.chat_id.in_(group_ids))
            )
        ).all():
            group_uids.setdefault(cid, []).append(uid)
    for c in chats:
        counts_by_chat[c.id] = (member_count_by.get(c.id, 0), 0)
        if c.type == "private":
            members_by_chat[c.id] = [
                (users_by_id[cm.user_id], cm)
                for cm in cms_by_private.get(c.id, [])
                if cm.user_id in users_by_id
            ]
            counts_by_chat[c.id] = (
                len(members_by_chat[c.id]),
                sum(1 for u, _cm in members_by_chat[c.id] if manager.is_online(u.id)),
            )
        else:
            my_cm = my_cm_by_chat.get(c.id)
            members_by_chat[c.id] = [(user, my_cm)] if my_cm else []
            uids = group_uids.get(c.id, [])
            counts_by_chat[c.id] = (
                member_count_by.get(c.id, len(uids)),
                sum(1 for uid in uids if manager.is_online(uid)),
            )

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

    result = []
    for c in chats:
        mc, oc = counts_by_chat.get(c.id, (0, 0))
        out = _build_chat_out(
            c, user, members_by_chat.get(c.id, []),
            last_msgs_by_chat.get(c.id), my_cm_by_chat.get(c.id),
            unread_by_chat.get(c.id, 0),
        )
        out.member_count = mc
        out.online_count = oc
        result.append(out)
    result.sort(key=lambda c: (c.name == "Избранное", c.last_message_at is not None, c.last_message_at or 0), reverse=True)
    return result


@router.post("/saved", response_model=ChatOut)
async def saved_chat(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    chat = await _get_or_create_saved_chat(db, user)
    return await _serialize_chat(db, chat, user)


@router.post("", response_model=ChatOut)
async def create_chat(
    data: CreateChatRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if data.type not in ("private", "group", "channel"):
        raise HTTPException(status_code=400, detail="Недопустимый тип чата")

    if getattr(data, "add_all", False):
        all_ids = (await db.execute(select(User.id).where(User.is_active == True))).scalars().all()  # noqa: E712
        member_ids = set(all_ids) | {user.id}
    else:
        member_ids = set(data.member_ids) | {user.id}

    # Announcement channels are special: every global application admin must be
    # able to publish there, not only the user who created the channel.  Add all
    # active global admins as channel members/admins at creation time.
    admin_ids: set[int] = set()
    if data.type == "channel":
        admin_ids = set((
            await db.execute(select(User.id).where(User.role == "admin", User.is_active == True))  # noqa: E712
        ).scalars().all())
        member_ids |= admin_ids

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
                out = await _serialize_chat(db, chat, user)
                out.is_new = False
                return out

    chat = Chat(
        type=data.type,
        name=data.name,
        description=data.description,
        avatar_color=random_color(),
        created_by=user.id,
    )
    db.add(chat)
    await db.flush()
    db.add_all([
        ChatMember(
            chat_id=chat.id,
            user_id=mid,
            is_admin=(mid == user.id) or (data.type == "channel" and mid in admin_ids),
        )
        for mid in member_ids
    ])
    await db.commit()
    await db.refresh(chat)

    out = await _serialize_chat(db, chat, user)
    out.is_new = True
    manager.remember_members(chat.id, list(member_ids))
    others = [m for m in member_ids if m != user.id]
    if others:
        manager.spawn(manager.send_to_users(others, {"type": "chat_created", "chat_id": chat.id, "chat": out.model_dump(mode="json")}))
    return out


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
    if getattr(data, "add_all", False):
        cond = [User.is_active == True]  # noqa: E712
        if existing:
            cond.append(User.id.notin_(existing))
        rows = (await db.execute(select(User.id, User.role, User.full_name, User.username).where(*cond))).all()
        db.add_all([
            ChatMember(
                chat_id=chat_id,
                user_id=r.id,
                is_admin=(chat.type == "channel" and r.role == "admin"),
            )
            for r in rows
        ])
        added_names = [r.full_name or r.username for r in rows]
        if rows:
            await db.commit()
            manager.forget_members(chat_id)
    else:
        new_ids = [mid for mid in dict.fromkeys(data.member_ids) if mid not in existing]
        if new_ids:
            users = (await db.execute(select(User).where(User.id.in_(new_ids)))).scalars().all()
            by_id = {u.id: u for u in users}
            batch = []
            for mid in new_ids:
                u = by_id.get(mid)
                if not u:
                    continue
                batch.append(ChatMember(
                    chat_id=chat_id,
                    user_id=mid,
                    is_admin=(chat.type == "channel" and u.role == "admin"),
                ))
                added_names.append(u.full_name or u.username)
            if batch:
                db.add_all(batch)
                await db.commit()
                manager.forget_members(chat_id)

    if added_names:
        actor = user.full_name or user.username
        if len(added_names) > 8:
            text = f"{actor} добавил {len(added_names)} участников"
        else:
            text = f"{actor} добавил: {', '.join(added_names)}"
        await _system_message(db, chat_id, text, user)
    members = await _members_ids(db, chat_id)
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one()
    out = await _serialize_chat(db, chat, user)
    manager.spawn(manager.send_to_users(members, {"type": "chat_updated", "chat_id": chat_id, "chat": out.model_dump(mode="json")}))
    return out



@router.get("/{chat_id}/members", response_model=list[UserLiteOut])
async def list_chat_members(
    chat_id: int,
    offset: int = 0,
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Paginated lite roster for group info (does not block chat open)."""
    await _ensure_member(db, chat_id, user.id)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    rows = (
        await db.execute(
            select(User, ChatMember)
            .join(ChatMember, ChatMember.user_id == User.id)
            .where(ChatMember.chat_id == chat_id)
            .order_by(ChatMember.is_admin.desc(), User.full_name, User.username)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return [
        UserLiteOut(
            id=u.id,
            username=u.username,
            full_name=u.full_name or "",
            avatar_color=u.avatar_color or "#3390ec",
            is_online=manager.is_online(u.id),
            is_chat_admin=bool(cm.is_admin),
        )
        for u, cm in rows
    ]


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
    manager.forget_members(chat_id)

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
    # Private chats only: ticks for the other person. Large groups must NOT
    # fan this out to 120+ members — 10 people opening the group would freeze everyone.
    chat = (await db.execute(select(Chat.type).where(Chat.id == chat_id))).scalar_one_or_none()
    if chat == "private":
        others = [m for m in (await _members_ids(db, chat_id)) if m != user.id]
        manager.spawn(manager.send_to_users(others, {
            "type": "read_receipt",
            "chat_id": chat_id,
            "user_id": user.id,
            "last_read_message_id": last,
        }))
    return {"ok": True, "last_read_message_id": last}


@router.post("/{chat_id}/discard-empty")
async def discard_empty_private_chat(chat_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    """Delete a newly-created private chat if it still has no messages.

    This supports Telegram-like behaviour: selecting a person may create/open a
    private chat draft, but if the user leaves/closes without sending anything,
    the empty chat disappears for both sides.
    """
    cm = await _ensure_member(db, chat_id, user.id)
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat or chat.type != "private":
        return {"ok": True, "deleted": False}
    # Only the creator (or admin) may discard the empty draft chat.
    if chat.created_by != user.id and user.role != "admin":
        return {"ok": True, "deleted": False}
    msg_count = (await db.execute(select(func.count()).select_from(Message).where(Message.chat_id == chat_id))).scalar() or 0
    if msg_count > 0:
        return {"ok": True, "deleted": False}
    members = await _members_ids(db, chat_id)
    await db.delete(chat)
    await db.commit()
    await manager.send_to_users(members, {"type": "chat_deleted", "chat_id": chat_id})
    return {"ok": True, "deleted": True}


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
