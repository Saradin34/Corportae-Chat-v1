"""WebSocket endpoint for real-time messaging & presence."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..database import async_session_maker
from ..models import Chat, ChatMember, User
from ..security import get_user_from_token
from ..ws_manager import manager

router = APIRouter()


async def _presence_recipients(user_id: int) -> list[int]:
    """Presence is company-wide for directory/org-structure/admin screens.

    Earlier it was sent only to users sharing a chat. For a corporate directory
    users expect online/away/offline state to update in real time everywhere.
    """
    return [uid for uid in manager.online_user_ids() if uid != user_id]


async def _user_chat_member_ids(user_id: int) -> list[int]:
    """All user IDs that share a chat with the given user (for presence broadcast)."""
    async with async_session_maker() as db:
        chat_ids = (
            await db.execute(select(ChatMember.chat_id).where(ChatMember.user_id == user_id))
        ).scalars().all()
        if not chat_ids:
            return []
        peers = (
            await db.execute(
                select(ChatMember.user_id).where(ChatMember.chat_id.in_(chat_ids))
            )
        ).scalars().all()
        return list({p for p in peers if p != user_id})


async def _call_target(chat_id: int, from_user_id: int, requested_to: int | None = None) -> int | None:
    """Validate a WebRTC call target and return the other user id.

    Calls are currently supported for private 1:1 chats. Signalling is relayed
    through this websocket; media goes peer-to-peer via WebRTC in the browser.
    """
    async with async_session_maker() as db:
        chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
        if not chat or chat.type != "private":
            return None
        members = (await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id))).scalars().all()
        if from_user_id not in members:
            return None
        others = [m for m in members if m != from_user_id]
        if not others:
            return None
        target = requested_to or others[0]
        return target if target in members and target != from_user_id else None


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = ""):
    async with async_session_maker() as db:
        user = await get_user_from_token(token, db)
        if user is None or not user.is_active:
            await ws.close(code=4001)
            return
        user_id = user.id

    await manager.connect(user_id, ws)

    # update presence
    async with async_session_maker() as db:
        u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if u:
            u.is_online = True
            await db.commit()

    manager.set_status(user_id, "online")
    peers = await _presence_recipients(user_id)
    await manager.send_to_users(peers, {"type": "presence", "user_id": user_id, "online": True, "status": "online"})

    try:
        while True:
            data = await ws.receive_json()
            mtype = data.get("type")
            if mtype == "ping":
                await ws.send_json({"type": "pong"})
            elif mtype == "status":
                # client reports "online" / "away" (idle)
                new_status = data.get("status", "online")
                manager.set_status(user_id, new_status)
                peers = await _presence_recipients(user_id)
                await manager.send_to_users(
                    peers,
                    {"type": "presence", "user_id": user_id, "online": True, "status": manager.get_status(user_id)},
                )
            elif mtype == "typing":
                chat_id = data.get("chat_id")
                if chat_id:
                    async with async_session_maker() as db:
                        members = (
                            await db.execute(
                                select(ChatMember.user_id).where(ChatMember.chat_id == chat_id)
                            )
                        ).scalars().all()
                    await manager.send_to_users(
                        [m for m in members if m != user_id],
                        {"type": "typing", "chat_id": chat_id, "user_id": user_id, "username": user.username},
                    )
            elif mtype in ("call_invite", "call_accept", "call_reject", "call_end", "call_signal"):
                chat_id = data.get("chat_id")
                try:
                    chat_id = int(chat_id)
                except (TypeError, ValueError):
                    chat_id = 0
                requested_to = data.get("to_user_id")
                try:
                    requested_to = int(requested_to) if requested_to is not None else None
                except (TypeError, ValueError):
                    requested_to = None
                target = await _call_target(chat_id, user_id, requested_to)
                if target:
                    payload = dict(data)
                    payload["type"] = mtype
                    payload["chat_id"] = chat_id
                    payload["from_user_id"] = user_id
                    payload["from_name"] = user.full_name or user.username
                    payload.pop("token", None)
                    await manager.send_to_user(target, payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await manager.disconnect(user_id, ws)
        if not manager.is_online(user_id):
            async with async_session_maker() as db:
                u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
                if u:
                    u.is_online = False
                    await db.commit()
            peers = await _presence_recipients(user_id)
            await manager.send_to_users(peers, {"type": "presence", "user_id": user_id, "online": False, "status": "offline"})
