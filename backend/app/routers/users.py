"""User routes: search, profile, update."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import Group, User
from ..permissions import get_effective_permissions
from ..schemas import MyPermissionsOut, UserOut, UserUpdate
from ..security import get_current_user
from ..ws_manager import manager

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/me/permissions", response_model=MyPermissionsOut)
async def my_permissions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The current user's resolved permission flags (so the UI can hide
    actions the user's group is not allowed to perform)."""
    perms = await get_effective_permissions(db, user)
    group_name = ""
    if user.group_id:
        g = (await db.execute(select(Group).where(Group.id == user.group_id))).scalar_one_or_none()
        if g:
            group_name = g.name
    return MyPermissionsOut(
        **perms,
        group_id=user.group_id,
        group_name=group_name,
        is_admin=(user.role == "admin"),
    )


@router.get("", response_model=list[UserOut])
async def list_users(
    q: str = "",
    limit: int = 50,
    include_self: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Clamp the limit: default 50 (sidebar/pickers), up to 1000 for the
    # contact book which needs the whole company directory.
    limit = max(1, min(limit, 1000))
    stmt = select(User).where(User.is_active == True)  # noqa: E712
    if not include_self:
        stmt = stmt.where(User.id != user.id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                User.username.ilike(like),
                User.full_name.ilike(like),
                User.email.ilike(like),
            )
        )
    stmt = stmt.order_by(User.full_name, User.username).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    result = []
    for u in rows:
        out = UserOut.model_validate(u)
        out.is_online = manager.is_online(u.id)
        result.append(out)
    return result


@router.get("/org/tree")
async def org_tree(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Company org structure for the contact book. Uses app Groups as
    departments (usually imported/synced from AD groups) and users as members.
    """
    groups = (await db.execute(select(Group).order_by(Group.is_default, Group.name))).scalars().all()
    users = (await db.execute(select(User).where(User.is_active == True).order_by(User.full_name, User.username))).scalars().all()  # noqa: E712
    buckets: dict[int | None, list[User]] = {}
    for u in users:
        buckets.setdefault(u.group_id, []).append(u)

    def member(u: User):
        return {
            "id": u.id,
            "username": u.username,
            "full_name": u.full_name,
            "email": u.email,
            "title": u.title or "",
            "phone": u.phone or "",
            "office": u.office or "",
            "avatar_color": u.avatar_color,
            "avatar_url": u.avatar_url or "",
            "is_online": manager.is_online(u.id),
            "role": u.role,
            "auth_source": u.auth_source,
        }

    nodes = []
    for g in groups:
        # default group covers NULL users and users explicitly pointing to it
        members = (buckets.get(g.id, []) + buckets.get(None, [])) if g.is_default else buckets.get(g.id, [])
        nodes.append({
            "id": g.id,
            "name": g.name,
            "raw_name": g.name,
            "description": g.description or "",
            "is_default": g.is_default,
            "ad_group_dn": g.ad_group_dn or "",
            "member_count": len(members),
            "online_count": sum(1 for u in members if manager.is_online(u.id)),
            "members": [member(u) for u in members],
        })

    # If the default group doesn't exist yet, still show users without group.
    if not any(n["is_default"] for n in nodes) and buckets.get(None):
        members = buckets.get(None, [])
        nodes.append({
            "id": None,
            "name": "Пользователи без группы",
            "raw_name": "Пользователи без группы",
            "description": "Пользователи без назначенного отдела",
            "is_default": True,
            "ad_group_dn": "",
            "member_count": len(members),
            "online_count": sum(1 for u in members if manager.is_online(u.id)),
            "members": [member(u) for u in members],
        })

    return {"departments": nodes}


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not u:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    out = UserOut.model_validate(u)
    out.is_online = manager.is_online(u.id)
    return out


@router.patch("/me", response_model=UserOut)
async def update_me(
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.bio is not None:
        user.bio = data.bio
    if data.avatar_color is not None:
        user.avatar_color = data.avatar_color
    # Directory contact fields (editable for local accounts; AD overwrites on login)
    if data.title is not None:
        user.title = data.title
    if data.phone is not None:
        user.phone = data.phone
    if data.office is not None:
        user.office = data.office
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)
