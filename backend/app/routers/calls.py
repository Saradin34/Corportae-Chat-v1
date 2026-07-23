"""Call history routes (Asterisk AMI integration)."""
import re

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

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
        # Short PBX extensions must match a token exactly. This prevents "1"
        # from matching 201/204 but allows "доб. 113" -> 113.
        if len(n) < 3 or len(p) < 3:
            if p == n:
                return True
        elif p == n or p.endswith(n) or n.endswith(p):
            return True
    return False


def _user_ext(u: User) -> str:
    for raw in (u.phone, u.office):
        for token in _number_tokens(raw):
            if 2 <= len(token) <= 5:
                return token
    return _digits(u.phone) or _digits(u.office)


def _display_user(u: User | None) -> str:
    if not u:
        return ""
    return u.full_name or u.username or ""


async def _find_user_by_number(db: AsyncSession, number: str) -> User | None:
    n = _digits(number)
    if not n:
        return None
    users = (await db.execute(select(User))).scalars().all()
    # Prefer active users, but still allow inactive records as a last-resort
    # phonebook for old call history.
    users.sort(key=lambda u: 0 if getattr(u, "is_active", True) else 1)
    for u in users:
        if _matches_number(n, u.phone) or _matches_number(n, u.office):
            return u
    return None


async def _call_out(db: AsyncSession, call: CallEvent) -> CallEventOut:
    out = CallEventOut.model_validate(call)
    callee = (await db.execute(select(User).where(User.id == call.user_id))).scalar_one_or_none() if call.user_id else None
    caller = await _find_user_by_number(db, call.caller_number)

    caller_display = _display_user(caller) or call.caller_name or call.caller_number or "Неизвестный номер"
    callee_display = _display_user(callee) or call.extension or "Неизвестный получатель"
    callee_number = _user_ext(callee) if callee else (call.extension or "")

    # Only one safe normalization: if the call has answered_at, it is accepted.
    # Do NOT convert ended to missed here: tabs must use explicit raw status.
    if call.answered_at and call.status != "answered":
        out.status = "answered"

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
    return [await _call_out(db, r) for r in rows]


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
    return [await _call_out(db, r) for r in rows]


@router.get("/unread-count")
async def missed_unread_count(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from sqlalchemy import func
    cnt = (await db.execute(
        select(func.count()).select_from(CallEvent).where(
            CallEvent.user_id == user.id,
            CallEvent.status == "missed",
            CallEvent.is_read == False,  # noqa: E712
        )
    )).scalar() or 0
    return {"count": cnt}


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
    from ..ami import ami_originate
    caller_id = user.full_name or user.username or from_ext
    return await ami_originate(from_ext, to_ext, caller_id=caller_id)


@router.get("/admin", response_model=list[CallEventOut])
async def admin_calls(
    limit: int = 200,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    rows = (await db.execute(select(CallEvent).order_by(desc(CallEvent.id)).limit(max(1, min(limit, 500))))).scalars().all()
    return [await _call_out(db, r) for r in rows]
