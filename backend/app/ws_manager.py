"""In-memory WebSocket connection manager for real-time delivery."""
import asyncio
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        # user_id -> set of websockets (multiple tabs/devices)
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        # user_id -> presence status: "online" | "away" (transient, in-memory)
        self._status: dict[int, str] = {}
        self._lock = asyncio.Lock()

    def set_status(self, user_id: int, status: str) -> None:
        if status not in ("online", "away"):
            status = "online"
        self._status[user_id] = status

    def get_status(self, user_id: int) -> str:
        if user_id not in self._connections:
            return "offline"
        return self._status.get(user_id, "online")

    async def connect(self, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections[user_id].add(ws)

    async def disconnect(self, user_id: int, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(user_id)
            if conns:
                conns.discard(ws)
                if not conns:
                    self._connections.pop(user_id, None)
                    self._status.pop(user_id, None)

    def is_online(self, user_id: int) -> bool:
        return user_id in self._connections

    def online_user_ids(self) -> list[int]:
        return list(self._connections.keys())

    async def send_to_user(self, user_id: int, message: dict) -> None:
        conns = list(self._connections.get(user_id, set()))
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                await self.disconnect(user_id, ws)

    async def send_to_users(self, user_ids: list[int], message: dict) -> None:
        for uid in set(user_ids):
            await self.send_to_user(uid, message)


manager = ConnectionManager()
