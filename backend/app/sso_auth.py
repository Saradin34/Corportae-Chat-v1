"""NTLM / Kerberos SSO (SPNEGO) and reverse-proxy authentication.

Production recommendation:
  Put nginx / IIS / Apache with mod_auth_kerb in front of the app.
  The proxy validates the Kerberos/NTLM ticket and passes the user
  name in the REMOTE_USER header (or X-Remote-User).  FastAPI then
  only needs to read that header — no keytab required inside the
  container.

Direct SPNEGO:
  If SSO_ALLOW_NEGOTIATE is true, the backend itself handles the
  Negotiate handshake.  On Linux this needs a keytab
  (KRB5_KTNAME / SSO_KEYTAB_PATH).  On Windows SSPI is used.
"""
from __future__ import annotations

import base64
import logging
import os
import traceback
from typing import Any

from fastapi import HTTPException, Request, status

from .config import settings
from .ldap_auth import LdapError, _search_connection, _norm

logger = logging.getLogger("corporate-chat")

try:
    import spnego
except ImportError:  # pragma: no cover
    spnego = None


# ---------------------------------------------------------------------------
# 1. Reverse-proxy SSO (most reliable for production)
# ---------------------------------------------------------------------------
def get_proxy_user(request: Request) -> str | None:
    """Read the authenticated user from reverse-proxy headers."""
    if not settings.SSO_ENABLED or not settings.SSO_ALLOW_PROXY:
        return None
    headers = [
        "remote-user",
        "x-remote-user",
        "x-webauth-user",
        "ad_login",
        "x-ms-client-principal-name",
    ]
    for h in headers:
        val = request.headers.get(h)
        if val:
            return _normalize_username(val)
    return None


# ---------------------------------------------------------------------------
# 2. Direct SPNEGO (Negotiate)
# ---------------------------------------------------------------------------
class _NegotiateStore:
    """In-memory store for multi-step NTLM contexts.

    In a multi-worker deployment you must replace this with a Redis
    or database-backed cache (e.g. key = client-ip + session-id).
    """
    _store: dict[str, Any] = {}

    @classmethod
    def get(cls, cid: str) -> Any | None:
        return cls._store.get(cid)

    @classmethod
    def set(cls, cid: str, ctx: Any) -> None:
        cls._store[cid] = ctx

    @classmethod
    def pop(cls, cid: str) -> Any | None:
        return cls._store.pop(cid, None)


def _spn_to_hostname(spn: str | None) -> str:
    """Extract the bare FQDN from a Kerberos SPN.

    "HTTP/chat.kupava.by@KUPAVA.BY" -> "chat.kupava.by"
    "chat.kupava.by"                -> "chat.kupava.by"
    ""                              -> ""
    """
    if not spn:
        return ""
    name = spn.strip()
    if "/" in name:           # drop service class, e.g. "HTTP/"
        name = name.split("/", 1)[1]
    if "@" in name:           # drop realm, e.g. "@KUPAVA.BY"
        name = name.split("@", 1)[0]
    return name.strip()


def _new_server_context() -> Any | None:
    """Create a spnego server context (Negotiate)."""
    if spnego is None:
        logger.error("SPNEGO library (pyspnego) is not installed")
        return None

    # Ensure krb5.conf is picked up by MIT krb5
    if os.path.exists("/etc/krb5.conf"):
        os.environ.setdefault("KRB5_CONFIG", "/etc/krb5.conf")
        logger.info("KRB5_CONFIG set to /etc/krb5.conf")

    kt_path = settings.SSO_KEYTAB_PATH
    if kt_path:
        if not os.path.exists(kt_path):
            logger.error("Keytab file not found: %s", kt_path)
            return None
        if not os.access(kt_path, os.R_OK):
            logger.error("Keytab file not readable: %s", kt_path)
            return None
        logger.info("Keytab found: %s (size=%s)", kt_path, os.path.getsize(kt_path))
        os.environ["KRB5_KTNAME"] = kt_path
    else:
        logger.info("No explicit SSO_KEYTAB_PATH — using default Kerberos keytab")

    # spnego.server() expects a bare hostname (FQDN), e.g. "chat.kupava.by".
    # SSO_SERVICE_NAME is usually the full SPN "HTTP/chat.kupava.by@KUPAVA.BY".
    # Strip the service ("HTTP/") prefix and the realm ("@REALM") suffix so the
    # acceptor name matches the keytab principal correctly.
    hostname = _spn_to_hostname(settings.SSO_SERVICE_NAME)
    logger.info(
        "Creating spnego server context (spn=%s, hostname=%s)",
        settings.SSO_SERVICE_NAME or "auto", hostname or "auto",
    )

    try:
        if os.name == "nt":  # Windows → SSPI
            ctx = spnego.server(
                protocol="negotiate",
                hostname=hostname or None,
            )
            logger.info("SSPI server context created")
            return ctx

        # Linux / macOS → MIT krb5 via keytab
        ctx = spnego.server(
            protocol="negotiate",
            hostname=hostname or None,
        )
        logger.info("MIT krb5 server context created successfully")
        return ctx
    except Exception as e:
        logger.error("SPNEGO server context creation failed: %s", e)
        logger.error("Traceback: %s", traceback.format_exc())
        return None


def handle_negotiate(authorization: str | None, client_id: str | None = None) -> tuple[str, dict]:
    """Validate a Negotiate token.

    Returns:
        (username, response_headers)

    Raises:
        HTTPException(401) with WWW-Authenticate header when more data is needed.
    """
    if not settings.SSO_ENABLED or not settings.SSO_ALLOW_NEGOTIATE:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Direct Negotiate SSO is not enabled",
        )

    if not authorization or not authorization.startswith("Negotiate "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Negotiate"},
            detail="Negotiate authentication required",
        )

    token = base64.b64decode(authorization[10:])
    logger.info("Negotiate token received (len=%s, client_id=%s)", len(token), client_id)

    # Try to resume existing context (NTLM needs 3 steps)
    ctx = None
    if client_id:
        ctx = _NegotiateStore.pop(client_id)
        if ctx:
            logger.info("Resumed existing SPNEGO context for client_id=%s", client_id)

    if ctx is None:
        ctx = _new_server_context()

    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SSO service not available (cannot create SPNEGO context) — check keytab and SSO_SERVICE_NAME",
        )

    try:
        token_out = ctx.step(token)
        logger.info("SPNEGO step OK — complete=%s, token_out_len=%s", ctx.complete, len(token_out) if token_out else 0)
    except Exception as e:
        logger.error("SPNEGO step failed: %s", e)
        logger.error("Traceback: %s", traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Negotiate"},
            detail=f"Invalid SSO credentials: {type(e).__name__}: {e}",
        )

    if not ctx.complete:
        # Need another round-trip
        cid = client_id or os.urandom(8).hex()
        _NegotiateStore.set(cid, ctx)
        out_hdr = {"WWW-Authenticate": "Negotiate", "X-SSO-Client-Id": cid}
        if token_out:
            out_hdr["WWW-Authenticate"] = f"Negotiate {base64.b64encode(token_out).decode()}"
        logger.info("SPNEGO needs another round-trip (client_id=%s)", cid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers=out_hdr,
            detail="continue",
        )

    user = ctx.client_principal
    if not user:
        logger.error("SPNEGO context complete but client_principal is empty")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not determine user from SSO token",
        )

    logger.info("SPNEGO authenticated user: %s", user)
    return _normalize_username(user), {}


def _normalize_username(raw: str) -> str:
    """Return lower-case username without domain."""
    if "\\" in raw:
        raw = raw.split("\\", 1)[1]
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    return raw.strip().lower()


# ---------------------------------------------------------------------------
# 3. LDAP enrichment after SSO (displayName, groups, etc.)
# ---------------------------------------------------------------------------
async def ldap_enrich_after_sso(username: str) -> dict | None:
    """Look up a user in AD via the service account to get profile info."""
    if not settings.LDAP_ENABLED:
        return None
    try:
        conn = _search_connection()
        if conn is None:
            return None
        if not conn.bound:
            conn.bind()

        flt = f"(&(objectClass=*)({settings.LDAP_LOGIN_ATTR}={username}))"
        attrs = [
            "displayName", "mail", "memberOf",
            "title", "telephoneNumber", "physicalDeliveryOfficeName",
        ]
        conn.search(settings.LDAP_BASE_DN, flt, attributes=attrs)
        if not conn.entries:
            conn.unbind()
            return None

        a = conn.entries[0].entry_attributes_as_dict
        conn.unbind()
        return {
            "username": username,
            "email": _norm(a.get("mail")) or f"{username}@{settings.LDAP_DOMAIN}",
            "full_name": _norm(a.get("displayName")) or _norm(a.get("cn")) or username,
            "title": _norm(a.get("title")),
            "phone": _norm(a.get("telephoneNumber")),
            "office": _norm(a.get("physicalDeliveryOfficeName")),
            "groups": [str(g) for g in (a.get("memberOf") or [])],
        }
    except Exception as e:
        logger.warning("LDAP enrichment after SSO failed for %s: %s", username, e)
        return None
