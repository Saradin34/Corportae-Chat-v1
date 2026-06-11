"""Upload routes: chat attachments + avatars (user & group)."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import Chat, ChatMember, User
from ..permissions import get_effective_permissions
from ..schemas import AvatarResult, UploadResult
from ..security import get_current_user
from ..storage import save_avatar, save_upload
from ..ws_manager import manager

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


async def _read_limited(file: UploadFile, max_mb: int) -> bytes:
    max_bytes = max_mb * 1024 * 1024
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Файл превышает {max_mb} МБ")
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    return data


@router.post("/file", response_model=UploadResult)
async def upload_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload an attachment (image or document). Returns metadata the client
    then sends with POST /chats/{id}/messages to create the message."""
    # group permission: must be allowed to send files OR images
    perms = await get_effective_permissions(db, user)
    if not (perms["can_send_files"] or perms["can_send_images"]):
        raise HTTPException(status_code=403, detail="Ваша группа не может отправлять вложения")
    # runtime-editable size limit (falls back to env default)
    from .. import app_settings
    max_mb = await app_settings.get(db, "max_upload_mb") or settings.MAX_UPLOAD_MB
    data = await _read_limited(file, int(max_mb))
    saved = save_upload(data, file.filename or "file", file.content_type)
    return UploadResult(
        kind=saved.kind,
        url=saved.url,
        thumb=saved.thumb_url,
        name=saved.name,
        size=saved.size,
        width=saved.width,
        height=saved.height,
    )


@router.post("/avatar", response_model=AvatarResult)
async def upload_avatar(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload the current user's avatar (square-cropped circle)."""
    data = await _read_limited(file, settings.MAX_AVATAR_MB)
    try:
        url = save_avatar(data, file.filename or "avatar.jpg")
    except Exception:
        raise HTTPException(status_code=400, detail="Не удалось обработать изображение")
    user.avatar_url = url
    await db.commit()
    await db.refresh(user)
    return AvatarResult(avatar_url=url)


@router.post("/chat/{chat_id}/avatar", response_model=AvatarResult)
async def upload_chat_avatar(
    chat_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a group's avatar (group admins only)."""
    cm = (
        await db.execute(
            select(ChatMember).where(ChatMember.chat_id == chat_id, ChatMember.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not cm:
        raise HTTPException(status_code=403, detail="Нет доступа к этому чату")
    chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    if chat.type == "private":
        raise HTTPException(status_code=400, detail="Нельзя менять аватар личного чата")
    if not cm.is_admin and user.role != "admin":
        raise HTTPException(status_code=403, detail="Только администратор группы может менять аватар")

    data = await _read_limited(file, settings.MAX_AVATAR_MB)
    try:
        url = save_avatar(data, file.filename or "avatar.jpg")
    except Exception:
        raise HTTPException(status_code=400, detail="Не удалось обработать изображение")
    chat.avatar_url = url
    await db.commit()

    members = list((await db.execute(select(ChatMember.user_id).where(ChatMember.chat_id == chat_id))).scalars().all())
    await manager.send_to_users(members, {"type": "chat_updated", "chat_id": chat_id})
    return AvatarResult(avatar_url=url)
