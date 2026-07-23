"""Support tickets: users write, admins see and reply."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import SupportMessage, SupportTemplate, SupportTicket, User
from ..schemas import (
    SupportAssignRequest,
    SupportCreateRequest,
    SupportMessageOut,
    SupportReplyRequest,
    SupportStatusRequest,
    SupportTemplateCreate,
    SupportTemplateOut,
    SupportTemplateUpdate,
    SupportTicketOut,
)
from ..security import get_current_admin, get_current_user
from ..ws_manager import manager

router = APIRouter(prefix="/api/support", tags=["support"])

SUPPORT_CATEGORIES = {"it", "telephony", "access", "hr", "office", "security", "other", "general"}
SUPPORT_PRIORITIES = {"low", "normal", "high", "critical"}
SUPPORT_STATUSES = {"open", "in_progress", "waiting_user", "pending", "resolved", "closed"}


def _clean_category(value: str) -> str:
    # Categories are intentionally collapsed to one common support queue.
    # Keep the field for DB compatibility, but do not expose multiple categories.
    return "general"


def _clean_priority(value: str) -> str:
    v = (value or "normal").strip().lower()
    return v if v in SUPPORT_PRIORITIES else "normal"


def _user_label(u: User | None) -> str:
    return (u.full_name or u.username) if u else "Пользователь"


async def _ticket_out(db: AsyncSession, t: SupportTicket, viewer: User) -> SupportTicketOut:
    owner = (await db.execute(select(User).where(User.id == t.user_id))).scalar_one_or_none()
    assigned = (await db.execute(select(User).where(User.id == t.assigned_admin_id))).scalar_one_or_none() if t.assigned_admin_id else None
    last = (await db.execute(select(SupportMessage).where(SupportMessage.ticket_id == t.id).order_by(desc(SupportMessage.id)).limit(1))).scalar_one_or_none()
    unread_stmt = select(func.count()).select_from(SupportMessage).where(SupportMessage.ticket_id == t.id)
    if viewer.role == "admin":
        unread_stmt = unread_stmt.where(SupportMessage.sender_role != "admin", SupportMessage.is_read_by_admin == False)  # noqa: E712
    else:
        unread_stmt = unread_stmt.where(SupportMessage.sender_role == "admin", SupportMessage.is_read_by_user == False)  # noqa: E712
    unread = (await db.execute(unread_stmt)).scalar() or 0
    return SupportTicketOut(
        id=t.id,
        user_id=t.user_id,
        user_name=_user_label(owner),
        subject=t.subject,
        category=getattr(t, "category", "general") or "general",
        status=t.status,
        priority=t.priority,
        assigned_admin_id=t.assigned_admin_id,
        assigned_admin_name=_user_label(assigned) if assigned else "",
        unread=unread,
        last_message=(last.text[:160] if last else ""),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


async def _message_out(db: AsyncSession, m: SupportMessage) -> SupportMessageOut:
    sender = (await db.execute(select(User).where(User.id == m.sender_id))).scalar_one_or_none() if m.sender_id else None
    return SupportMessageOut(
        id=m.id,
        ticket_id=m.ticket_id,
        sender_id=m.sender_id,
        sender_name=_user_label(sender),
        sender_role=m.sender_role,
        text=m.text,
        is_read_by_user=m.is_read_by_user,
        is_read_by_admin=m.is_read_by_admin,
        created_at=m.created_at,
    )


async def _admin_ids(db: AsyncSession) -> list[int]:
    return list((await db.execute(select(User.id).where(User.role == "admin", User.is_active == True))).scalars().all())  # noqa: E712


@router.get("/meta")
async def support_meta(_: User = Depends(get_current_user)):
    return {
        "categories": ["general"],
        "priorities": ["low", "normal", "high", "critical"],
        "statuses": ["open", "in_progress", "waiting_user", "pending", "resolved", "closed"],
    }


@router.get("/templates", response_model=list[SupportTemplateOut])
async def list_templates(category: str = "", db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    stmt = select(SupportTemplate).where(SupportTemplate.is_active == True)  # noqa: E712
    if category:
        stmt = stmt.where(SupportTemplate.category == _clean_category(category))
    rows = (await db.execute(stmt.order_by(SupportTemplate.category, SupportTemplate.title))).scalars().all()
    return [SupportTemplateOut.model_validate(r) for r in rows]


@router.post("/templates", response_model=SupportTemplateOut)
async def create_template(data: SupportTemplateCreate, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    t = SupportTemplate(title=data.title.strip(), text=data.text.strip(), category=_clean_category(data.category), created_by=admin.id)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return SupportTemplateOut.model_validate(t)


@router.patch("/templates/{template_id}", response_model=SupportTemplateOut)
async def update_template(template_id: int, data: SupportTemplateUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    t = (await db.execute(select(SupportTemplate).where(SupportTemplate.id == template_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    patch = data.model_dump(exclude_unset=True)
    if "title" in patch and patch["title"] is not None:
        t.title = patch["title"].strip()
    if "text" in patch and patch["text"] is not None:
        t.text = patch["text"].strip()
    if "category" in patch and patch["category"] is not None:
        t.category = _clean_category(patch["category"])
    if "is_active" in patch and patch["is_active"] is not None:
        t.is_active = bool(patch["is_active"])
    await db.commit()
    await db.refresh(t)
    return SupportTemplateOut.model_validate(t)


@router.delete("/templates/{template_id}")
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    t = (await db.execute(select(SupportTemplate).where(SupportTemplate.id == template_id))).scalar_one_or_none()
    if t:
        t.is_active = False
        await db.commit()
    return {"ok": True}


@router.get("", response_model=list[SupportTicketOut])
async def list_my_tickets(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (await db.execute(select(SupportTicket).where(SupportTicket.user_id == user.id).order_by(desc(SupportTicket.updated_at), desc(SupportTicket.id)))).scalars().all()
    return [await _ticket_out(db, t, user) for t in rows]


@router.post("", response_model=SupportTicketOut)
async def create_ticket(data: SupportCreateRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    t = SupportTicket(user_id=user.id, subject=data.subject.strip(), category=_clean_category(data.category), priority=_clean_priority(data.priority), status="open")
    db.add(t)
    await db.flush()
    msg = SupportMessage(ticket_id=t.id, sender_id=user.id, sender_role="user", text=data.text.strip(), is_read_by_user=True, is_read_by_admin=False)
    db.add(msg)
    await db.commit()
    await db.refresh(t)
    await manager.send_to_users(await _admin_ids(db), {"type": "support_updated", "ticket_id": t.id})
    return await _ticket_out(db, t, user)


@router.get("/{ticket_id}/messages", response_model=list[SupportMessageOut])
async def ticket_messages(ticket_id: int, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    t = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if not t or (t.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    rows = (await db.execute(select(SupportMessage).where(SupportMessage.ticket_id == ticket_id).order_by(SupportMessage.id))).scalars().all()
    for m in rows:
        if user.role == "admin" and m.sender_role != "admin":
            m.is_read_by_admin = True
        if user.role != "admin" and m.sender_role == "admin":
            m.is_read_by_user = True
    await db.commit()
    return [await _message_out(db, m) for m in rows]


@router.post("/{ticket_id}/reply", response_model=SupportMessageOut)
async def reply_ticket(ticket_id: int, data: SupportReplyRequest, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    t = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if not t or (t.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    role = "admin" if user.role == "admin" else "user"
    msg = SupportMessage(ticket_id=t.id, sender_id=user.id, sender_role=role, text=data.text.strip(), is_read_by_user=(role == "user"), is_read_by_admin=(role == "admin"))
    if role == "admin":
        t.status = "waiting_user"
        if not t.assigned_admin_id:
            t.assigned_admin_id = user.id
    else:
        t.status = "open"
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    recipients = [t.user_id] if role == "admin" else await _admin_ids(db)
    await manager.send_to_users(recipients, {"type": "support_updated", "ticket_id": t.id})
    return await _message_out(db, msg)


@router.post("/{ticket_id}/status", response_model=SupportTicketOut)
async def set_ticket_status(ticket_id: int, data: SupportStatusRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    t = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if data.status not in SUPPORT_STATUSES:
        raise HTTPException(status_code=400, detail="Недопустимый статус")
    t.status = data.status
    if not t.assigned_admin_id and data.status in ("in_progress", "waiting_user", "resolved", "closed"):
        t.assigned_admin_id = admin.id
    await db.commit()
    await db.refresh(t)
    await manager.send_to_user(t.user_id, {"type": "support_updated", "ticket_id": t.id})
    return await _ticket_out(db, t, admin)


@router.post("/{ticket_id}/assign", response_model=SupportTicketOut)
async def assign_ticket(ticket_id: int, data: SupportAssignRequest, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    t = (await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if data.admin_id is not None:
        assignee = (await db.execute(select(User).where(User.id == data.admin_id, User.role == "admin", User.is_active == True))).scalar_one_or_none()  # noqa: E712
        if not assignee:
            raise HTTPException(status_code=404, detail="Администратор не найден")
        t.assigned_admin_id = assignee.id
    else:
        t.assigned_admin_id = admin.id
    await db.commit()
    await db.refresh(t)
    return await _ticket_out(db, t, admin)


@router.get("/admin/list", response_model=list[SupportTicketOut])
async def admin_tickets(status: str = "", category: str = "", db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_admin)):
    stmt = select(SupportTicket)
    if status:
        stmt = stmt.where(SupportTicket.status == status)
    # Category filtering is disabled: all tickets are in the common queue.
    rows = (await db.execute(stmt.order_by(desc(SupportTicket.updated_at), desc(SupportTicket.id)))).scalars().all()
    return [await _ticket_out(db, t, admin) for t in rows]
