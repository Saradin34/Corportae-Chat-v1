"""WebSocket endpoint for real-time messaging & presence."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..database import async_session_maker
from ..models import ChatMember, User
from ..security import get_user_from_token
from ..ws_manager import manager

router = APIRouter()


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
    # If the user set a manual status (dnd / vacation), advertise that instead
    # of plain "online" so peers see the right badge immediately.
    manual_status = (u.status if u else "") or ""
    peers = await _user_chat_member_ids(user_id)
    await manager.send_to_users(
        peers,
        {"type": "presence", "user_id": user_id, "online": True, "status": manual_status or "online"},
    )

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
                peers = await _user_chat_member_ids(user_id)
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
            peers = await _user_chat_member_ids(user_id)
            await manager.send_to_users(peers, {"type": "presence", "user_id": user_id, "online": False})
