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
    """Persist an uploaded file without modifying the original bytes.

    Important: uploaded images are now stored *as-is*. Older builds re-encoded
    JPEG/PNG images through Pillow which could make a file much larger (for
    example JPG -> PNG-like recompression, metadata/color profile changes, etc.).
    We only decode the image to read dimensions and create a separate thumbnail.
    Downloading the attachment always returns the original uploaded bytes.
    """
    _ensure_dirs()
    ext = _ext(filename)
    uid = uuid.uuid4().hex
    original_name = _safe_name(filename)

    if is_image(filename, content_type):
        try:
            src_img = Image.open(io.BytesIO(data))
            # Read dimensions after EXIF transpose so UI displays the intended
            # orientation, but DO NOT rewrite the original file.
            display_img = ImageOps.exif_transpose(src_img)
            w, h = display_img.size
            fmt = (src_img.format or "").upper()
            inferred_ext = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp", "BMP": "bmp"}.get(fmt, "")
            safe_ext = ext if ext and len(ext) <= 12 else (inferred_ext or "img")

            full_rel = f"files/{uid}.{safe_ext}"
            full_path = os.path.join(settings.UPLOAD_DIR, full_rel)
            # Store the original bytes exactly as uploaded.
            with open(full_path, "wb") as f:
                f.write(data)

            # Thumbnail is separate. If thumbnail generation fails, keep the
            # original image and just omit the thumb.
            thumb_url = ""
            try:
                thumb_rel = f"thumbs/{uid}.jpg"
                thumb_path = os.path.join(settings.UPLOAD_DIR, thumb_rel)
                thumb = display_img.convert("RGB")
                thumb.thumbnail((480, 480))
                thumb.save(thumb_path, "JPEG", quality=82, optimize=True)
                thumb_url = f"/uploads/{thumb_rel}"
            except Exception:
                thumb_url = ""

            return SavedFile(
                url=f"/uploads/{full_rel}",
                thumb_url=thumb_url,
                name=original_name,
                size=len(data),
                width=w,
                height=h,
                kind="image",
            )
        except Exception:
            # Not a valid image -> fall through and store as a generic file.
            pass

    # generic file: store original bytes exactly as uploaded.
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
