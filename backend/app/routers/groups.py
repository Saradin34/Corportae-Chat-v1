"""Group (department) management: CRUD + permission flags + member assignment.

Groups carry a set of permission flags applied to all their members. One group
is the implicit default ("Пользователи без группы") used by users without a
group; it cannot be renamed or deleted.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import GROUP_PERMISSIONS, AuditLog, Chat, ChatMember, Group, User
from ..schemas import (
    AdGroupOut,
    AssignGroupRequest,
    CreateGroupRequest,
    GroupOut,
    ImportAdGroupRequest,
    ImportAdGroupResult,
    UpdateGroupRequest,
)
from ..security import get_current_admin
from ..utils import random_color
from ..ws_manager import manager

router = APIRouter(prefix="/api/admin/groups", tags=["groups"])

DEFAULT_GROUP_NAME = "Пользователи без группы"


async def _log(db: AsyncSession, actor: User, action: str, details: str = "") -> None:
    db.add(AuditLog(actor_id=actor.id, actor_name=actor.username, action=action, details=details))
    await db.commit()


async def ensure_default_group(db: AsyncSession) -> Group:
    """Create the implicit default group if it doesn't exist yet."""
    g = (await db.execute(select(Group).where(Group.is_default == True))).scalar_one_or_none()  # noqa: E712
    if g is None:
        g = Group(name=DEFAULT_GROUP_NAME, is_default=True, description="Права по умолчанию для пользователей без группы")
        db.add(g)
        await db.commit()
        await db.refresh(g)
    return g


async def _member_count(db: AsyncSession, group: Group) -> int:
    if group.is_default:
        # default group covers users with no group_id OR pointing at it
        return (await db.execute(
            select(func.count()).select_from(User).where((User.group_id == None) | (User.group_id == group.id))  # noqa: E711
        )).scalar() or 0
    return (await db.execute(
        select(func.count()).select_from(User).where(User.group_id == group.id)
    )).scalar() or 0


async def _serialize(db: AsyncSession, g: Group) -> GroupOut:
    out = GroupOut.model_validate(g)
    out.member_count = await _member_count(db, g)
    return out


async def _ensure_group_chat(db: AsyncSession, group: Group, created_by: int) -> Chat:
    """Create a group chat linked to an organisational group if it doesn't exist yet."""
    chat = (await db.execute(select(Chat).where(Chat.group_id == group.id))).scalar_one_or_none()
    if chat is None:
        chat = Chat(
            type="group",
            name=group.name,
            description=group.description or "",
            group_id=group.id,
            avatar_color=random_color(),
            created_by=created_by,
        )
        db.add(chat)
        await db.flush()
        await db.refresh(chat)
    return chat


async def _sync_group_chat_members(db: AsyncSession, group: Group) -> None:
    """Synchronise group members into the linked group chat (add/remove)."""
    chat = (await db.execute(select(Chat).where(Chat.group_id == group.id))).scalar_one_or_none()
    if not chat:
        return

    # Expected members: users belonging to this group
    if group.is_default:
        expected = set((await db.execute(
            select(User.id).where((User.group_id == None) | (User.group_id == group.id))  # noqa: E711
        )).scalars().all())
    else:
        expected = set((await db.execute(
            select(User.id).where(User.group_id == group.id)
        )).scalars().all())

    current = set((await db.execute(
        select(ChatMember.user_id).where(ChatMember.chat_id == chat.id)
    )).scalars().all())

    to_add = expected - current
    to_remove = current - expected

    for uid in to_add:
        db.add(ChatMember(chat_id=chat.id, user_id=uid, is_admin=False))

    if to_remove:
        for uid in to_remove:
            cm = (await db.execute(
                select(ChatMember).where(ChatMember.chat_id == chat.id, ChatMember.user_id == uid)
            )).scalar_one_or_none()
            if cm:
                await db.delete(cm)

    if to_add or to_remove:
        await db.flush()
        # Notify affected users so the chat appears/disappears in their sidebar
        if to_add:
            await manager.send_to_users(list(to_add), {"type": "chat_created", "chat_id": chat.id})
        all_affected = current | to_add | to_remove
        await manager.send_to_users(list(all_affected), {"type": "chat_updated", "chat_id": chat.id})


@router.get("", response_model=list[GroupOut])
async def list_groups(db: AsyncSession = Depends(get_db), _: User = Depends(get_current_admin)):
    await ensure_default_group(db)
    # default group last, the rest alphabetically
    groups = (await db.execute(select(Group).order_by(Group.is_default, Group.name))).scalars().all()
    return [await _serialize(db, g) for g in groups]


@router.post("", response_model=GroupOut)
async def create_group(
    data: CreateGroupRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название не может быть пустым")
    existing = (await db.execute(select(Group).where(func.lower(Group.name) == name.lower()))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Группа с таким названием уже существует")
    g = Group(name=name, description=data.description)
    db.add(g)
    await db.commit()
    await db.refresh(g)
    await _log(db, admin, "create_group", f"group={name}")
    # Create the linked group chat (empty until users are assigned)
    await _ensure_group_chat(db, g, admin.id)
    await db.commit()
    return await _serialize(db, g)


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: int,
    data: UpdateGroupRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    g = (await db.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Группа не найдена")

    old_name = g.name
    if data.name is not None and not g.is_default:
        new_name = data.name.strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="Название не может быть пустым")
        clash = (await db.execute(
            select(Group).where(func.lower(Group.name) == new_name.lower(), Group.id != group_id)
        )).scalar_one_or_none()
        if clash:
            raise HTTPException(status_code=400, detail="Группа с таким названием уже существует")
        g.name = new_name
    if data.description is not None:
        g.description = data.description

    # permission flags
    for perm in GROUP_PERMISSIONS:
        val = getattr(data, perm, None)
        if val is not None:
            setattr(g, perm, bool(val))

    await db.commit()
    await db.refresh(g)
    # Keep the linked chat name in sync
    if old_name != g.name:
        chat = (await db.execute(select(Chat).where(Chat.group_id == g.id))).scalar_one_or_none()
        if chat:
            chat.name = g.name
            await db.commit()
    await _log(db, admin, "update_group", f"group={g.name}")
    return await _serialize(db, g)


@router.delete("/{group_id}")
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    g = (await db.execute(select(Group).where(Group.id == group_id))).scalar_one_or_none()
    if not g:
        raise HTTPException(status_code=404, detail="Группа не найдена")
    if g.is_default:
        raise HTTPException(status_code=400, detail="Нельзя удалить группу по умолчанию")
    # detach members -> they fall back to the default group
    members = (await db.execute(select(User).where(User.group_id == group_id))).scalars().all()
    for u in members:
        u.group_id = None
    # Delete linked group chat if it exists
    chat = (await db.execute(select(Chat).where(Chat.group_id == g.id))).scalar_one_or_none()
    chat_member_ids = []
    if chat:
        chat_member_ids = list((await db.execute(
            select(ChatMember.user_id).where(ChatMember.chat_id == chat.id)
        )).scalars().all())
        await db.delete(chat)
    name = g.name
    await db.delete(g)
    await db.commit()
    if chat_member_ids:
        await manager.send_to_users(chat_member_ids, {"type": "chat_deleted", "chat_id": chat.id})
    await _log(db, admin, "delete_group", f"group={name} freed={len(members)}")
    return {"ok": True, "freed_members": len(members)}


@router.post("/assign")
async def assign_users(
    data: AssignGroupRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Assign a set of users to a group (or remove from group when group_id is
    null / the default group)."""
    target_group = None
    if data.group_id is not None:
        target_group = (await db.execute(select(Group).where(Group.id == data.group_id))).scalar_one_or_none()
        if target_group is None:
            raise HTTPException(status_code=404, detail="Группа не найдена")

    new_gid = None if (target_group is None or target_group.is_default) else target_group.id
    users = (await db.execute(select(User).where(User.id.in_(data.user_ids)))).scalars().all()
    # Track which groups were affected so we can sync their linked chats
    affected_groups = {u.group_id for u in users if u.group_id is not None}
    for u in users:
        u.group_id = new_gid
    await db.commit()

    # Sync linked group chats for affected groups (old + new)
    if new_gid is not None:
        affected_groups.add(new_gid)
    for gid in affected_groups:
        grp = (await db.execute(select(Group).where(Group.id == gid))).scalar_one_or_none()
        if grp:
            await _sync_group_chat_members(db, grp)
            await db.commit()

    gname = target_group.name if target_group else DEFAULT_GROUP_NAME
    await _log(db, admin, "assign_group", f"group={gname} users={len(users)}")
    return {"ok": True, "updated": len(users)}


# ============================================================
#  Active Directory: search groups + import group with members
# ============================================================
async def _upsert_ad_user(db: AsyncSession, ad_user) -> tuple[User, bool]:
    """Create or update a local User from an AD entry. Returns (user, created)."""
    from sqlalchemy import or_

    user = (
        await db.execute(
            select(User).where(or_(User.username == ad_user.username, User.email == ad_user.email))
        )
    ).scalar_one_or_none()
    created = False
    if user is None:
        user = User(
            username=ad_user.username,
            email=ad_user.email,
            password_hash="!ldap",
            full_name=ad_user.full_name or ad_user.username,
            title=getattr(ad_user, "title", "") or "",
            phone=getattr(ad_user, "phone", "") or "",
            office=getattr(ad_user, "office", "") or "",
            avatar_color=random_color(),
            role="admin" if getattr(ad_user, "is_admin", False) else "user",
            auth_source="ldap",
        )
        db.add(user)
        await db.flush()
        created = True
    else:
        # keep key profile fields fresh
        if user.auth_source != "ldap":
            user.auth_source = "ldap"
        if ad_user.full_name:
            user.full_name = ad_user.full_name
        user.title = getattr(ad_user, "title", "") or user.title
        user.phone = getattr(ad_user, "phone", "") or user.phone
        user.office = getattr(ad_user, "office", "") or user.office
    return user, created


@router.get("/ad/search", response_model=list[AdGroupOut])
async def ad_search_groups(
    q: str = "",
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_admin),
):
    """Search Active Directory for groups by name (substring)."""
    from .. import ldap_auth

    try:
        found = ldap_auth.search_groups(q, limit=25)
    except ldap_auth.LdapError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Ошибка обращения к AD: {e}")

    # mark which AD groups already have a linked app group
    dns = [g.dn for g in found]
    linked_dns = set()
    if dns:
        linked_dns = set(
            (await db.execute(select(Group.ad_group_dn).where(Group.ad_group_dn.in_(dns)))).scalars().all()
        )
    return [
        AdGroupOut(dn=g.dn, name=g.name, description=g.description,
                   member_count=g.member_count, linked=(g.dn in linked_dns))
        for g in found
    ]


@router.post("/ad/import", response_model=ImportAdGroupResult)
async def ad_import_group(
    data: ImportAdGroupRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Import an AD group: create (or reuse) an app group linked to the AD DN,
    then create/locate every AD member as a user and assign them to the group."""
    from .. import ldap_auth

    dn = data.dn.strip()
    group_name = (data.name or ldap_auth.group_cn(dn) or "AD Group").strip()

    # fetch AD members first (fail early if AD is unreachable)
    try:
        ad_members = ldap_auth.group_members(dn)
    except ldap_auth.LdapError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Ошибка обращения к AD: {e}")

    # find or create the app group (linked by ad_group_dn, else by name)
    group = (await db.execute(select(Group).where(Group.ad_group_dn == dn))).scalar_one_or_none()
    if group is None:
        clash = (await db.execute(
            select(Group).where(func.lower(Group.name) == group_name.lower())
        )).scalar_one_or_none()
        if clash is not None:
            if clash.ad_group_dn and clash.ad_group_dn != dn:
                raise HTTPException(status_code=400, detail="Группа с таким названием уже связана с другой группой AD")
            clash.ad_group_dn = dn
            group = clash
        else:
            group = Group(name=group_name, description="Импортирована из Active Directory", ad_group_dn=dn)
            db.add(group)
            await db.flush()

    created_users = 0
    added_members = 0
    for ad_user in ad_members:
        user, created = await _upsert_ad_user(db, ad_user)
        if created:
            created_users += 1
        if user.group_id != group.id:
            user.group_id = group.id
            added_members += 1

    await db.commit()
    await db.refresh(group)
    # Create / update the linked group chat and populate it with imported members
    await _ensure_group_chat(db, group, admin.id)
    await _sync_group_chat_members(db, group)
    await db.commit()
    await _log(db, admin, "import_ad_group",
               f"group={group.name} dn={dn} members={len(ad_members)} created={created_users}")
    return ImportAdGroupResult(
        group=await _serialize(db, group),
        created_users=created_users,
        added_members=added_members,
        total_ad_members=len(ad_members),
    )
