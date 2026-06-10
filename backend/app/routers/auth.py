"""Authentication routes: register, login (local + Active Directory)."""
import logging

import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import User
from ..schemas import (
    AuthConfigOut,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from ..security import create_access_token, get_current_user, hash_password, verify_password
from ..utils import random_color

logger = logging.getLogger("corporate-chat")
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfigOut)
async def auth_config():
    """Tells the frontend which sign-in methods are enabled."""
    return AuthConfigOut(
        local_auth=settings.ALLOW_LOCAL_AUTH,
        ldap_enabled=settings.LDAP_ENABLED,
        ldap_domain=settings.LDAP_DOMAIN if settings.LDAP_ENABLED else "",
        sso_enabled=settings.SSO_ENABLED,
        sso_negotiate=settings.SSO_ALLOW_NEGOTIATE,
        sso_allow_proxy=settings.SSO_ALLOW_PROXY,
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if not settings.ALLOW_LOCAL_AUTH:
        raise HTTPException(status_code=403, detail="Локальная регистрация отключена. Используйте вход через Active Directory.")
    existing = (
        await db.execute(
            select(User).where(or_(User.username == data.username, User.email == data.email))
        )
    ).scalar_one_or_none()
    if existing:
        if existing.username == data.username:
            raise HTTPException(status_code=400, detail="Имя пользователя уже занято")
        raise HTTPException(status_code=400, detail="Email уже используется")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        full_name=data.full_name or data.username,
        avatar_color=random_color(),
        auth_source="local",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


async def _ldap_login(data: LoginRequest, db: AsyncSession) -> TokenResponse:
    """Authenticate against Active Directory, JIT-provision, map admin role."""
    from .. import ldap_auth

    try:
        ad_user = ldap_auth.authenticate(data.username, data.password)
    except PermissionError as e:
        raise HTTPException(status_code=401, detail=str(e) or "Неверные учётные данные AD")
    except ldap_auth.LdapError as e:
        logger.error("LDAP error: %s", e)
        raise HTTPException(status_code=503, detail="Сервер Active Directory недоступен")
    except Exception as e:  # noqa: BLE001
        logger.exception("Unexpected LDAP failure")
        raise HTTPException(status_code=503, detail="Ошибка аутентификации AD")

    # find existing AD user (by username or email)
    user = (
        await db.execute(
            select(User).where(
                or_(User.username == ad_user.username, User.email == ad_user.email)
            )
        )
    ).scalar_one_or_none()

    mapped_role = "admin" if ad_user.is_admin else "user"

    if user is None:
        # JIT provisioning
        user = User(
            username=ad_user.username,
            email=ad_user.email,
            password_hash="!ldap",  # not usable for local login
            full_name=ad_user.full_name or ad_user.username,
            title=ad_user.title,
            phone=ad_user.phone,
            office=ad_user.office,
            avatar_color=random_color(),
            role=mapped_role,
            auth_source="ldap",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("JIT-provisioned AD user '%s' role=%s", user.username, mapped_role)
    else:
        # keep profile/role in sync with AD on each login
        changed = False
        if user.auth_source != "ldap":
            user.auth_source = "ldap"; changed = True
        if ad_user.full_name and user.full_name != ad_user.full_name:
            user.full_name = ad_user.full_name; changed = True
        if user.title != ad_user.title:
            user.title = ad_user.title; changed = True
        if user.phone != ad_user.phone:
            user.phone = ad_user.phone; changed = True
        if user.office != ad_user.office:
            user.office = ad_user.office; changed = True
        if settings.LDAP_ADMIN_GROUP and user.role != mapped_role:
            # only auto-manage role when an admin group is configured
            user.role = mapped_role; changed = True
        if changed:
            await db.commit()
            await db.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")

    # NOTE: AD users are NOT auto-added to any chats or groups on login.
    # They start with no chats and either create chats themselves or get added
    # by others. (Group/department auto-join from AD was intentionally removed.)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    # Active Directory path
    if settings.LDAP_ENABLED:
        # Try AD first. If local auth is also allowed, fall back to local on
        # AD auth failure so emergency/local accounts still work.
        if not settings.ALLOW_LOCAL_AUTH:
            return await _ldap_login(data, db)
        try:
            return await _ldap_login(data, db)
        except HTTPException as ad_err:
            # Only fall back for credential errors, not server errors.
            if ad_err.status_code != 401:
                raise
            # fall through to local auth below

    if not settings.ALLOW_LOCAL_AUTH:
        raise HTTPException(status_code=403, detail="Локальный вход отключён. Используйте Active Directory.")

    user = (
        await db.execute(
            select(User).where(or_(User.username == data.username, User.email == data.username))
        )
    ).scalar_one_or_none()

    if user is None or user.auth_source == "ldap" or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверное имя пользователя или пароль")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.auth_source == "ldap":
        raise HTTPException(status_code=400, detail="Пароль управляется в Active Directory и не может быть изменён здесь")
    if not verify_password(data.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Неверный текущий пароль")
    user.password_hash = hash_password(data.new_password)
    await db.commit()
    return {"ok": True}


async def _complete_sso_login(username: str, db: AsyncSession) -> TokenResponse:
    """Create or update a local user after successful SSO and return JWT."""
    from ..sso_auth import ldap_enrich_after_sso

    ldap_info = await ldap_enrich_after_sso(username)

    email = ldap_info["email"] if ldap_info else f"{username}@{settings.LDAP_DOMAIN}"
    full_name = ldap_info["full_name"] if ldap_info else username

    user = (
        await db.execute(
            select(User).where(
                or_(User.username == username, User.email == email)
            )
        )
    ).scalar_one_or_none()

    if user is None:
        role = "user"
        if ldap_info and settings.LDAP_ADMIN_GROUP:
            if any(settings.LDAP_ADMIN_GROUP.lower() == g.strip().lower() for g in ldap_info.get("groups", [])):
                role = "admin"

        user = User(
            username=username,
            email=email,
            password_hash="!sso",
            full_name=full_name,
            title=ldap_info.get("title", "") if ldap_info else "",
            phone=ldap_info.get("phone", "") if ldap_info else "",
            office=ldap_info.get("office", "") if ldap_info else "",
            avatar_color=random_color(),
            role=role,
            auth_source="sso",
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("JIT-provisioned SSO user '%s' role=%s", username, role)
    else:
        if user.auth_source not in ("sso", "ldap"):
            user.auth_source = "sso"
            await db.commit()
            await db.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Аккаунт заблокирован")

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


def _sso_html_response(token: str, user: UserOut) -> HTMLResponse:
    """Return a tiny HTML page that saves the JWT into localStorage and redirects."""
    user_json = user.model_dump_json()
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html>
<head><title>SSO Login</title></head>
<body>
<script>
(function() {{
    try {{
        localStorage.setItem("cc_token", {json.dumps(token)});
        localStorage.setItem("cc_user", JSON.stringify({user_json}));
        location.href = "/";
    }} catch (e) {{
        document.body.innerHTML = "<h2>Ошибка сохранения сессии. Попробуйте обычный вход.</h2>";
    }}
}})();
</script>
</body>
</html>""")


@router.get("/sso")
async def sso_login(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """SSO entry point (reverse-proxy or SPNEGO Negotiate).

    - Proxy mode: reads REMOTE_USER / X-Remote-User headers.
    - Negotiate mode: reads Authorization: Negotiate <token>.
    - If no auth present, returns 401 with WWW-Authenticate: Negotiate.
    - For browser requests (Accept: text/html) returns a page that stores
      the JWT in localStorage and redirects to /.
    - For fetch/XHR requests returns JSON.
    """
    if not settings.SSO_ENABLED:
        raise HTTPException(status_code=403, detail="SSO отключено")

    # Detect whether the request comes from a browser page load (not fetch/XHR)
    accept = request.headers.get("Accept", "")
    wants_json = "application/json" in accept

    # 1. Proxy headers
    from ..sso_auth import get_proxy_user
    try:
        proxy_user = get_proxy_user(request)
        if proxy_user:
            logger.info("SSO proxy login for user: %s", proxy_user)
            token_resp = await _complete_sso_login(proxy_user, db)
            if wants_json:
                return token_resp
            return _sso_html_response(token_resp.access_token, token_resp.user)
    except Exception as e:
        logger.error("SSO proxy error: %s", e)
        raise HTTPException(status_code=503, detail="Ошибка прокси-SSO")

    # 2. Negotiate header
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Negotiate "):
        from ..sso_auth import handle_negotiate
        try:
            username, _ = handle_negotiate(
                auth,
                request.headers.get("X-SSO-Client-Id"),
            )
            logger.info("SSO negotiate login for user: %s", username)
            token_resp = await _complete_sso_login(username, db)
            if wants_json:
                return token_resp
            return _sso_html_response(token_resp.access_token, token_resp.user)
        except HTTPException as e:
            if e.status_code == 401:
                return JSONResponse(
                    status_code=401,
                    headers=e.headers or {"WWW-Authenticate": "Negotiate"},
                    content={"detail": e.detail or "Unauthorized"},
                )
            raise
        except Exception as e:
            logger.error("SSO negotiate error: %s", e)
            raise HTTPException(status_code=503, detail="Ошибка SSO-сервиса (Negotiate)")

    # 3. No auth → challenge
    if settings.SSO_ALLOW_NEGOTIATE:
        return JSONResponse(
            status_code=401,
            headers={"WWW-Authenticate": "Negotiate"},
            content={"detail": "Negotiate authentication required"},
        )
    raise HTTPException(status_code=401, detail="SSO: нет данных аутентификации")
