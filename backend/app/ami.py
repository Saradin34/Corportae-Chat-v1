"""Asterisk AMI listener: incoming call notifications + missed calls.

The listener is intentionally tolerant to different Asterisk/FreePBX-like AMI
field layouts. It maps PBX extensions to users by users.phone / users.office.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .config import settings
from .database import async_session_maker
from .models import CallEvent, User
from .ws_manager import manager

logger = logging.getLogger("corporate-chat")

_digits = re.compile(r"\D+")
_active: dict[str, int] = {}  # linked_id/unique_id -> CallEvent.id
_answered: set[str] = set()
_notified: set[str] = set()  # linked_id/call_id already sent as incoming popup
_facts: dict[str, dict[str, str]] = {}  # linked_id -> accumulated caller/callee facts


def _norm_phone(value: str | None) -> str:
    return _digits.sub("", str(value or ""))


def _number_tokens(value: str | None) -> list[str]:
    raw = str(value or "")
    parts = [p for p in re.findall(r"\d+", raw) if p]
    full = _norm_phone(raw)
    out: list[str] = []
    for n in [full, *parts]:
        if n and n not in out:
            out.append(n)
    return out


def _matches_number(needle: str | None, raw: str | None) -> bool:
    n = _norm_phone(needle)
    if not n:
        return False
    for p in _number_tokens(raw):
        if not p:
            continue
        if len(n) < 3 or len(p) < 3:
            if p == n:
                return True
        elif p == n or p.endswith(n) or n.endswith(p):
            return True
    return False


def _same_number(a: str | None, b: str | None) -> bool:
    na, nb = _norm_phone(a), _norm_phone(b)
    return bool(na and nb and (na == nb or na.endswith(nb) or nb.endswith(na)))


def _channel_ext(channel: str | None) -> str:
    """Extract endpoint extension from AMI Channel like PJSIP/204-000abc."""
    ch = str(channel or "")
    m = re.search(r"(?:PJSIP|SIP|IAX2)/(\d+)(?:-|/|$)", ch, re.IGNORECASE)
    return m.group(1) if m else ""


def _clean_caller_name(name: str | None, caller_number: str | None = None, callee_ext: str | None = None) -> str:
    """Drop PBX pseudo names like <unknown>, ring group number, or phone number."""
    v = _first(name)
    if not v or v.lower() in ("<unknown>", "unknown", "anonymous"):
        return ""
    if _same_number(v, caller_number) or _same_number(v, callee_ext):
        return ""
    # Many PBXs put ring group/exten (e.g. 6311) into CallerIDName. If the
    # value is only digits and wasn't matched to a real user, it is not a name.
    if _norm_phone(v) == v:
        return ""
    return v


def _event_key(ev: dict[str, str]) -> str:
    return ev.get("Linkedid") or ev.get("LinkedID") or ev.get("Uniqueid") or ev.get("UniqueID") or ""


def _first(*vals: str | None) -> str:
    for v in vals:
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return ""


def _event_name(ev: dict[str, str]) -> str:
    return (ev.get("Event") or "").strip().lower()


def _event_state(ev: dict[str, str]) -> str:
    return (ev.get("ChannelStateDesc") or ev.get("State") or ev.get("DialStatus") or "").strip().lower()


async def _find_user_by_extension(ext: str) -> User | None:
    n = _norm_phone(ext)
    if not n:
        return None
    async with async_session_maker() as db:
        users = (await db.execute(select(User))).scalars().all()
        users.sort(key=lambda u: 0 if getattr(u, "is_active", True) else 1)
        for u in users:
            if _matches_number(n, u.phone) or _matches_number(n, u.office):
                return u
    return None


async def _missed_unread_count(user_id: int) -> int:
    from sqlalchemy import func
    async with async_session_maker() as db:
        return (await db.execute(
            select(func.count()).select_from(CallEvent).where(
                CallEvent.user_id == user_id,
                CallEvent.status == "missed",
                CallEvent.answered_at == None,  # noqa: E711
                CallEvent.is_read == False,  # noqa: E712
            )
        )).scalar() or 0


async def _mark_previous_missed_read(user_id: int | None, caller_number: str) -> int:
    if not user_id or not caller_number:
        return 0
    norm = _norm_phone(caller_number)
    if not norm:
        return 0
    async with async_session_maker() as db:
        rows = (await db.execute(select(CallEvent).where(
            CallEvent.user_id == user_id,
            CallEvent.status == "missed",
            CallEvent.is_read == False,  # noqa: E712
        ))).scalars().all()
        changed = 0
        for c in rows:
            cn = _norm_phone(c.caller_number)
            if cn and (cn == norm or cn.endswith(norm) or norm.endswith(cn)):
                c.is_read = True
                changed += 1
        if changed:
            await db.commit()
        return changed


async def _pick_callee(ev: dict[str, str]) -> tuple[str, User | None]:
    """Pick the called internal extension/user from AMI fields.

    The correct callee is usually the endpoint extension (204), not DID,
    ring-group, trunk or route numbers. We therefore try fields that identify
    the endpoint first and accept only values that match an app user phone/office.
    """
    key = _event_key(ev)
    f = _facts.get(key) if key else None
    if f:
        # Facts know the two legs and are safer than a single AMI event.
        for cand in (f.get("destination"), f.get("callee")):
            cand = _first(cand)
            if cand:
                u = await _find_user_by_extension(cand)
                if u:
                    return cand, u

    channel_ext = _channel_ext(ev.get("Channel"))
    dest_channel_ext = _channel_ext(ev.get("DestChannel"))

    preferred = [
        ev.get("UserName"),          # CSTA Delivered/Established: endpoint user
        ev.get("DestCallerIDNum"),   # DialBegin destination endpoint
        dest_channel_ext,
        channel_ext,
        ev.get("SrcExten"),
        ev.get("DestExten"),
        ev.get("Exten"),
        ev.get("ConnectedLineNum"),
        ev.get("CallerIDNum"),       # fallback for endpoint channel Newstate
    ]

    for cand in preferred:
        cand = _first(cand)
        if not cand:
            continue
        u = await _find_user_by_extension(cand)
        if u:
            return cand, u

    # Return a non-empty fallback for logging/history, even if no user matched.
    return _first(*preferred), None


async def _pick_caller(ev: dict[str, str], callee_ext: str) -> tuple[str, str]:
    # Prefer accumulated facts; they combine DialBegin + endpoint events and
    # prevent caller/callee leg inversion on physical SIP phones.
    key = _event_key(ev)
    f = _facts.get(key) if key else None
    if f:
        cand = _first(f.get("caller"), f.get("originator"))
        if cand and not _same_number(cand, callee_ext):
            u = await _find_user_by_extension(cand)
            if u:
                return cand, (u.full_name or u.username or "")
            return cand, f.get("caller_name") or ""

    # Prefer a known non-callee number as caller. In some events CallerIDNum is
    # the callee, while ConnectedLineNum is the true caller.
    candidates = [
        ev.get("CallerNumber"),       # UserEvent UpdateCaller
        ev.get("CallerIDNum"),
        ev.get("ConnectedLineNum"),
        ev.get("Src"),
        ev.get("ConnectedLineName"),
    ]
    # Known user candidate wins.
    for cand in candidates:
        cand = _first(cand)
        if not cand or _same_number(cand, callee_ext):
            continue
        u = await _find_user_by_extension(cand)
        if u:
            return cand, (u.full_name or u.username or "")
    # Any non-callee candidate.
    for cand in candidates:
        cand = _first(cand)
        if cand and not _same_number(cand, callee_ext):
            name = _clean_caller_name(_first(ev.get("CallerIDName"), ev.get("ConnectedLineName")), cand, callee_ext)
            u = await _find_user_by_extension(cand)
            if u:
                name = u.full_name or u.username or name
            return cand, name
    # Last resort: use whatever AMI gave, but avoid showing callee as caller.
    fallback = _first(*candidates)
    name = _clean_caller_name(_first(ev.get("CallerIDName"), ev.get("ConnectedLineName")), fallback, callee_ext)
    if _same_number(fallback, callee_ext):
        name = ""
    return fallback, name


def _is_unknown(v: str | None) -> bool:
    x = str(v or "").strip().lower()
    return not x or x in ("<unknown>", "unknown", "anonymous", "none", "null")


async def _record_event_facts(ev: dict[str, str]) -> dict[str, str]:
    """Accumulate best caller/callee numbers per Linkedid.

    IMPORTANT for physical SIP phones: the same Linkedid contains both legs:
      - caller leg: Channel=PJSIP/219...
      - callee leg: DestChannel/PJSIP/204... or later Channel=PJSIP/204...

    We must never let a later caller-leg event overwrite the real callee,
    otherwise the caller sees his own outgoing unanswered call as "missed".
    Therefore every fact has a priority and the destination endpoint wins over
    a plain Channel endpoint.
    """
    key = _event_key(ev)
    if not key:
        return {}
    f = _facts.setdefault(key, {})

    name = _event_name(ev)
    csta_name = (ev.get("EventName") or "").strip().lower()
    channel_ext = _channel_ext(ev.get("Channel"))
    dest_channel_ext = _channel_ext(ev.get("DestChannel"))

    # DialBegin with DestChannel is the clearest two-leg event:
    # Channel = caller/originator, DestChannel/DestCallerIDNum = callee.
    if dest_channel_ext and channel_ext and not _same_number(channel_ext, dest_channel_ext):
        if await _find_user_by_extension(channel_ext):
            f["originator"] = channel_ext
        if await _find_user_by_extension(dest_channel_ext):
            f["destination"] = dest_channel_ext
    dest_cid = _first(ev.get("DestCallerIDNum"))
    if dest_cid and await _find_user_by_extension(dest_cid):
        f["destination"] = dest_cid
    # Some systems expose the endpoint in CSTA UserName.
    if csta_name in ("deliveredevent", "establishedevent") and _first(ev.get("UserName")):
        f["destination"] = _first(ev.get("UserName"))

    originator = f.get("originator") or ""
    destination = f.get("destination") or ""

    async def set_callee(cand: str | None, priority: int) -> bool:
        cand = _first(cand)
        if not cand or _is_unknown(cand):
            return False
        # Never choose the known caller/originator as callee if we already know
        # a different destination. This is the key fix for caller-side missed.
        if originator and _same_number(cand, originator) and destination and not _same_number(destination, originator):
            return False
        u = await _find_user_by_extension(cand)
        if not u:
            return False
        old_pri = int(f.get("callee_priority") or 0)
        if old_pri and old_pri > priority and f.get("callee"):
            return False
        # If we already have explicit destination, do not overwrite it with a
        # lower-priority Channel/Src/Exten value from the other leg.
        if destination and not _same_number(cand, destination) and priority < 100:
            return False
        f["callee"] = cand
        f["callee_priority"] = str(priority)
        f["callee_user_id"] = str(u.id)
        f["callee_name"] = u.full_name or u.username or cand
        return True

    # Callee: explicit destination endpoint fields first. Plain Channel is only
    # accepted when it is not the known originator/caller leg.
    await set_callee(ev.get("UserName"), 120 if csta_name in ("deliveredevent", "establishedevent") else 85)
    await set_callee(ev.get("DestCallerIDNum"), 115)
    await set_callee(dest_channel_ext, 110)
    await set_callee(destination, 108)
    if not (originator and channel_ext and _same_number(channel_ext, originator)):
        await set_callee(channel_ext, 80)
    await set_callee(ev.get("SrcExten"), 65)
    await set_callee(ev.get("DestExten"), 55)
    await set_callee(ev.get("Exten"), 45)

    callee = f.get("callee") or ""

    async def set_caller(cand: str | None, priority: int) -> bool:
        cand = _first(cand)
        if not cand or _is_unknown(cand) or _same_number(cand, callee):
            return False
        old_pri = int(f.get("caller_priority") or 0)
        if old_pri and old_pri > priority and f.get("caller"):
            return False
        # Do not replace a long external/mobile number with a short route/group
        # number unless the new value is a known user and has higher priority.
        known = await _find_user_by_extension(cand)
        if f.get("caller") and not known and len(_norm_phone(f["caller"])) >= len(_norm_phone(cand)) and priority <= old_pri:
            return False
        f["caller"] = cand
        f["caller_priority"] = str(priority)
        if known:
            f["caller_name"] = known.full_name or known.username or cand
        else:
            clean_name = _clean_caller_name(_first(ev.get("CallerIDName"), ev.get("ConnectedLineName")), cand, callee)
            if clean_name:
                f["caller_name"] = clean_name
        return True

    # Caller: explicit CallerNumber/CallerID first. On incoming external calls
    # endpoint events often carry the real caller in ConnectedLineNum.
    await set_caller(ev.get("CallerNumber"), 120)
    await set_caller(ev.get("CallerIDNum"), 105)
    await set_caller(ev.get("ConnectedLineNum"), 95)
    await set_caller(ev.get("Src"), 80)
    await set_caller(originator, 90)

    return f


async def _create_call_from_facts(key: str, ev: dict[str, str], status: str = "ringing") -> tuple[CallEvent | None, User | None]:
    f = _facts.get(key) or await _record_event_facts(ev)
    callee = f.get("callee") or ""
    originator = f.get("originator") or ""
    destination = f.get("destination") or ""
    # Final safety: if facts still point callee to the originator/caller leg,
    # switch to the known destination endpoint. This prevents the caller from
    # receiving a missed call when he dials and the other side does not answer.
    if originator and destination and _same_number(callee, originator) and not _same_number(destination, originator):
        callee = destination
        f["callee"] = destination
    user = await _find_user_by_extension(callee)
    if not user:
        if settings.AMI_DEBUG_EVENTS:
            logger.info("AMI call ignored: no app user for callee facts=%s event=%s", f, ev)
        return None, None

    # Do not create duplicate history rows for the same PBX call. Several AMI
    # events (DialBegin/Newstate/BridgeEnter/CDR) share the same Linkedid.
    async with async_session_maker() as db:
        existing = (await db.execute(
            select(CallEvent).where(CallEvent.linked_id == key, CallEvent.user_id == user.id).order_by(CallEvent.id.desc())
        )).scalar_one_or_none()
        if existing:
            if status == "answered" and existing.status in ("ringing", "missed", "ended"):
                existing.status = "answered"
                existing.answered_at = existing.answered_at or datetime.now(timezone.utc)
            existing.raw = json.dumps(ev, ensure_ascii=False)[:4000]
            await db.commit()
            await db.refresh(existing)
            _active[key] = existing.id
            return existing, user

    caller = f.get("caller") or ""
    caller_name = f.get("caller_name") or ""
    caller_user = await _find_user_by_extension(caller)
    if caller_user:
        caller_name = caller_user.full_name or caller_user.username or caller_name
    # Do not create an incoming-call popup for the caller's own outbound leg
    # (common AMI pattern: Channel=PJSIP/204 with CallerIDNum=204 and no real
    # callee yet). Wait until the callee endpoint event arrives.
    if not caller or _is_unknown(caller) or _same_number(caller, callee):
        if settings.AMI_DEBUG_EVENTS:
            logger.info("AMI call ignored: caller is unknown/same as callee facts=%s event=%s", f, ev)
        return None, None
    async with async_session_maker() as db:
        call = CallEvent(
            user_id=user.id,
            extension=_norm_phone(callee) or callee,
            caller_number=caller,
            caller_name=caller_name,
            direction="incoming",
            status=status,
            unique_id=ev.get("Uniqueid") or ev.get("UniqueID") or "",
            linked_id=key,
            raw=json.dumps(ev, ensure_ascii=False)[:4000],
        )
        if status == "answered":
            call.answered_at = datetime.now(timezone.utc)
        db.add(call)
        await db.commit()
        await db.refresh(call)
        if key:
            _active[key] = call.id
        return call, user


async def _create_or_get_ringing(ev: dict[str, str]) -> tuple[CallEvent | None, User | None]:
    key = _event_key(ev)
    if key:
        await _record_event_facts(ev)
    if key and key in _active:
        async with async_session_maker() as db:
            call = (await db.execute(select(CallEvent).where(CallEvent.id == _active[key]))).scalar_one_or_none()
            if call:
                user = (await db.execute(select(User).where(User.id == call.user_id))).scalar_one_or_none() if call.user_id else None

                # Later AMI events often contain more accurate endpoint data.
                # Update callee first (e.g. ringgroup 6311 -> endpoint 204).
                better_ext, better_user = await _pick_callee(ev)
                changed = False
                if better_user and (not call.user_id or not _same_number(better_ext, call.extension)):
                    call.user_id = better_user.id
                    call.extension = _norm_phone(better_ext) or better_ext
                    user = better_user
                    changed = True

                better_caller, better_name = await _pick_caller(ev, call.extension)
                if better_caller and not _same_number(better_caller, call.extension):
                    # Update when current caller is empty or was accidentally set to callee.
                    if not call.caller_number or _same_number(call.caller_number, call.extension) or call.caller_number in ("<unknown>", "unknown"):
                        call.caller_number = better_caller
                        changed = True
                    if better_name and (not call.caller_name or _same_number(call.caller_name, call.extension) or call.caller_name.lower() in ("<unknown>", "unknown")):
                        call.caller_name = better_name
                        changed = True

                if changed:
                    call.raw = json.dumps(ev, ensure_ascii=False)[:4000]
                    await db.commit()
                    await db.refresh(call)
                return call, user

    if key:
        async with async_session_maker() as db:
            existing = (await db.execute(select(CallEvent).where(CallEvent.linked_id == key).order_by(CallEvent.id.desc()))).scalar_one_or_none()
            if existing:
                _active[key] = existing.id
                user = (await db.execute(select(User).where(User.id == existing.user_id))).scalar_one_or_none() if existing.user_id else None
                return existing, user

    if key:
        fact_call, fact_user = await _create_call_from_facts(key, ev, status="ringing")
        if fact_call:
            return fact_call, fact_user

    ext, user = await _pick_callee(ev)
    if not user:
        if settings.AMI_DEBUG_EVENTS:
            logger.info("AMI ringing ignored: no user for event=%s", ev)
        return None, None
    caller, caller_name = await _pick_caller(ev, ext)
    # Suppress false incoming-call notifications on the caller side. If the
    # only number we know is the callee itself (or unknown), this is usually the
    # outbound leg, not a call that should pop up for the user.
    if not caller or _is_unknown(caller) or _same_number(caller, ext):
        if settings.AMI_DEBUG_EVENTS:
            logger.info("AMI ringing ignored: caller is unknown/same as callee caller=%s ext=%s event=%s", caller, ext, ev)
        return None, None

    async with async_session_maker() as db:
        call = CallEvent(
            user_id=user.id,
            extension=_norm_phone(ext) or ext,
            caller_number=caller,
            caller_name=caller_name,
            direction="incoming",
            status="ringing",
            unique_id=ev.get("Uniqueid") or ev.get("UniqueID") or "",
            linked_id=key,
            raw=json.dumps(ev, ensure_ascii=False)[:4000],
        )
        db.add(call)
        await db.commit()
        await db.refresh(call)
        if key:
            _active[key] = call.id
        return call, user


async def _notify_incoming(call: CallEvent, user: User) -> None:
    # AMI sends many Ringing/Newstate/NewCallerid/DialBegin events for one PBX
    # call. The history row is deduped separately, but without this guard the
    # frontend receives many incoming_call popups/system notifications.
    notify_key = call.linked_id or call.unique_id or f"call:{call.id}"
    if notify_key in _notified:
        return
    _notified.add(notify_key)
    await manager.send_to_user(user.id, {
        "type": "incoming_call",
        "call": {
            "id": call.id,
            "caller_number": call.caller_number,
            "caller_name": call.caller_name,
            "extension": call.extension,
            "started_at": call.started_at.isoformat() if call.started_at else None,
        },
    })


async def _mark_answered(ev: dict[str, str]) -> None:
    key = _event_key(ev)
    if not key:
        return
    _answered.add(key)
    cid = _active.get(key)
    if not cid:
        # Some PBXs don't emit a clean Ringing/DialBegin event. Try to create
        # a call record from the answered event so history still works.
        call, _ = await _create_call_from_facts(key, ev, status="answered")
        cid = call.id if call else None
    if not cid:
        return
    async with async_session_maker() as db:
        call = (await db.execute(select(CallEvent).where(CallEvent.id == cid))).scalar_one_or_none()
        if call:
            call.status = "answered"
            call.answered_at = call.answered_at or datetime.now(timezone.utc)
            await db.commit()
            cleared = await _mark_previous_missed_read(call.user_id, call.caller_number)
            if cleared and call.user_id:
                await manager.send_to_user(call.user_id, {"type": "missed_calls_count", "count": await _missed_unread_count(call.user_id)})


async def _mark_ended_or_missed(ev: dict[str, str]) -> None:
    key = _event_key(ev)
    cid = _active.pop(key, None) if key else None
    if not cid and key:
        facts = _facts.get(key) or await _record_event_facts(ev)
        # Do not create a new missed row on Hangup/CDR from ambiguous caller-leg
        # facts. A missed row may be created only if we have a destination
        # endpoint (DialBegin DestChannel/DestCallerIDNum or CSTA UserName).
        if not facts.get("destination"):
            return
        call, _ = await _create_call_from_facts(key, ev, status="ringing")
        cid = call.id if call else None
    if not cid:
        return
    async with async_session_maker() as db:
        call = (await db.execute(select(CallEvent).where(CallEvent.id == cid))).scalar_one_or_none()
        if not call:
            return
        disposition = (ev.get("Disposition") or ev.get("DialStatus") or ev.get("DestChannelStateDesc") or ev.get("ChannelStateDesc") or "").strip().lower()
        answered = (
            key in _answered
            or call.status == "answered"
            or bool(call.answered_at)
            or disposition in ("answer", "answered", "up")
        )
        # Return to the first stable logic: answered stays answered; otherwise
        # only rows that were created for the recipient become missed. False
        # caller-side rows are prevented at creation time by not creating calls
        # on raw Newchannel/NewCallerid events.
        call.status = "answered" if answered else "missed"
        call.ended_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(call)
        if call.status == "missed" and call.user_id:
            unread_count = await _missed_unread_count(call.user_id)
            await manager.send_to_user(call.user_id, {
                "type": "missed_call",
                "unread_count": unread_count,
                "call": {
                    "id": call.id,
                    "caller_number": call.caller_number,
                    "caller_name": call.caller_name,
                    "extension": call.extension,
                    "started_at": call.started_at.isoformat() if call.started_at else None,
                },
            })
    if key in _answered:
        _answered.discard(key)
    if key in _notified:
        _notified.discard(key)
    if key in _facts:
        _facts.pop(key, None)


async def handle_ami_event(ev: dict[str, str]) -> None:
    name = _event_name(ev)
    state = _event_state(ev)
    csta_name = (ev.get("EventName") or "").strip().lower()
    facts = await _record_event_facts(ev)
    if settings.AMI_DEBUG_EVENTS:
        logger.info("AMI event: %s", ev)

    # Collect Newchannel/NewCallerid facts only. Do not create a call row there:
    # those early events are often the caller's own phone leg.
    if name in ("newchannel", "newcallerid"):
        return

    has_explicit_destination = bool(
        facts.get("destination")
        or _first(ev.get("DestCallerIDNum"))
        or _channel_ext(ev.get("DestChannel"))
        or _first(ev.get("UserName"))
        or csta_name == "deliveredevent"
    )
    # RINGING alone is ambiguous on physical phones: it can be the caller's own
    # leg. Create on ringing only after we know an explicit destination.
    if name in ("dialbegin", "newconnectedline") or csta_name == "deliveredevent" or (state == "ringing" and has_explicit_destination):
        call, user = await _create_or_get_ringing(ev)
        if call and user and call.status == "ringing":
            await _notify_incoming(call, user)
    elif name in ("bridgeenter", "link") or state == "up" or csta_name == "establishedevent":
        await _mark_answered(ev)
    elif name == "dialend":
        # DialEnd=ANSWER is not the end of the call, it only means the callee
        # answered. Do not pop active call here; wait for Hangup/CDR.
        if (ev.get("DialStatus") or "").upper() == "ANSWER":
            await _mark_answered(ev)
        else:
            await _mark_ended_or_missed(ev)
    elif name in ("hangup", "hanguprequest", "cdr"):
        await _mark_ended_or_missed(ev)


async def _read_ami_message(reader: asyncio.StreamReader, timeout: float = 10.0) -> dict[str, str] | None:
    msg: dict[str, str] = {}
    while True:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            if msg:
                return msg
            raise TimeoutError(f"AMI did not send a complete message within {timeout:.0f}s")
        if not line:
            return None if msg else None
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if text == "":
            return msg if msg else {}
        if ":" in text:
            k, v = text.split(":", 1)
            msg[k.strip()] = v.strip()
        elif text:
            if text.lower().startswith("asterisk call manager"):
                return {"Greeting": text}
            msg.setdefault("_line", text)


async def _send_action(writer: asyncio.StreamWriter, **fields: Any) -> None:
    data = "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
    writer.write(data.encode("utf-8"))
    await writer.drain()


async def ami_originate(from_ext: str, to_ext: str, caller_id: str = "") -> dict[str, str | bool]:
    if not settings.AMI_ENABLED:
        return {"ok": False, "error": "AMI integration is disabled"}
    if not settings.AMI_USERNAME or not settings.AMI_SECRET:
        return {"ok": False, "error": "AMI credentials are not configured"}
    channel = settings.AMI_ORIGINATE_CHANNEL_TEMPLATE.format(ext=from_ext)
    reader, writer = await asyncio.open_connection(settings.AMI_HOST, settings.AMI_PORT)
    try:
        await _read_ami_message(reader, timeout=8)
        await _send_action(writer, Action="Login", Username=settings.AMI_USERNAME, Secret=settings.AMI_SECRET, Events="off")
        login = await _read_ami_message(reader, timeout=8)
        if not login or str(login.get("Response", "")).lower() != "success":
            return {"ok": False, "error": f"AMI login failed: {login}"}
        await _send_action(
            writer,
            Action="Originate",
            Channel=channel,
            Context=settings.AMI_ORIGINATE_CONTEXT,
            Exten=to_ext,
            Priority=settings.AMI_ORIGINATE_PRIORITY,
            CallerID=caller_id or from_ext,
            Timeout=settings.AMI_ORIGINATE_TIMEOUT_MS,
            Async="true",
        )
        resp = await _read_ami_message(reader, timeout=8)
        ok = bool(resp and str(resp.get("Response", "")).lower() == "success")
        return {"ok": ok, "response": json.dumps(resp or {}, ensure_ascii=False), "channel": channel, "to": to_ext}
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def ami_listener(stop_event: asyncio.Event) -> None:
    if not settings.AMI_ENABLED:
        logger.info("AMI listener disabled")
        return
    if not settings.AMI_USERNAME or not settings.AMI_SECRET:
        logger.warning("AMI enabled but AMI_USERNAME/AMI_SECRET is not configured")
        return

    while not stop_event.is_set():
        writer = None
        try:
            logger.info("Connecting to Asterisk AMI %s:%s", settings.AMI_HOST, settings.AMI_PORT)
            reader, writer = await asyncio.open_connection(settings.AMI_HOST, settings.AMI_PORT)
            greeting = await _read_ami_message(reader, timeout=8)
            logger.info("AMI greeting: %s", greeting)
            await _send_action(writer, Action="Login", Username=settings.AMI_USERNAME, Secret=settings.AMI_SECRET, Events="on")
            resp = await _read_ami_message(reader, timeout=8)
            logger.info("AMI login response: %s", resp)
            if not resp or str(resp.get("Response", "")).lower() != "success":
                logger.error("AMI login failed: %s", resp)
                writer.close(); await writer.wait_closed()
                await asyncio.sleep(settings.AMI_RECONNECT_SECONDS)
                continue
            logger.info("AMI connected and authenticated")
            while not stop_event.is_set():
                ev = await _read_ami_message(reader, timeout=60)
                if ev is None:
                    raise ConnectionError("AMI connection closed")
                if ev.get("Event"):
                    try:
                        await handle_ami_event(ev)
                    except Exception:
                        logger.exception("AMI event handling failed: %s", ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("AMI listener error: %s", e)
            try:
                if writer:
                    writer.close(); await writer.wait_closed()
            except Exception:
                pass
            await asyncio.sleep(settings.AMI_RECONNECT_SECONDS)
