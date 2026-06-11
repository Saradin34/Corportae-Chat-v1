"""Message routes: list, send, edit, delete, react, pin, forward, search, export."""
import html as _html
import json as _json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Chat, ChatMember, Message, Reaction, User
from ..schemas import (
    CreateMessageRequest,
    ForwardMessageRequest,
    MessageOut,
    ReactionOut,
    ReactionRequest,
)
from ..permissions import get_effective_permissions
from ..security import get_current_user
from ..ws_manager import manager

router = APIRouter(prefix="/api/chats/{chat_id}/messages", tags=["messages"])


async def _members(db: AsyncSession, chat_id: int) -> list[int]:
    return list((await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id))).scalars().all())


async def _ensure_member(db: AsyncSession, chat_id: int, user_id: int) -> None:
    if user_id not in await _members(db, chat_id):
        raise HTTPException(status_code=403, detail="Нет доступа к этому чату")


def _to_out(msg: Message, sender: User, reactions: list[ReactionOut]) -> MessageOut:
    return MessageOut(
        id=msg.id,
        chat_id=msg.chat_id,
        sender_id=msg.sender_id,
        sender_username=sender.username,
        sender_name=sender.full_name or sender.username,
        sender_color=sender.avatar_color,
        sender_avatar=sender.avatar_url or "",
        text="" if msg.is_deleted else msg.text,
        reply_to=msg.reply_to,
        forwarded_from_name=msg.forwarded_from_name or "",
        attachment_kind="" if msg.is_deleted else (msg.attachment_kind or ""),
        attachment_url="" if msg.is_deleted else (msg.attachment_url or ""),
        attachment_thumb="" if msg.is_deleted else (msg.attachment_thumb or ""),
        attachment_name="" if msg.is_deleted else (msg.attachment_name or ""),
        attachment_size=0 if msg.is_deleted else (msg.attachment_size or 0),
        attachment_w=0 if msg.is_deleted else (msg.attachment_w or 0),
        attachment_h=0 if msg.is_deleted else (msg.attachment_h or 0),
        is_pinned=msg.is_pinned,
        is_edited=msg.is_edited,
        is_deleted=msg.is_deleted,
        is_system=msg.is_system,
        reactions=reactions,
        created_at=msg.created_at,
    )


async def _reactions_for(db: AsyncSession, message_ids: list[int], me_id: int) -> dict[int, list[ReactionOut]]:
    if not message_ids:
        return {}
    rows = (await db.execute(select(Reaction).where(Reaction.message_id.in_(message_ids)))).scalars().all()
    grouped: dict[int, dict[str, ReactionOut]] = {}
    for r in rows:
        bucket = grouped.setdefault(r.message_id, {})
        ro = bucket.get(r.emoji)
        if not ro:
            ro = ReactionOut(emoji=r.emoji, count=0, user_ids=[], reacted=False)
            bucket[r.emoji] = ro
        ro.count += 1
        ro.user_ids.append(r.user_id)
        if r.user_id == me_id:
            ro.reacted = True
    return {mid: list(b.values()) for mid, b in grouped.items()}


@router.get("", response_model=list[MessageOut])
async def list_messages(
    chat_id: int,
    before: int | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _ensure_member(db, chat_id, user.id)
    stmt = select(Message, User).join(User, User.id == Message.sender_id).where(Message.chat_id == chat_id)
    if before:
        stmt = stmt.where(Message.id < before)
    stmt = stmt.order_by(Message.id.desc()).limit(min(limit, 100))
    rows = (await db.execute(stmt)).all()
    rows.reverse()
    reacts = await _reactions_for(db, [m.id for m, _ in rows], user.id)
    return [_to_out(m, s, reacts.get(m.id, [])) for m, s in rows]


@router.get("/pinned", response_model=list[MessageOut])
async def list_pinned(chat_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await _ensure_member(db, chat_id, user.id)
    rows = (
        await db.execute(
            select(Message, User).join(User, User.id == Message.sender_id)
            .where(Message.chat_id == chat_id, Message.is_pinned == True, Message.is_deleted == False)  # noqa: E712
            .order_by(Message.id.desc())
        )
    ).all()
    reacts = await _reactions_for(db, [m.id for m, _ in rows], user.id)
    return [_to_out(m, s, reacts.get(m.id, [])) for m, s in rows]


@router.get("/search", response_model=list[MessageOut])
async def search_messages(
    chat_id: int, q: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    await _ensure_member(db, chat_id, user.id)
    if not q.strip():
        return []
    rows = (
        await db.execute(
            select(Message, User).join(User, User.id == Message.sender_id)
            .where(
                Message.chat_id == chat_id,
                Message.is_deleted == False,  # noqa: E712
                Message.text.ilike(f"%{q}%"),
            )
            .order_by(Message.id.desc()).limit(50)
        )
    ).all()
    reacts = await _reactions_for(db, [m.id for m, _ in rows], user.id)
    return [_to_out(m, s, reacts.get(m.id, [])) for m, s in rows]


@router.get("/export")
async def export_chat(
    chat_id: int,
    format: str = Query("txt", pattern="^(txt|html|json)$"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Export the full conversation as TXT, HTML (print-to-PDF), or JSON.

    Useful for compliance / archiving. Only chat members can export.
    """
    await _ensure_member(db, chat_id, user.id)
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")

    rows = (
        await db.execute(
            select(Message, User).join(User, User.id == Message.sender_id)
            .where(Message.chat_id == chat_id, Message.is_deleted == False)  # noqa: E712
            .order_by(Message.id.asc())
        )
    ).all()

    chat_title = chat.name or f"Чат #{chat.id}"
    if chat.type == "private":
        others = [s.full_name or s.username for m, s in rows if s.id != user.id]
        if others:
            chat_title = others[0]

    def _fmt_dt(dt: datetime | None) -> str:
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _line_text(m: Message) -> str:
        if m.is_system:
            return m.text or ""
        parts = []
        if m.text:
            parts.append(m.text)
        if m.attachment_kind == "image":
            parts.append(f"[Изображение: {m.attachment_name or 'фото'}]")
        elif m.attachment_kind == "file":
            parts.append(f"[Файл: {m.attachment_name or 'файл'}]")
        if m.forwarded_from_name:
            parts.insert(0, f"(переслано от {m.forwarded_from_name})")
        return " ".join(parts)

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fname_base = f"chat_{chat.id}_{stamp}"

    if format == "json":
        data = {
            "chat": {"id": chat.id, "type": chat.type, "title": chat_title},
            "exported_at": datetime.now().isoformat(),
            "messages": [
                {
                    "id": m.id,
                    "sender": s.full_name or s.username,
                    "sender_id": m.sender_id,
                    "text": m.text or "",
                    "is_system": m.is_system,
                    "attachment_kind": m.attachment_kind or "",
                    "attachment_name": m.attachment_name or "",
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m, s in rows
            ],
        }
        return JSONResponse(
            content=data,
            headers={"Content-Disposition": f'attachment; filename="{fname_base}.json"'},
        )

    if format == "html":
        body = []
        for m, s in rows:
            sender = _html.escape(s.full_name or s.username)
            when = _fmt_dt(m.created_at)
            text = _html.escape(_line_text(m)).replace("\n", "<br>")
            if m.is_system:
                body.append(f'<div class="sys">{text} <span class="t">{when}</span></div>')
            else:
                mine = " mine" if m.sender_id == user.id else ""
                body.append(
                    f'<div class="msg{mine}"><div class="hd"><b>{sender}</b> '
                    f'<span class="t">{when}</span></div><div class="tx">{text}</div></div>'
                )
        doc = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">
<title>{_html.escape(chat_title)}</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:#fff;color:#111;max-width:820px;margin:24px auto;padding:0 16px}}
  h1{{font-size:20px;border-bottom:2px solid #3390ec;padding-bottom:8px}}
  .meta{{color:#777;font-size:12px;margin-bottom:18px}}
  .msg{{margin:10px 0;padding:8px 12px;border-radius:10px;background:#f1f4f7}}
  .msg.mine{{background:#e7f3ff}}
  .hd{{font-size:13px;margin-bottom:2px}}
  .t{{color:#999;font-size:11px;margin-left:6px}}
  .tx{{font-size:14px;white-space:pre-wrap}}
  .sys{{text-align:center;color:#888;font-size:12px;margin:10px 0}}
  @media print{{body{{margin:0}}}}
</style></head><body>
<h1>{_html.escape(chat_title)}</h1>
<div class="meta">Экспортировано {_fmt_dt(datetime.now())} · сообщений: {len(rows)} ·
  для печати в PDF используйте Ctrl+P → «Сохранить как PDF»</div>
{''.join(body)}
</body></html>"""
        return HTMLResponse(
            content=doc,
            headers={"Content-Disposition": f'attachment; filename="{fname_base}.html"'},
        )

    # txt (default)
    lines = [f"Чат: {chat_title}", f"Экспортировано: {_fmt_dt(datetime.now())}",
             f"Сообщений: {len(rows)}", "=" * 50, ""]
    for m, s in rows:
        when = _fmt_dt(m.created_at)
        if m.is_system:
            lines.append(f"--- {_line_text(m)} ({when}) ---")
        else:
            sender = s.full_name or s.username
            lines.append(f"[{when}] {sender}:")
            lines.append(f"    {_line_text(m)}")
        lines.append("")
    return PlainTextResponse(
        content="\n".join(lines),
        headers={"Content-Disposition": f'attachment; filename="{fname_base}.txt"'},
    )


@router.post("", response_model=MessageOut)
async def send_message(
    chat_id: int,
    data: CreateMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _ensure_member(db, chat_id, user.id)

    # ---- channel posting rule: only chat-admins may post in a channel ----
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if chat and chat.type == "channel" and user.role != "admin":
        cm = (await db.execute(
            select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id)
        )).scalar_one_or_none()
        if not cm or not cm.is_admin:
            raise HTTPException(status_code=403, detail="В канал могут писать только администраторы канала")

    text = (data.text or "").strip()
    has_attachment = bool(data.attachment_kind and data.attachment_url)
    if not text and not has_attachment:
        raise HTTPException(status_code=400, detail="Пустое сообщение")
    if data.attachment_kind and data.attachment_kind not in ("image", "file"):
        raise HTTPException(status_code=400, detail="Недопустимый тип вложения")
    # attachment URLs must point at our own uploads (no SSRF/abuse)
    if has_attachment and not data.attachment_url.startswith("/uploads/"):
        raise HTTPException(status_code=400, detail="Недопустимый адрес вложения")

    # ---- group permission enforcement ----
    perms = await get_effective_permissions(db, user)
    if not perms["can_send_messages"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может отправлять сообщения")
    if has_attachment and data.attachment_kind == "image" and not perms["can_send_images"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может отправлять изображения")
    if has_attachment and data.attachment_kind == "file" and not perms["can_send_files"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может отправлять файлы")

    msg = Message(
        chat_id=chat_id,
        sender_id=user.id,
        text=text,
        reply_to=data.reply_to,
        attachment_kind=data.attachment_kind if has_attachment else "",
        attachment_url=data.attachment_url if has_attachment else "",
        attachment_thumb=data.attachment_thumb if has_attachment else "",
        attachment_name=data.attachment_name if has_attachment else "",
        attachment_size=data.attachment_size if has_attachment else 0,
        attachment_w=data.attachment_w if has_attachment else 0,
        attachment_h=data.attachment_h if has_attachment else 0,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    out = _to_out(msg, user, [])

    member_ids = await _members(db, chat_id)
    # ---- @mention detection ----
    mentioned_ids = await _resolve_mentions(db, text, member_ids, exclude_id=user.id)
    payload = {"type": "new_message", "message": out.model_dump(mode="json")}
    if mentioned_ids:
        payload["mentions"] = mentioned_ids
    await manager.send_to_users(member_ids, payload)
    return out


async def _resolve_mentions(db: AsyncSession, text: str, member_ids: list[int], exclude_id: int) -> list[int]:
    """Find @username tokens in the text and map them to member user IDs.

    Only members of the chat can be mentioned. The author is never notified
    about their own mention.
    """
    import re
    if not text or "@" not in text:
        return []
    names = {n.lower() for n in re.findall(r"@([A-Za-z0-9_.\-]+)", text)}
    if not names:
        return []
    rows = (
        await db.execute(
            select(User.id, User.username).where(
                User.id.in_(member_ids),
                func.lower(User.username).in_(names),
            )
        )
    ).all()
    return [uid for uid, _ in rows if uid != exclude_id]


@router.post("/forward", response_model=MessageOut)
async def forward_message(
    chat_id: int,
    data: ForwardMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # chat_id is the source; data.to_chat_id is the destination
    await _ensure_member(db, chat_id, user.id)
    await _ensure_member(db, data.to_chat_id, user.id)
    if not (await get_effective_permissions(db, user))["can_forward"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может пересылать сообщения")
    src = (await db.execute(select(Message, User).join(User, User.id == Message.sender_id)
                            .where(Message.id == data.message_id, Message.chat_id == chat_id))).first()
    if not src:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    smsg, ssender = src
    fwd = Message(
        chat_id=data.to_chat_id,
        sender_id=user.id,
        text=smsg.text,
        forwarded_from_name=ssender.full_name or ssender.username,
        attachment_kind=smsg.attachment_kind or "",
        attachment_url=smsg.attachment_url or "",
        attachment_thumb=smsg.attachment_thumb or "",
        attachment_name=smsg.attachment_name or "",
        attachment_size=smsg.attachment_size or 0,
        attachment_w=smsg.attachment_w or 0,
        attachment_h=smsg.attachment_h or 0,
    )
    db.add(fwd)
    await db.commit()
    await db.refresh(fwd)
    out = _to_out(fwd, user, [])
    await manager.send_to_users(await _members(db, data.to_chat_id), {"type": "new_message", "message": out.model_dump(mode="json")})
    return out


@router.patch("/{message_id}", response_model=MessageOut)
async def edit_message(
    chat_id: int,
    message_id: int,
    data: CreateMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    msg = (await db.execute(select(Message).where(Message.id == message_id, Message.chat_id == chat_id))).scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    if msg.sender_id != user.id:
        raise HTTPException(status_code=403, detail="Можно редактировать только свои сообщения")
    if not (await get_effective_permissions(db, user))["can_edit_own"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может редактировать сообщения")
    msg.text = data.text
    msg.is_edited = True
    await db.commit()
    await db.refresh(msg)
    reacts = (await _reactions_for(db, [msg.id], user.id)).get(msg.id, [])
    out = _to_out(msg, user, reacts)
    await manager.send_to_users(await _members(db, chat_id), {"type": "edit_message", "message": out.model_dump(mode="json")})
    return out


@router.delete("/{message_id}")
async def delete_message(
    chat_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    msg = (await db.execute(select(Message).where(Message.id == message_id, Message.chat_id == chat_id))).scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    if msg.sender_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Нет прав на удаление")
    if msg.sender_id == user.id and user.role != "admin" and not (await get_effective_permissions(db, user))["can_delete_own"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может удалять сообщения")
    msg.is_deleted = True
    msg.is_pinned = False
    msg.text = ""
    await db.commit()
    await manager.send_to_users(await _members(db, chat_id), {"type": "delete_message", "chat_id": chat_id, "message_id": message_id})
    return {"ok": True}


@router.post("/{message_id}/pin")
async def toggle_pin(
    chat_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cm = (await db.execute(select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id))).scalar_one_or_none()
    if not cm:
        raise HTTPException(status_code=403, detail="Нет доступа")
    if not (await get_effective_permissions(db, user))["can_pin"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может закреплять сообщения")
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if chat and chat.type != "private" and not cm.is_admin and user.role != "admin":
        raise HTTPException(status_code=403, detail="Закреплять может только админ группы")
    msg = (await db.execute(select(Message).where(Message.id == message_id, Message.chat_id == chat_id))).scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    msg.is_pinned = not msg.is_pinned
    await db.commit()
    await manager.send_to_users(await _members(db, chat_id), {"type": "pin_changed", "chat_id": chat_id, "message_id": message_id, "is_pinned": msg.is_pinned})
    return {"ok": True, "is_pinned": msg.is_pinned}


@router.post("/{message_id}/react")
async def react(
    chat_id: int,
    message_id: int,
    data: ReactionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _ensure_member(db, chat_id, user.id)
    if not (await get_effective_permissions(db, user))["can_react"]:
        raise HTTPException(status_code=403, detail="Ваша группа не может ставить реакции")
    msg = (await db.execute(select(Message).where(Message.id == message_id, Message.chat_id == chat_id))).scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    existing = (
        await db.execute(
            select(Reaction).where(
                Reaction.message_id == message_id,
                Reaction.user_id == user.id,
                Reaction.emoji == data.emoji,
            )
        )
    ).scalar_one_or_none()
    if existing:
        await db.delete(existing)
    else:
        db.add(Reaction(message_id=message_id, user_id=user.id, emoji=data.emoji))
    await db.commit()

    reacts = (await _reactions_for(db, [message_id], user.id)).get(message_id, [])
    payload = {
        "type": "reaction_changed",
        "chat_id": chat_id,
        "message_id": message_id,
        "reactions": [r.model_dump() for r in reacts],
    }
    await manager.send_to_users(await _members(db, chat_id), payload)
    return {"ok": True, "reactions": [r.model_dump() for r in reacts]}
