"""In-memory WebSocket connection manager for real-time delivery."""
import asyncio
import time
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        # user_id -> set of websockets (multiple tabs/devices)
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        # user_id -> presence status: "online" | "away" | "dnd" (transient, in-memory)
        self._status: dict[int, str] = {}
        self._lock = asyncio.Lock()
        self._send_sem = asyncio.Semaphore(48)
        self._bg_tasks: set[asyncio.Task] = set()
        self._members_cache: dict[int, list[int]] = {}
        self._members_cache_at: dict[int, float] = {}

    def set_status(self, user_id: int, status: str) -> None:
        if status not in ("online", "away", "dnd"):
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

    def remember_members(self, chat_id: int, ids: list[int]) -> None:
        self._members_cache[chat_id] = list(ids)
        self._members_cache_at[chat_id] = time.monotonic()

    def forget_members(self, chat_id: int) -> None:
        self._members_cache.pop(chat_id, None)
        self._members_cache_at.pop(chat_id, None)

    def cached_members(self, chat_id: int):
        at = self._members_cache_at.get(chat_id)
        if at is None or (time.monotonic() - at) > 60:
            return None
        return self._members_cache.get(chat_id)

    def spawn(self, coro) -> asyncio.Task:
        """Fire-and-forget without losing the task to GC (Python 3.11+)."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def send_to_user(self, user_id: int, message: dict) -> None:
        conns = list(self._connections.get(user_id, set()))
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                await self.disconnect(user_id, ws)

    async def send_to_users(self, user_ids: list[int], message: dict) -> None:
        """Fan-out concurrently so a 120-person group does not stall the event loop."""
        ids = [uid for uid in set(user_ids) if uid in self._connections]
        if not ids:
            return

        async def _one(uid: int) -> None:
            async with self._send_sem:
                await self.send_to_user(uid, message)

        await asyncio.gather(*(_one(uid) for uid in ids), return_exceptions=True)


manager = ConnectionManager()
