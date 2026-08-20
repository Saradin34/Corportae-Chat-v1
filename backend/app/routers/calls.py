"""Call history routes (Asterisk AMI integration)."""
import json
import re

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import CallEvent, User
from ..schemas import CallEventOut, OriginateCallRequest
from ..security import get_current_admin, get_current_user

router = APIRouter(prefix="/api/calls", tags=["calls"])


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _number_tokens(value: str | None) -> list[str]:
    """Return useful numeric tokens from a phone/office field.

    AD fields are often not clean: "113 / +375...", "каб. 204, вн. 204",
    "+375... доб. 113".  Previously we concatenated all digits and short
    PBX extensions stopped matching users.
    """
    raw = str(value or "")
    parts = [p for p in re.findall(r"\d+", raw) if p]
    full = _digits(raw)
    out: list[str] = []
    for n in [full, *parts]:
        if n and n not in out:
            out.append(n)
    return out


def _matches_number(needle: str, raw: str | None) -> bool:
    n = _digits(needle)
    if not n:
        return False
    for p in _number_tokens(raw):
        if not p:
            continue
        if p == n:
            return True
        # Never match a long city/mobile number to a short internal extension
        # by suffix. Example: caller 92179121 must NOT match user extension 121.
        if min(len(n), len(p)) <= 5 and max(len(n), len(p)) > 5:
            continue
        # Very short values are exact-only.
        if len(n) < 3 or len(p) < 3:
            continue
        # Suffix matching is allowed only for real phone numbers, e.g. +375...
        # vs local 7-8 digit form, not for 3-5 digit PBX extensions.
        if p.endswith(n) or n.endswith(p):
            return True
    return False


def _user_ext(u: User) -> str:
    """Return PBX extension/phone for calls.

    Use users.phone only. users.office is a cabinet/room number and must not be
    used for call history matching or originate.
    """
    for token in _number_tokens(u.phone):
        if 2 <= len(token) <= 5:
            return token
    return _digits(u.phone)


def _display_user(u: User | None) -> str:
    if not u:
        return ""
    return u.full_name or u.username or ""


def _channel_ext(channel: str | None) -> str:
    ch = str(channel or "")
    m = re.search(r"(?:PJSIP|SIP|IAX2)/(\d+)(?:-|/|$)", ch, re.IGNORECASE)
    return m.group(1) if m else ""


def _raw_indicates_outgoing_external(call: CallEvent, owner_ext: str) -> bool:
    """Detect old rows where an outgoing city call was stored as missed incoming.

    The AMI DialBegin for such calls often has Channel=PJSIP/<owner> and a long
    destination number in DestCallerIDNum/DestExten/Exten.  We only use this as
    a display/read normalization for long external numbers.
    """
    if call.direction == "outgoing":
        return True
    city = _digits(call.caller_number)
    owner = _digits(owner_ext or call.extension)
    if len(city) <= 5 or not owner:
        return False
    try:
        raw = json.loads(call.raw or "{}")
    except Exception:
        return False
    if _channel_ext(raw.get("Channel")) != owner:
        return False
    candidates = [raw.get("DestCallerIDNum"), raw.get("DestExten"), raw.get("Exten"), raw.get("ConnectedLineNum")]
    return any(_digits(v) == city for v in candidates)


async def _find_user_by_number(db: AsyncSession, number: str) -> User | None:
    n = _digits(number)
    if not n:
        return None
    users = (await db.execute(select(User))).scalars().all()
    # Prefer active users, but still allow inactive records as a last-resort
    # phonebook for old call history.
    users.sort(key=lambda u: 0 if getattr(u, "is_active", True) else 1)
    for u in users:
        if _matches_number(n, u.phone):
            return u
    return None


async def _is_office_only_number(db: AsyncSession, number: str) -> bool:
    n = _digits(number)
    if not n:
        return False
    users = (await db.execute(select(User))).scalars().all()
    if any(_matches_number(n, u.phone) for u in users):
        return False
    return any(_matches_number(n, u.office) for u in users)


async def _call_out(db: AsyncSession, call: CallEvent) -> CallEventOut:
    out = CallEventOut.model_validate(call)
    callee = (await db.execute(select(User).where(User.id == call.user_id))).scalar_one_or_none() if call.user_id else None
    caller = await _find_user_by_number(db, call.caller_number)
    self_leg = bool(caller and callee and caller.id == callee.id)
    caller_is_office_only = await _is_office_only_number(db, call.caller_number)

    # If a long external/city number did not match a user exactly, do not reuse
    # stored caller_name that could have been assigned by old suffix matching
    # (e.g. 92179121 -> extension 121 -> wrong employee name).
    caller_number_digits = _digits(call.caller_number)
    safe_stored_caller_name = call.caller_name if (caller or len(caller_number_digits) <= 5) else ""
    caller_display = _display_user(caller) or safe_stored_caller_name or call.caller_number or "Неизвестный номер"
    callee_display = _display_user(callee) or call.extension or "Неизвестный получатель"
    callee_number = _user_ext(callee) if callee else (call.extension or "")

    # For outgoing calls, call.user_id is the owner/caller, and call.extension is
    # the dialed destination (including city/mobile numbers). Do not render it
    # as if the external number called the owner. Also normalize older broken
    # rows where outgoing city calls were stored as missed incoming.
    is_outgoing_external_fix = _raw_indicates_outgoing_external(call, callee_number)
    if call.direction == "outgoing" or is_outgoing_external_fix:
        owner_display = _display_user(callee) or call.caller_name or callee_number or "Вы"
        dialed_number = call.extension if call.direction == "outgoing" else call.caller_number
        target_user = await _find_user_by_number(db, dialed_number)
        caller_display = owner_display
        callee_number = dialed_number or ""
        callee_display = _display_user(target_user) or dialed_number or "Неизвестный получатель"
        out.direction = "outgoing"
        if out.status == "missed":
            out.status = "ended"
            out.is_read = True

    # Only one safe normalization: if the call has answered_at, it is accepted.
    # Do NOT convert ended to missed here: tabs must use explicit raw status.
    if call.answered_at and call.status != "answered":
        out.status = "answered"
    # If caller_number and recipient map to the same app user (e.g. user has
    # office/cabinet 401 and extension 204), this is a PBX self/outgoing leg,
    # not a missed incoming call. Keep it out of the missed tab/badge.
    if (self_leg or caller_is_office_only) and out.status == "missed":
        out.status = "ended"
        out.direction = "outgoing"

    out.caller_display = caller_display
    out.callee_name = _display_user(callee)
    out.callee_number = callee_number
    out.callee_display = callee_display
    if out.status == "missed":
        out.call_summary = f"{caller_display} звонил(а) {callee_display}"
    elif out.status == "answered":
        out.call_summary = f"{caller_display} разговаривал(а) с {callee_display}"
    elif out.status == "ringing":
        out.call_summary = f"{caller_display} звонит {callee_display}"
    else:
        out.call_summary = f"{caller_display} → {callee_display}"
    return out

@router.get("", response_model=list[CallEventOut])
async def my_calls(
    status: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(CallEvent).where(CallEvent.user_id == user.id)
    if status:
        stmt = stmt.where(CallEvent.status == status)
    stmt = stmt.order_by(desc(CallEvent.id)).limit(max(1, min(limit, 300)))
    rows = (await db.execute(stmt)).scalars().all()
    out = [await _call_out(db, r) for r in rows]
    if status:
        out = [r for r in out if r.status == status]
    return out


@router.get("/missed", response_model=list[CallEventOut])
async def my_missed_calls(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(CallEvent)
        .where(CallEvent.user_id == user.id, CallEvent.status == "missed")
        .order_by(desc(CallEvent.id))
        .limit(max(1, min(limit, 300)))
    )
    rows = (await db.execute(stmt)).scalars().all()
    out = [await _call_out(db, r) for r in rows]
    return [r for r in out if r.status == "missed"]


@router.get("/unread-count")
async def missed_unread_count(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (await db.execute(
        select(CallEvent)
        .where(CallEvent.user_id == user.id, CallEvent.is_read == False)  # noqa: E712
        .order_by(desc(CallEvent.id))
        .limit(500)
    )).scalars().all()
    out = [await _call_out(db, r) for r in rows]
    return {"count": sum(1 for r in out if r.status == "missed")}


@router.post("/{call_id}/read")
async def mark_call_read(
    call_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    call = (await db.execute(select(CallEvent).where(CallEvent.id == call_id))).scalar_one_or_none()
    if not call or (call.user_id != user.id and user.role != "admin"):
        return {"ok": True}
    call.is_read = True
    await db.commit()
    return {"ok": True}


@router.post("/read-all")
async def mark_all_calls_read(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (await db.execute(select(CallEvent).where(CallEvent.user_id == user.id, CallEvent.is_read == False))).scalars().all()  # noqa: E712
    for c in rows:
        c.is_read = True
    await db.commit()
    return {"ok": True, "updated": len(rows)}


@router.post("/originate")
async def originate_call(
    data: OriginateCallRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target = (await db.execute(select(User).where(User.id == data.to_user_id, User.is_active == True))).scalar_one_or_none()  # noqa: E712
    if not target:
        return {"ok": False, "error": "Пользователь не найден"}
    from_ext = _user_ext(user)
    to_ext = _user_ext(target)
    if not from_ext:
        return {"ok": False, "error": "У вашего профиля не указан телефон/внутренний номер"}
    if not to_ext:
        return {"ok": False, "error": "У пользователя не указан телефон/внутренний номер"}
    # Click-to-call must avoid the old AMI Originate callback flow:
    #   PBX calls my phone -> I answer -> PBX calls the target.
    # AMI listener remains enabled elsewhere for call history/missed calls.
    #
    # Preferred for Yeastar P-Series: OpenAPI /call/dial with auto_answer=yes.
    # This uses PBX API credentials and should remove the manual pickup step on
    # phones that support auto-answer.
    mode = (settings.CALL_CONTROL_MODE or "").lower().strip()
    if settings.YEASTAR_ENABLED or mode == "yeastar":
        from ..yeastar import yeastar_dial
        result = yeastar_dial(from_ext, to_ext)
        result["from_ext"] = from_ext
        result["to_ext"] = to_ext
        return result

    # Generic HTTP phone-control fallback for non-Yeastar deployments.
    from ..phone_control import ip_phone_dial
    phone_result = ip_phone_dial(from_ext, to_ext)
    phone_result["from_ext"] = from_ext
    phone_result["to_ext"] = to_ext
    return phone_result

    # OLD AMI CALLBACK MODE — intentionally disabled/commented out.
    # If this code runs, the PBX will ring the caller first, which is exactly
    # what we do NOT want for the chat call button.
    #
    # from ..ami import ami_originate
    # caller_id = from_ext
    # result = await ami_originate(from_ext, to_ext, caller_id=caller_id)
    # if result.get("ok"):
    #     result["from_ext"] = from_ext
    #     result["to_ext"] = to_ext
    # return result


@router.get("/admin", response_model=list[CallEventOut])
async def admin_calls(
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    rows = (await db.execute(select(CallEvent).order_by(desc(CallEvent.id)).limit(max(1, min(limit, 500))))).scalars().all()
    return [await _call_out(db, r) for r in rows]
