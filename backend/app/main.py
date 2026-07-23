"""FastAPI application entrypoint."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .config import settings
from .database import async_session_maker, init_db
from .models import User
from .routers import admin, auth, calendar, calls, chats, groups, messages, settings as settings_router, support, uploads, users, ws
from .security import hash_password
from .utils import random_color

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("corporate-chat")


async def _ensure_admin(max_retries: int = 10, delay: float = 1.5) -> None:
    """Create the default admin if missing. Retries on transient DB errors,
    and never raises out of startup (a failure here must not crash the app)."""
    for attempt in range(1, max_retries + 1):
        try:
            async with async_session_maker() as db:
                existing = (
                    await db.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
                ).scalar_one_or_none()
                if existing is None:
                    admin_user = User(
                        username=settings.ADMIN_USERNAME,
                        email=settings.ADMIN_EMAIL,
                        password_hash=hash_password(settings.ADMIN_PASSWORD),
                        full_name="Administrator",
                        avatar_color=random_color(),
                        role="admin",
                    )
                    db.add(admin_user)
                    await db.commit()
                    logger.info("Created default admin user '%s'", settings.ADMIN_USERNAME)
                return
        except Exception as e:  # noqa: BLE001
            logger.warning("Admin bootstrap attempt %d/%d failed: %s",
                           attempt, max_retries, e)
            await asyncio.sleep(delay)
    logger.error("Could not ensure admin user after %d attempts (continuing)", max_retries)


async def _ensure_default_group() -> None:
    """Create the implicit default group ('Пользователи без группы') if missing.
    Never raises out of startup."""
    try:
        from .routers.groups import ensure_default_group
        async with async_session_maker() as db:
            await ensure_default_group(db)
            logger.info("Default group ensured")
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not ensure default group: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
        await _ensure_admin()
        await _ensure_default_group()
        # Optional Asterisk AMI listener for incoming/missed call notifications.
        app.state.ami_stop = asyncio.Event()
        app.state.ami_task = None
        if settings.AMI_ENABLED:
            from .ami import ami_listener
            app.state.ami_task = asyncio.create_task(ami_listener(app.state.ami_stop))
        logger.info("Startup complete")
    except Exception:
        # Log the FULL traceback so the real cause is visible in
        # `docker compose logs backend` instead of a generic summary.
        logger.exception("FATAL: startup failed")
        raise
    yield
    try:
        if getattr(app.state, "ami_stop", None):
            app.state.ami_stop.set()
        if getattr(app.state, "ami_task", None):
            app.state.ami_task.cancel()
    except Exception:
        pass
    logger.info("Shutdown")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# CORS: the frontend is served by nginx on the SAME origin as the API, so no
# cross-origin access is needed. We keep it locked down (no wildcard) — a
# wide-open API is exactly what corporate AV / scanners flag. Extra trusted
# origins (e.g. a separate domain) can be added via CORS_ORIGINS env var.
_cors_origins = [o.strip() for o in (settings.CORS_ORIGINS or "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(chats.router)
app.include_router(messages.router)
app.include_router(admin.router)
app.include_router(groups.router)
app.include_router(settings_router.router)
app.include_router(uploads.router)
app.include_router(calls.router)
app.include_router(calendar.router)
app.include_router(support.router)
app.include_router(ws.router)


# Serve uploaded files. In Docker, nginx serves /uploads directly from the
# shared volume (faster); this mount is the fallback for local mode / safety.
import os as _os  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from fastapi.staticfiles import StaticFiles as _StaticFiles  # noqa: E402

_os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", _StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}


# ----- Optional static serving (local mode, no nginx) -----
# Enabled by SERVE_STATIC=1. In Docker, nginx serves the frontend instead.
import os  # noqa: E402

if os.environ.get("SERVE_STATIC") == "1":
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _frontend = Path(__file__).resolve().parents[2] / "frontend" / "html"
    if _frontend.exists():
        app.mount("/css", StaticFiles(directory=_frontend / "css"), name="css")
        app.mount("/js", StaticFiles(directory=_frontend / "js"), name="js")

        @app.get("/")
        async def _index():
            return FileResponse(_frontend / "index.html")

        @app.get("/{full_path:path}")
        async def _spa(full_path: str):
            # Serve index.html for any non-API route (SPA fallback)
            candidate = _frontend / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(_frontend / "index.html")
