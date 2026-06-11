"""File storage: saving uploads, image thumbnails, avatar cropping."""
from __future__ import annotations

import io
import os
import uuid
from dataclasses import dataclass

from PIL import Image, ImageOps

from .config import settings


@dataclass
class SavedFile:
    url: str
    thumb_url: str
    name: str
    size: int
    width: int
    height: int
    kind: str  # "image" | "file"


def _ensure_dirs() -> None:
    for sub in ("files", "thumbs", "avatars"):
        os.makedirs(os.path.join(settings.UPLOAD_DIR, sub), exist_ok=True)


def image_extensions() -> set[str]:
    return {e.strip().lower() for e in settings.IMAGE_EXTENSIONS.split(",") if e.strip()}


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


def _safe_name(filename: str) -> str:
    base = os.path.basename(filename or "file")
    base = base.replace("\x00", "").strip() or "file"
    return base[:120]


def is_image(filename: str, content_type: str | None) -> bool:
    if _ext(filename) in image_extensions():
        return True
    return bool(content_type and content_type.startswith("image/"))


def save_upload(data: bytes, filename: str, content_type: str | None) -> SavedFile:
    """Persist an uploaded file. Images also get a thumbnail + dimensions."""
    _ensure_dirs()
    ext = _ext(filename)
    uid = uuid.uuid4().hex
    original_name = _safe_name(filename)

    if is_image(filename, content_type):
        try:
            img = Image.open(io.BytesIO(data))
            img = ImageOps.exif_transpose(img)  # honor camera rotation
            fmt = (img.format or "PNG").upper()
            if fmt not in ("JPEG", "PNG", "GIF", "WEBP", "BMP"):
                fmt = "PNG"
            save_ext = {"JPEG": "jpg"}.get(fmt, fmt.lower())
            w, h = img.size

            full_rel = f"files/{uid}.{save_ext}"
            full_path = os.path.join(settings.UPLOAD_DIR, full_rel)
            # Re-encode to strip metadata; keep animation for GIF.
            if fmt == "GIF":
                with open(full_path, "wb") as f:
                    f.write(data)
            else:
                rgb = img.convert("RGB") if fmt == "JPEG" else img
                rgb.save(full_path, fmt)

            # thumbnail (max 480px on the long side)
            thumb_rel = f"thumbs/{uid}.jpg"
            thumb_path = os.path.join(settings.UPLOAD_DIR, thumb_rel)
            thumb = img.convert("RGB")
            thumb.thumbnail((480, 480))
            thumb.save(thumb_path, "JPEG", quality=82)

            return SavedFile(
                url=f"/uploads/{full_rel}",
                thumb_url=f"/uploads/{thumb_rel}",
                name=original_name,
                size=len(data),
                width=w,
                height=h,
                kind="image",
            )
        except Exception:
            # Not a valid image -> fall through and store as a generic file.
            pass

    # generic file
    safe_ext = ext if ext and len(ext) <= 12 else "bin"
    full_rel = f"files/{uid}.{safe_ext}"
    full_path = os.path.join(settings.UPLOAD_DIR, full_rel)
    with open(full_path, "wb") as f:
        f.write(data)
    return SavedFile(
        url=f"/uploads/{full_rel}",
        thumb_url="",
        name=original_name,
        size=len(data),
        width=0,
        height=0,
        kind="file",
    )


def save_avatar(data: bytes, filename: str) -> str:
    """Crop to a centered square and store as a 256px avatar. Returns URL."""
    _ensure_dirs()
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    # center-crop to square
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img = img.resize((256, 256))
    uid = uuid.uuid4().hex
    rel = f"avatars/{uid}.jpg"
    img.save(os.path.join(settings.UPLOAD_DIR, rel), "JPEG", quality=85)
    return f"/uploads/{rel}"


def human_size(num: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if num < 1024:
            return f"{num:.0f} {unit}" if unit == "Б" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} ТБ"
