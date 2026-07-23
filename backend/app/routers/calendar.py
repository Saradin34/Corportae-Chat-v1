"""Personal/shared calendars and notes."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Calendar, CalendarMember, CalendarNote, User
from ..schemas import CalendarCreate, CalendarNoteCreate, CalendarNoteOut, CalendarNoteUpdate, CalendarOut, CalendarUpdate
from ..security import get_current_user

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


async def _ensure_personal_calendar(db: AsyncSession, user: User) -> Calendar:
    cal = (await db.execute(select(Calendar).where(Calendar.owner_id == user.id, Calendar.is_shared == False))).scalar_one_or_none()  # noqa: E712
    if cal is None:
        cal = Calendar(owner_id=user.id, name="Мой календарь", color="#3390ec", is_shared=False)
        db.add(cal)
        await db.flush()
        db.add(CalendarMember(calendar_id=cal.id, user_id=user.id, can_edit=True))
        await db.commit()
        await db.refresh(cal)
    return cal


async def _accessible_calendar_ids(db: AsyncSession, user: User) -> set[int]:
    await _ensure_personal_calendar(db, user)
    ids = set((await db.execute(select(CalendarMember.calendar_id).where(CalendarMember.user_id == user.id))).scalars().all())
    owned = set((await db.execute(select(Calendar.id).where(Calendar.owner_id == user.id))).scalars().all())
    return ids | owned


async def _can_edit_calendar(db: AsyncSession, user: User, calendar_id: int) -> bool:
    cal = (await db.execute(select(Calendar).where(Calendar.id == calendar_id))).scalar_one_or_none()
    if not cal:
        return False
    if cal.owner_id == user.id:
        return True
    cm = (await db.execute(select(CalendarMember).where(CalendarMember.calendar_id == calendar_id, CalendarMember.user_id == user.id))).scalar_one_or_none()
    return bool(cm and cm.can_edit)


async def _calendar_out(db: AsyncSession, cal: Calendar, user: User) -> CalendarOut:
    member_ids = list((await db.execute(select(CalendarMember.user_id).where(CalendarMember.calendar_id == cal.id))).scalars().all())
    return CalendarOut(
        id=cal.id, name=cal.name, color=cal.color, owner_id=cal.owner_id,
        is_shared=cal.is_shared, member_ids=member_ids,
        can_edit=await _can_edit_calendar(db, user, cal.id),
    )


async def _note_out(db: AsyncSession, note: CalendarNote) -> CalendarNoteOut:
    out = CalendarNoteOut.model_validate(note)
    if note.calendar_id:
        cal = (await db.execute(select(Calendar).where(Calendar.id == note.calendar_id))).scalar_one_or_none()
        if cal:
            out.calendar_name = cal.name
            out.calendar_color = cal.color
    return out


@router.get("/calendars", response_model=list[CalendarOut])
async def list_calendars(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    ids = await _accessible_calendar_ids(db, user)
    rows = (await db.execute(select(Calendar).where(Calendar.id.in_(ids)).order_by(Calendar.is_shared, Calendar.name))).scalars().all()
    return [await _calendar_out(db, c, user) for c in rows]


@router.post("/calendars", response_model=CalendarOut)
async def create_calendar(data: CalendarCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cal = Calendar(owner_id=user.id, name=data.name.strip(), color=data.color or "#3390ec", is_shared=bool(data.member_ids))
    db.add(cal)
    await db.flush()
    members = set(data.member_ids) | {user.id}
    for uid in members:
        exists = (await db.execute(select(User.id).where(User.id == uid, User.is_active == True))).scalar_one_or_none()  # noqa: E712
        if exists:
            db.add(CalendarMember(calendar_id=cal.id, user_id=uid, can_edit=True))
    await db.commit()
    await db.refresh(cal)
    return await _calendar_out(db, cal, user)


@router.patch("/calendars/{calendar_id}", response_model=CalendarOut)
async def update_calendar(calendar_id: int, data: CalendarUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cal = (await db.execute(select(Calendar).where(Calendar.id == calendar_id))).scalar_one_or_none()
    if not cal or cal.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Только владелец календаря может менять настройки")
    if data.name is not None:
        cal.name = data.name.strip()
    if data.color is not None:
        cal.color = data.color
    if data.member_ids is not None:
        rows = (await db.execute(select(CalendarMember).where(CalendarMember.calendar_id == cal.id))).scalars().all()
        for r in rows:
            await db.delete(r)
        members = set(data.member_ids) | {user.id}
        cal.is_shared = len(members) > 1
        for uid in members:
            exists = (await db.execute(select(User.id).where(User.id == uid, User.is_active == True))).scalar_one_or_none()  # noqa: E712
            if exists:
                db.add(CalendarMember(calendar_id=cal.id, user_id=uid, can_edit=True))
    await db.commit()
    await db.refresh(cal)
    return await _calendar_out(db, cal, user)


@router.delete("/calendars/{calendar_id}")
async def delete_calendar(calendar_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cal = (await db.execute(select(Calendar).where(Calendar.id == calendar_id))).scalar_one_or_none()
    if not cal or cal.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Только владелец календаря может удалить календарь")
    await db.delete(cal)
    await db.commit()
    return {"ok": True}


@router.get("", response_model=list[CalendarNoteOut])
async def list_notes(start: datetime | None = None, end: datetime | None = None, calendar_id: int | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    ids = await _accessible_calendar_ids(db, user)
    if start is None:
        now = datetime.now(timezone.utc)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if end is None:
        end = start + timedelta(days=45)
    if calendar_id:
        if calendar_id not in ids:
            raise HTTPException(status_code=403, detail="Нет доступа к календарю")
        ids = {calendar_id}
    stmt = select(CalendarNote).where(CalendarNote.calendar_id.in_(ids), CalendarNote.starts_at >= start, CalendarNote.starts_at <= end).order_by(CalendarNote.starts_at, CalendarNote.id)
    rows = (await db.execute(stmt)).scalars().all()
    return [await _note_out(db, r) for r in rows]


@router.post("", response_model=CalendarNoteOut)
async def create_note(data: CalendarNoteCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    cal = (await _ensure_personal_calendar(db, user)) if data.calendar_id is None else (await db.execute(select(Calendar).where(Calendar.id == data.calendar_id))).scalar_one_or_none()
    if not cal or not await _can_edit_calendar(db, user, cal.id):
        raise HTTPException(status_code=403, detail="Нет доступа к календарю")
    note = CalendarNote(user_id=user.id, calendar_id=cal.id, title=data.title.strip(), text=data.text.strip(), starts_at=data.starts_at, color=data.color or cal.color)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return await _note_out(db, note)


@router.patch("/{note_id}", response_model=CalendarNoteOut)
async def update_note(note_id: int, data: CalendarNoteUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    note = (await db.execute(select(CalendarNote).where(CalendarNote.id == note_id))).scalar_one_or_none()
    if not note or not note.calendar_id or not await _can_edit_calendar(db, user, note.calendar_id):
        raise HTTPException(status_code=404, detail="Заметка не найдена")
    patch = data.model_dump(exclude_unset=True)
    if "calendar_id" in patch and patch["calendar_id"] is not None and patch["calendar_id"] != note.calendar_id:
        if not await _can_edit_calendar(db, user, patch["calendar_id"]):
            raise HTTPException(status_code=403, detail="Нет доступа к календарю")
    for k, v in patch.items():
        if v is not None:
            setattr(note, k, v.strip() if isinstance(v, str) else v)
    await db.commit()
    await db.refresh(note)
    return await _note_out(db, note)


@router.delete("/{note_id}")
async def delete_note(note_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    note = (await db.execute(select(CalendarNote).where(CalendarNote.id == note_id))).scalar_one_or_none()
    if note and note.calendar_id and await _can_edit_calendar(db, user, note.calendar_id):
        await db.delete(note)
        await db.commit()
    return {"ok": True}
