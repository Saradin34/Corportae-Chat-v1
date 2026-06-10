"""Active Directory / LDAP authentication.

Flow (on-prem AD, direct bind):
  1. Bind to a domain controller using the user's own credentials
     (username@domain or DOMAIN\\username). A successful bind == valid password.
  2. Search the directory for the user's entry to read profile attributes
     and group memberships (memberOf).
  3. Map AD group membership -> application role (admin/user).

Designed to be testable offline via ldap3's MOCK_SYNC server.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ldap3 import ALL, NTLM, SIMPLE, Connection, Server, ServerPool
from ldap3.core.exceptions import LDAPException

from .config import settings

logger = logging.getLogger("corporate-chat")


@dataclass
class LdapUser:
    username: str
    email: str
    full_name: str
    dn: str
    title: str = ""
    phone: str = ""
    office: str = ""
    groups: list[str] = field(default_factory=list)
    is_admin: bool = False


@dataclass
class LdapGroup:
    """A security group found in Active Directory."""
    dn: str
    name: str          # CN / sAMAccountName of the group
    description: str = ""
    member_count: int = 0


class LdapError(Exception):
    """Raised for configuration/connection problems (not bad passwords)."""


# Injectable hook so tests can supply a mock connection factory.
_connection_factory = None


def set_connection_factory(factory) -> None:
    """Test seam: factory(user_dn, password) -> ldap3.Connection."""
    global _connection_factory
    _connection_factory = factory


def _build_server_pool() -> ServerPool:
    servers = []
    for raw in settings.LDAP_SERVERS.split(","):
        raw = raw.strip()
        if not raw:
            continue
        use_ssl = settings.LDAP_USE_SSL or raw.lower().startswith("ldaps://")
        host = raw.replace("ldaps://", "").replace("ldap://", "")
        servers.append(Server(host, use_ssl=use_ssl, get_info=ALL, connect_timeout=settings.LDAP_TIMEOUT))
    if not servers:
        raise LdapError("LDAP_SERVERS is not configured")
    return ServerPool(servers, active=True, exhaust=True)


def _bind_identities(username: str) -> list[str]:
    """Identities to try for the user bind, in order."""
    ids = []
    if settings.LDAP_DOMAIN:
        ids.append(f"{username}@{settings.LDAP_DOMAIN}")  # UPN
    if settings.LDAP_NETBIOS:
        ids.append(f"{settings.LDAP_NETBIOS}\\{username}")  # down-level
    ids.append(username)  # last resort (raw)
    return ids


def _make_connection(user_identity: str, password: str) -> Connection:
    if _connection_factory is not None:
        return _connection_factory(user_identity, password)
    server = _build_server_pool()
    
    # 🔧 ИСПРАВЛЕНИЕ: Используем NEGOTIATE вместо NTLM
    from ldap3 import NTLM, SIMPLE, KERBEROS
    
    # Проверяем, нужно ли использовать Kerberos
    if settings.LDAP_AUTH_MECHANISM and settings.LDAP_AUTH_MECHANISM.upper() == "NEGOTIATE":
        auth = KERBEROS
    elif "\\" in user_identity:
        auth = NTLM
    else:
        auth = SIMPLE
    
    conn = Connection(
        server,
        user=user_identity,
        password=password,
        authentication=auth,
        receive_timeout=settings.LDAP_TIMEOUT,
        auto_bind=False,
    )
    if settings.LDAP_START_TLS and not settings.LDAP_USE_SSL:
        conn.open()
        conn.start_tls()
    return conn


def _search_connection() -> Connection:
    """Connection used for directory search (service account, or anon mock)."""
    if _connection_factory is not None:
        # In mock mode, the service-account bind uses the configured creds.
        return _connection_factory(settings.LDAP_BIND_DN or "service", settings.LDAP_BIND_PASSWORD or "service")
    server = _build_server_pool()
    if settings.LDAP_BIND_DN:
        conn = Connection(
            server,
            user=settings.LDAP_BIND_DN,
            password=settings.LDAP_BIND_PASSWORD,
            authentication=SIMPLE,
            receive_timeout=settings.LDAP_TIMEOUT,
            auto_bind=False,
        )
        if settings.LDAP_START_TLS and not settings.LDAP_USE_SSL:
            conn.open()
            conn.start_tls()
        return conn
    return None


def _norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)


def authenticate(username: str, password: str) -> LdapUser:
    """Authenticate against AD. Returns LdapUser or raises.

    Raises:
        PermissionError  -> wrong credentials / not in required group.
        LdapError        -> server/config error.
    """
    if not password:
        raise PermissionError("Пустой пароль")

    username = username.strip()
    # strip a domain the user may have typed (DOMAIN\\user or user@domain)
    if "\\" in username:
        username = username.split("\\", 1)[1]
    if "@" in username:
        username = username.split("@", 1)[0]

    # 1) Verify the password via direct bind, trying each identity form.
    bound_conn = None
    last_err = None
    for identity in _bind_identities(username):
        try:
            conn = _make_connection(identity, password)
            ok = conn.bind()
            if ok:
                bound_conn = conn
                logger.info("LDAP bind OK for %s", identity)
                break
            last_err = conn.result
        except LDAPException as e:
            last_err = str(e)
            logger.warning("LDAP bind error for %s: %s", identity, e)
    if bound_conn is None:
        raise PermissionError("Неверное имя пользователя или пароль (AD)")

    # 2) Search for the user entry (use service account if provided, else the
    #    user's own authenticated connection).
    search_conn = _search_connection() or bound_conn
    try:
        if search_conn is not bound_conn:
            if not search_conn.bind():
                # fall back to the user connection
                search_conn = bound_conn
    except LDAPException:
        search_conn = bound_conn

    attrs = ["sAMAccountName", "userPrincipalName", "mail", "displayName", "cn", "givenName", "sn", "memberOf",
             "title", "telephoneNumber", "physicalDeliveryOfficeName"]
    flt = f"(&(objectClass=*)({settings.LDAP_LOGIN_ATTR}={_escape(username)}))"
    entry = None
    try:
        search_conn.search(settings.LDAP_BASE_DN, flt, attributes=attrs)
        if search_conn.entries:
            entry = search_conn.entries[0]
    except LDAPException as e:
        logger.warning("LDAP search failed: %s", e)

    # 3) Build the user profile.
    if entry is not None:
        dn = str(entry.entry_dn)
        a = entry.entry_attributes_as_dict
        email = _norm(a.get("mail")) or f"{username}@{settings.LDAP_DOMAIN}"
        full_name = _norm(a.get("displayName")) or _norm(a.get("cn")) or username
        title = _norm(a.get("title"))
        phone = _norm(a.get("telephoneNumber"))
        office = _norm(a.get("physicalDeliveryOfficeName"))
        groups = [str(g) for g in (a.get("memberOf") or [])]
    else:
        dn = f"{settings.LDAP_LOGIN_ATTR}={username},{settings.LDAP_BASE_DN}"
        email = f"{username}@{settings.LDAP_DOMAIN}"
        full_name = username
        title = phone = office = ""
        groups = []

    # required-group allow-list
    if settings.LDAP_REQUIRED_GROUP:
        if not _in_group(groups, settings.LDAP_REQUIRED_GROUP):
            raise PermissionError("Доступ запрещён: нет членства в требуемой группе AD")

    is_admin = bool(settings.LDAP_ADMIN_GROUP) and _in_group(groups, settings.LDAP_ADMIN_GROUP)

    # tidy up connections
    for c in {bound_conn, search_conn}:
        try:
            c.unbind()
        except Exception:
            pass

    return LdapUser(
        username=username.lower(),
        email=email,
        full_name=full_name,
        dn=dn,
        title=title,
        phone=phone,
        office=office,
        groups=groups,
        is_admin=is_admin,
    )


def _in_group(user_groups: list[str], target: str) -> bool:
    t = target.strip().lower()
    return any(t == g.strip().lower() for g in user_groups)


def _escape(value: str) -> str:
    """Escape LDAP filter special characters (RFC 4515)."""
    out = []
    for ch in value:
        if ch == "*":
            out.append("\\2a")
        elif ch == "(":
            out.append("\\28")
        elif ch == ")":
            out.append("\\29")
        elif ch == "\\":
            out.append("\\5c")
        elif ch == "\x00":
            out.append("\\00")
        else:
            out.append(ch)
    return "".join(out)


def group_cn(dn: str) -> str:
    """Extract a human-readable name from a group/OU DN.

    'CN=Sales,OU=Departments,DC=company,DC=local' -> 'Sales'
    """
    if not dn:
        return ""
    first = dn.split(",", 1)[0].strip()
    if "=" in first:
        return first.split("=", 1)[1].strip()
    return first


def selectable_groups(group_dns: list[str]) -> list[str]:
    """Filter the user's AD groups according to LDAP_GROUP_FILTER / EXCLUDE."""
    flt = [s.strip().lower() for s in settings.LDAP_GROUP_FILTER.split(",") if s.strip()]
    excl = [s.strip().lower() for s in settings.LDAP_GROUP_EXCLUDE.split(",") if s.strip()]
    result = []
    for dn in group_dns:
        low = dn.lower()
        name_low = group_cn(dn).lower()
        if flt and not any(f in low for f in flt):
            continue
        if any(e == name_low or e in low for e in excl):
            continue
        result.append(dn)
    return result


def _open_search_connection() -> Connection:
    """Bind a directory-search connection (service account). Raises LdapError
    when LDAP isn't usable for searching (no service account / can't bind)."""
    if not settings.LDAP_ENABLED:
        raise LdapError("Интеграция с Active Directory отключена")
    conn = _search_connection()
    if conn is None:
        raise LdapError(
            "Не настроена сервисная учётная запись AD (LDAP_BIND_DN/LDAP_BIND_PASSWORD) — "
            "поиск по каталогу невозможен"
        )
    try:
        if not conn.bind():
            raise LdapError(f"Не удалось подключиться к AD сервисной учёткой: {conn.result}")
    except LDAPException as e:
        raise LdapError(f"Ошибка подключения к AD: {e}")
    return conn


def search_groups(query: str, limit: int = 25) -> list[LdapGroup]:
    """Search Active Directory for security/distribution groups whose name
    matches `query` (substring, case-insensitive). Returns a list of LdapGroup.

    Used by the admin UI to find a group by name and link it to an app group.
    """
    query = (query or "").strip()
    conn = _open_search_connection()
    try:
        q = _escape(query)
        # match on cn / sAMAccountName / displayName; objectCategory=group
        name_filter = f"(|(cn=*{q}*)(sAMAccountName=*{q}*)(displayName=*{q}*))" if query else ""
        flt = f"(&(objectCategory=group){name_filter})"
        attrs = ["cn", "sAMAccountName", "displayName", "description", "member"]
        conn.search(settings.LDAP_BASE_DN, flt, attributes=attrs, size_limit=max(1, min(limit, 100)))
        out: list[LdapGroup] = []
        for entry in conn.entries:
            a = entry.entry_attributes_as_dict
            name = _norm(a.get("cn")) or _norm(a.get("sAMAccountName")) or group_cn(str(entry.entry_dn))
            members = a.get("member") or []
            out.append(LdapGroup(
                dn=str(entry.entry_dn),
                name=name,
                description=_norm(a.get("description")),
                member_count=len(members) if isinstance(members, (list, tuple)) else (1 if members else 0),
            ))
        # sort by closeness: exact/startswith first, then alphabetically
        ql = query.lower()
        out.sort(key=lambda g: (
            0 if g.name.lower() == ql else (1 if g.name.lower().startswith(ql) else 2),
            g.name.lower(),
        ))
        return out
    except LDAPException as e:
        raise LdapError(f"Ошибка поиска групп в AD: {e}")
    finally:
        try:
            conn.unbind()
        except Exception:
            pass


def group_members(group_dn: str) -> list[LdapUser]:
    """Return the user members of an AD group (by its DN).

    Uses the AD-specific in-chain matching rule (LDAP_MATCHING_RULE_IN_CHAIN,
    1.2.840.113556.1.4.1941) so nested-group members are included too.
    """
    if not group_dn:
        return []
    conn = _open_search_connection()
    try:
        gdn = _escape(group_dn)
        # users (person) that are members of this group, including nested groups
        flt = (
            "(&(objectCategory=person)(objectClass=user)"
            f"(memberOf:1.2.840.113556.1.4.1941:={gdn}))"
        )
        attrs = [
            "sAMAccountName", "userPrincipalName", "mail", "displayName", "cn",
            "title", "telephoneNumber", "physicalDeliveryOfficeName", "memberOf",
            "userAccountControl",
        ]
        conn.search(settings.LDAP_BASE_DN, flt, attributes=attrs, size_limit=0)
        out: list[LdapUser] = []
        for entry in conn.entries:
            a = entry.entry_attributes_as_dict
            sam = _norm(a.get("sAMAccountName"))
            if not sam:
                continue
            # skip disabled accounts (userAccountControl bit 0x2 == ACCOUNTDISABLE)
            try:
                uac = int(_norm(a.get("userAccountControl")) or "0")
                if uac & 0x2:
                    continue
            except (TypeError, ValueError):
                pass
            groups = [str(g) for g in (a.get("memberOf") or [])]
            out.append(LdapUser(
                username=sam.lower(),
                email=_norm(a.get("mail")) or f"{sam}@{settings.LDAP_DOMAIN}",
                full_name=_norm(a.get("displayName")) or _norm(a.get("cn")) or sam,
                dn=str(entry.entry_dn),
                title=_norm(a.get("title")),
                phone=_norm(a.get("telephoneNumber")),
                office=_norm(a.get("physicalDeliveryOfficeName")),
                groups=groups,
                is_admin=bool(settings.LDAP_ADMIN_GROUP) and _in_group(groups, settings.LDAP_ADMIN_GROUP),
            ))
        return out
    except LDAPException as e:
        raise LdapError(f"Ошибка получения участников группы AD: {e}")
    finally:
        try:
            conn.unbind()
        except Exception:
            pass
