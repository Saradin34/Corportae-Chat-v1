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

try:
    import gssapi
except ImportError:  # pragma: no cover
    gssapi = None


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






def _negotiate_token_hint(token: bytes) -> str:
    """Best-effort token mechanism hint for diagnostics.

    Important: Windows/Chrome SPNEGO NegTokenInit can include several offered
    mechanism OIDs (Kerberos and NTLM) in the same token. If Kerberos is present
    we must NOT reject the token just because the NTLM OID is also listed.
    Raw NTLMSSP is still a definite NTLM token.
    """
    if token.startswith(b"NTLMSSP"):
        return "ntlm-raw"
    has_kerberos = b"\x2a\x86\x48\x86\xf7\x12\x01\x02\x02" in token  # 1.2.840.113554.1.2.2
    has_ntlm = b"\x2b\x06\x01\x04\x01\x82\x37\x02\x02\x0a" in token       # 1.3.6.1.4.1.311.2.2.10
    if has_kerberos and has_ntlm:
        return "kerberos-in-spnego+ntlm-offered"
    if has_kerberos:
        return "kerberos-in-spnego"
    if has_ntlm:
        return "ntlm-in-spnego"
    return "unknown"

def _service_parts() -> tuple[str, str | None]:
    """Parse SSO_SERVICE_NAME into (service, hostname) for pyspnego.

    Accepts either a full Kerberos SPN like HTTP/chat.company.local@REALM or
    just a hostname. pyspnego wants service="HTTP" and hostname="host" —
    passing the full SPN as hostname can make negotiation fall back/behave
    incorrectly.
    """
    raw = (settings.SSO_SERVICE_NAME or "").strip()
    if not raw:
        return "HTTP", None
    # Strip realm part.
    no_realm = raw.split("@", 1)[0]
    if "/" in no_realm:
        service, host = no_realm.split("/", 1)
        return (service or "HTTP"), (host or None)
    return "HTTP", no_realm


class _GssapiServerContext:
    """Small adapter with the same surface we use from pyspnego contexts.

    We acquire acceptor credentials explicitly from the configured keytab.
    This is more reliable in containers than relying on implicit default
    credential discovery through KRB5_KTNAME.
    """

    def __init__(self, service: str, hostname: str | None, keytab_path: str | None):
        if gssapi is None:
            raise RuntimeError("python-gssapi is not installed")
        # GSSAPI hostbased service name format is SERVICE@hostname.
        name_text = f"{service}@{hostname}" if hostname else f"{service}@"
        name = gssapi.Name(name_text, name_type=gssapi.NameType.hostbased_service)
        store = None
        if keytab_path:
            kt = keytab_path[5:] if keytab_path.startswith("FILE:") else keytab_path
            store = {"keytab": kt}
            logger.info("Acquiring explicit GSSAPI acceptor creds name=%s keytab=%s", name_text, kt)
            self._creds = gssapi.Credentials(name=name, usage="accept", store=store)
        else:
            logger.info("Acquiring default GSSAPI acceptor creds name=%s", name_text)
            self._creds = gssapi.Credentials(name=name, usage="accept")
        self._ctx = gssapi.SecurityContext(creds=self._creds, usage="accept")
        self.complete = False
        self.client_principal = None

    def step(self, token: bytes):
        out = self._ctx.step(token)
        self.complete = bool(self._ctx.complete)
        if self.complete and self._ctx.initiator_name:
            self.client_principal = str(self._ctx.initiator_name)
        return out

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
        os.environ["KRB5_KTNAME"] = kt_path if kt_path.startswith(("FILE:", "DIR:", "WRFILE:")) else "FILE:" + kt_path
    else:
        logger.info("No explicit SSO_KEYTAB_PATH — using default Kerberos keytab")

    service, hostname = _service_parts()
    logger.info("Creating Kerberos-only SPNEGO server context (service=%s, hostname=%s, spn=%s)", service, hostname or "auto", settings.SSO_SERVICE_NAME or "auto")

    try:
        if os.name != "nt" and gssapi is not None:
            # Linux container: explicitly acquire acceptor creds from keytab.
            ctx = _GssapiServerContext(service, hostname, os.environ.get("KRB5_KTNAME") or kt_path)
            logger.info("Explicit GSSAPI Kerberos acceptor context created successfully")
            return ctx

        # Windows / fallback path via pyspnego. Force Kerberos. Do NOT use
        # protocol="negotiate" here: when a browser offers NTLM, pyspnego may
        # try an NTLM acceptor and fail with NTLM_USER_FILE.
        ctx = spnego.server(
            protocol="kerberos",
            service=service,
            hostname=hostname,
        )
        logger.info("Kerberos server context created successfully")
        return ctx
    except Exception as e:
        logger.error("Kerberos server context creation failed: %s", e)
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
    hint = _negotiate_token_hint(token)
    logger.info("Negotiate token received (len=%s, mechanism_hint=%s, client_id=%s)", len(token), hint, client_id)
    if hint in ("ntlm-raw", "ntlm-in-spnego"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Negotiate"},
            detail=(
                "Kerberos SSO expected, but the browser sent NTLM. "
                "Use the FQDN that has an HTTP SPN, add the site to the browser/Windows intranet zone, "
                "verify setspn/keytab, and ensure the client has a Kerberos ticket. "
                f"mechanism={hint}"
            ),
        )

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
        msg = str(e)
        if "NTLM" in msg or "BadMechanism" in type(e).__name__ or "common mechanism" in msg:
            msg = (
                "Kerberos SSO expected, but the browser/client offered NTLM or no usable Kerberos ticket. "
                "Check SPN, keytab, DNS/FQDN, browser Integrated Auth settings and that the site is in the intranet/trusted zone. "
                f"Original error: {type(e).__name__}: {e}"
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Negotiate"},
            detail=msg,
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
