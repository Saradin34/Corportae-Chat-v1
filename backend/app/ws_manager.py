"""WebSocket connection manager with optional Redis pub/sub fan-out.

Single worker (no Redis):
    Works purely in-memory — identical to the original behaviour.

Multiple workers / replicas (Redis configured):
    * Actual WebSocket sockets always live on the worker that accepted them
      (a socket can't be shared between processes).
    * Outgoing messages are PUBLISHED to a Redis channel; every worker's
      subscriber receives them and delivers to whichever sockets it owns.
      So a message sent from worker A reaches a user connected to worker B.
    * Presence (who is online + their status) is stored in Redis hashes and
      mirrored into a fast local cache, kept in sync via a presence channel.
      This keeps the hot `is_online()` / `get_status()` calls synchronous.

The manager degrades gracefully: if Redis can't be reached it transparently
falls back to in-memory mode (so dev/single-node deployments just work).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger("corporate-chat")

# Redis channels / keys
_CH_DELIVER = "cc:ws:deliver"     # {"targets": [int], "message": {...}}
_CH_PRESENCE = "cc:ws:presence"   # {"user_id": int, "online": bool, "status": str}
_KEY_ONLINE = "cc:online"         # hash: user_id -> connection count (across workers)
_KEY_STATUS = "cc:status"         # hash: user_id -> "online" | "away"


class ConnectionManager:
    def __init__(self) -> None:
        # user_id -> set of websockets owned by THIS worker
        self._connections: dict[int, set[WebSocket]] = defaultdict(set)
        # local count of this worker's own connections per user (for Redis deltas)
        self._local_counts: dict[int, int] = defaultdict(int)
        self._lock = asyncio.Lock()

        # ---- in-memory presence (used when Redis is off) ----
        self._status: dict[int, str] = {}

        # ---- Redis-backed presence mirror (used when Redis is on) ----
        self._redis = None
        self._pubsub = None
        self._reader_task: asyncio.Task | None = None
        self._mirror_online: set[int] = set()       # user_ids online anywhere
        self._mirror_status: dict[int, str] = {}     # user_id -> status

    @property
    def distributed(self) -> bool:
        return self._redis is not None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def init(self, redis_url: str | None = None) -> None:
        """Connect to Redis and start the subscriber. Safe to call once at
        startup. Falls back to in-memory mode if Redis is unavailable."""
        redis_url = redis_url or os.environ.get("REDIS_URL", "")
        if not redis_url:
            logger.info("WS manager: no REDIS_URL — running in-memory (single worker)")
            return
        try:
            import redis.asyncio as aioredis  # lazy import
            self._redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
            await self._redis.ping()
            # Load the current global presence snapshot into the local mirror.
            try:
                online = await self._redis.hgetall(_KEY_ONLINE)
                self._mirror_online = {int(uid) for uid, cnt in online.items() if int(cnt) > 0}
                status = await self._redis.hgetall(_KEY_STATUS)
                self._mirror_status = {int(uid): st for uid, st in status.items()}
            except Exception:  # noqa: BLE001
                self._mirror_online, self._mirror_status = set(), {}
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(_CH_DELIVER, _CH_PRESENCE)
            self._reader_task = asyncio.create_task(self._reader_loop())
            logger.info("WS manager: Redis pub/sub enabled (multi-worker ready)")
        except Exception as e:  # noqa: BLE001
            logger.warning("WS manager: Redis unavailable (%s) — falling back to in-memory", e)
            self._redis = None

    async def shutdown(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Best-effort: drop this worker's contribution to the online counts.
        if self._redis:
            try:
                async with self._lock:
                    items = list(self._local_counts.items())
                for uid, cnt in items:
                    if cnt > 0:
                        await self._redis.hincrby(_KEY_ONLINE, uid, -cnt)
                await self._pubsub.unsubscribe()
                await self._pubsub.aclose()
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Subscriber loop
    # ------------------------------------------------------------------
    async def _reader_loop(self) -> None:
        try:
            async for msg in self._pubsub.listen():
                if msg.get("type") != "message":
                    continue
                channel = msg.get("channel")
                try:
                    data = json.loads(msg.get("data") or "{}")
                except (ValueError, TypeError):
                    continue
                if channel == _CH_DELIVER:
                    await self._deliver_local(data.get("targets", []), data.get("message", {}))
                elif channel == _CH_PRESENCE:
                    self._apply_presence(data)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("WS reader loop ended: %s", e)

    def _apply_presence(self, data: dict) -> None:
        """Update the local presence mirror from a presence broadcast."""
        try:
            uid = int(data.get("user_id"))
        except (TypeError, ValueError):
            return
        if data.get("online"):
            self._mirror_online.add(uid)
            st = data.get("status")
            if st:
                self._mirror_status[uid] = st
        else:
            self._mirror_online.discard(uid)
            self._mirror_status.pop(uid, None)

    # ------------------------------------------------------------------
    # Presence (synchronous reads — backed by local mirror or memory)
    # ------------------------------------------------------------------
    def set_status(self, user_id: int, status: str) -> None:
        if status not in ("online", "away"):
            status = "online"
        if self.distributed:
            self._mirror_status[user_id] = status
            # persist + broadcast asynchronously (fire and forget)
            asyncio.create_task(self._persist_status(user_id, status))
        else:
            self._status[user_id] = status

    async def _persist_status(self, user_id: int, status: str) -> None:
        try:
            await self._redis.hset(_KEY_STATUS, user_id, status)
            await self._redis.publish(_CH_PRESENCE, json.dumps(
                {"user_id": user_id, "online": True, "status": status}))
        except Exception:  # noqa: BLE001
            pass

    def get_status(self, user_id: int) -> str:
        if self.distributed:
            if user_id not in self._mirror_online:
                return "offline"
            return self._mirror_status.get(user_id, "online")
        if user_id not in self._connections:
            return "offline"
        return self._status.get(user_id, "online")

    def is_online(self, user_id: int) -> bool:
        if self.distributed:
            return user_id in self._mirror_online
        return user_id in self._connections

    def online_user_ids(self) -> list[int]:
        if self.distributed:
            return list(self._mirror_online)
        return list(self._connections.keys())

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------
    async def connect(self, user_id: int, ws: WebSocket) -> None:
        await ws.accept()
        first_local = False
        async with self._lock:
            if not self._connections[user_id]:
                first_local = True
            self._connections[user_id].add(ws)
            self._local_counts[user_id] += 1

        if self.distributed:
            try:
                new_count = await self._redis.hincrby(_KEY_ONLINE, user_id, 1)
                # mirror locally right away
                self._mirror_online.add(user_id)
                if new_count == 1:
                    await self._redis.publish(_CH_PRESENCE, json.dumps(
                        {"user_id": user_id, "online": True, "status": "online"}))
            except Exception:  # noqa: BLE001
                self._mirror_online.add(user_id)
        # nothing extra for in-memory mode (presence derived from _connections)
        _ = first_local

    async def disconnect(self, user_id: int, ws: WebSocket) -> None:
        last_local = False
        async with self._lock:
            conns = self._connections.get(user_id)
            if conns:
                conns.discard(ws)
                self._local_counts[user_id] = max(0, self._local_counts[user_id] - 1)
                if not conns:
                    self._connections.pop(user_id, None)
                    self._local_counts.pop(user_id, None)
                    self._status.pop(user_id, None)
                    last_local = True

        if self.distributed and last_local:
            try:
                new_count = await self._redis.hincrby(_KEY_ONLINE, user_id, -1)
                if new_count <= 0:
                    await self._redis.hdel(_KEY_ONLINE, user_id)
                    await self._redis.hdel(_KEY_STATUS, user_id)
                    self._mirror_online.discard(user_id)
                    self._mirror_status.pop(user_id, None)
                    await self._redis.publish(_CH_PRESENCE, json.dumps(
                        {"user_id": user_id, "online": False}))
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------
    async def _deliver_local(self, user_ids, message: dict) -> None:
        """Send to sockets owned by THIS worker only."""
        for uid in set(user_ids):
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue
            for ws in list(self._connections.get(uid, set())):
                try:
                    await ws.send_json(message)
                except Exception:  # noqa: BLE001
                    await self.disconnect(uid, ws)

    async def send_to_user(self, user_id: int, message: dict) -> None:
        await self.send_to_users([user_id], message)

    async def send_to_users(self, user_ids, message: dict) -> None:
        ids = list({int(u) for u in user_ids})
        if not ids:
            return
        if self.distributed:
            # Publish once; every worker (incl. this one) delivers to its own
            # local sockets via the subscriber loop. No direct local send here,
            # otherwise this worker's clients would receive duplicates.
            try:
                await self._redis.publish(_CH_DELIVER, json.dumps({"targets": ids, "message": message}))
                return
            except Exception:  # noqa: BLE001
                # Redis hiccup — fall back to local delivery so we don't drop it.
                pass
        await self._deliver_local(ids, message)


manager = ConnectionManager()
