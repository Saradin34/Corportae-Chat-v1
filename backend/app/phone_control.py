"""Generic HTTP IP-phone control for direct click-to-call.

This mode asks the user's physical IP phone (Fanvil, Yealink, Grandstream,
Cisco/Linksys, etc. if it supports an HTTP dial/action URL) to dial the target
number itself, instead of Asterisk AMI callback ringing the user first.

PHONE_CONTROL_MAP supports many devices:
  219=192.168.30.49,204=192.168.30.50
  {"219":"192.168.30.49","204":"192.168.30.50"}

For mixed phone models you can override credentials/template per extension:
  {"219":{"ip":"192.168.30.49","template":"{scheme}://{ip}/cgi-bin/ConfigManApp.com?key={to}"},
   "204":{"ip":"192.168.30.50","username":"admin","password":"secret","template":"{scheme}://{ip}/custom?number={to}"}}

Legacy FANVIL_* environment variables are still supported as aliases so existing
installations do not break.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import socket
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import settings

logger = logging.getLogger("corporate-chat")
_digits_re = re.compile(r"\D+")
_ip_re = re.compile(r"(?<!\d)(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}(?!\d)")
_asterisk_contact_cache: dict[str, tuple[float, PhoneDevice]] = {}


def _first(*values: object, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _first_bool(*values: object, default: bool = False) -> bool:
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "y", "on", "да"):
            return True
        if text in ("0", "false", "no", "n", "off", "нет"):
            return False
    return default


def _first_float(*values: object, default: float = 4.0) -> float:
    for value in values:
        if value is None or str(value).strip() == "":
            continue
        try:
            return float(value)
        except Exception:
            continue
    return default


@dataclass
class PhoneDevice:
    ip: str
    username: str = ""
    password: str = ""
    scheme: str = ""
    template: str = ""
    timeout: float = 0.0


@dataclass
class PhoneDialResult:
    ok: bool
    error: str = ""
    from_ext: str = ""
    to_ext: str = ""
    ip: str = ""
    url: str = ""
    status: int = 0
    body: str = ""

    def as_dict(self) -> dict[str, str | bool | int]:
        return {
            "ok": self.ok,
            "error": self.error,
            "from_ext": self.from_ext,
            "to_ext": self.to_ext,
            "ip": self.ip,
            "url": self.url,
            "status": self.status,
            "body": self.body[:500],
            "method": "ip_phone",
        }


def _norm_ext(value: str | None) -> str:
    return _digits_re.sub("", str(value or ""))


def _device_from_value(value: object) -> PhoneDevice | None:
    if value is None:
        return None
    if isinstance(value, dict):
        ip = _first(value.get("ip"), value.get("host"), value.get("address"))
        if not ip:
            return None
        return PhoneDevice(
            ip=ip,
            username=_first(value.get("username"), value.get("user"), value.get("login")),
            password=_first(value.get("password"), value.get("pass"), value.get("secret")),
            scheme=_first(value.get("scheme")),
            template=_first(value.get("template"), value.get("dial_url"), value.get("url")),
            timeout=_first_float(value.get("timeout"), default=0.0),
        )
    ip = str(value).strip()
    return PhoneDevice(ip=ip) if ip else None


def _parse_phone_map(raw: str) -> dict[str, PhoneDevice]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            out: dict[str, PhoneDevice] = {}
            for k, v in data.items():
                ext = _norm_ext(k)
                dev = _device_from_value(v)
                if ext and dev:
                    out[ext] = dev
            return out
    except Exception:
        pass

    # Simple CSV/semicolon/newline format for many similar phones:
    # 219=192.168.30.49,204=192.168.30.50
    out: dict[str, PhoneDevice] = {}
    for part in re.split(r"[,;\n]+", raw):
        if not part.strip() or "=" not in part:
            continue
        k, v = part.split("=", 1)
        ext = _norm_ext(k)
        dev = _device_from_value(v)
        if ext and dev:
            out[ext] = dev
    return out


def _enabled() -> bool:
    return _first_bool(
        getattr(settings, "PHONE_CONTROL_ENABLED", None),
        getattr(settings, "FANVIL_ENABLED", None),
        default=False,
    )


def _global_username() -> str:
    return _first(getattr(settings, "PHONE_CONTROL_USERNAME", ""), getattr(settings, "FANVIL_USERNAME", ""))


def _global_password() -> str:
    return _first(getattr(settings, "PHONE_CONTROL_PASSWORD", ""), getattr(settings, "FANVIL_PASSWORD", ""))


def _global_timeout() -> float:
    return _first_float(getattr(settings, "PHONE_CONTROL_TIMEOUT", None), getattr(settings, "FANVIL_TIMEOUT", None), default=4.0)


def _phone_map_raw() -> str:
    return _first(getattr(settings, "PHONE_CONTROL_MAP", ""), getattr(settings, "FANVIL_PHONE_MAP", ""))


def _ad_enabled() -> bool:
    return _first_bool(getattr(settings, "PHONE_CONTROL_AD_ENABLED", None), default=False)


def _csv_names(raw: str) -> list[str]:
    out: list[str] = []
    for part in str(raw or "").split(","):
        name = part.strip()
        if name and name not in out:
            out.append(name)
    return out


def _ad_extension_attrs() -> list[str]:
    return _csv_names(getattr(settings, "PHONE_CONTROL_AD_EXTENSION_ATTRS", "telephoneNumber,ipPhone,otherTelephone"))


def _ad_ip_attrs() -> list[str]:
    return _csv_names(getattr(settings, "PHONE_CONTROL_AD_IP_ATTRS", "ipPhone,networkAddress,description,info"))


def _global_scheme() -> str:
    return _first(getattr(settings, "PHONE_CONTROL_SCHEME", ""), getattr(settings, "FANVIL_SCHEME", ""), default="http")


def _global_dial_template() -> str:
    return _first(
        getattr(settings, "PHONE_CONTROL_DIAL_URL_TEMPLATE", ""),
        getattr(settings, "FANVIL_DIAL_URL_TEMPLATE", ""),
        default="{scheme}://{ip}/cgi-bin/ConfigManApp.com?key={to}",
    )


def _values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _attr_tokens_match(values: object, ext: str) -> bool:
    for value in _values(values):
        tokens = re.findall(r"\d+", value)
        if ext in tokens or _norm_ext(value) == ext:
            return True
    return False


def _extract_ip(values: object) -> str:
    for value in _values(values):
        m = _ip_re.search(value)
        if m:
            return m.group(0)
    return ""


def _ldap_filter_for_ext(ext: str) -> str:
    # Search broadly by extension attributes. Verification is done in Python so
    # values like "каб. 401, вн. 219" can still match extension 219 safely.
    try:
        from .ldap_auth import _escape
        q = _escape(ext)
    except Exception:
        q = ext
    parts = [f"({attr}=*{q}*)" for attr in _ad_extension_attrs()]
    return f"(&(objectCategory=person)(objectClass=user)(|{''.join(parts)}))"


def _ad_device_for_extension(ext: str) -> PhoneDevice | None:
    if not _ad_enabled() or not getattr(settings, "LDAP_ENABLED", False):
        return None
    try:
        from .ldap_auth import _norm, _open_search_connection

        conn = _open_search_connection()
        attrs = list(dict.fromkeys([*_ad_extension_attrs(), *_ad_ip_attrs()]))
        try:
            conn.search(settings.LDAP_BASE_DN, _ldap_filter_for_ext(ext), attributes=attrs, size_limit=20)
            for entry in conn.entries:
                a = entry.entry_attributes_as_dict
                if not any(_attr_tokens_match(a.get(attr), ext) for attr in _ad_extension_attrs()):
                    continue
                for attr in _ad_ip_attrs():
                    ip = _extract_ip(a.get(attr))
                    if ip:
                        logger.info("IP phone for extension %s found in AD attribute %s: %s", ext, attr, ip)
                        return PhoneDevice(ip=ip)
                logger.warning(
                    "AD user for extension %s found, but no IP address in PHONE_CONTROL_AD_IP_ATTRS=%s",
                    ext,
                    ",".join(_ad_ip_attrs()),
                )
        finally:
            try:
                conn.unbind()
            except Exception:
                pass
    except Exception as e:  # noqa: BLE001
        logger.warning("AD phone lookup failed for extension %s: %s", ext, e)
    return None


def _asterisk_contacts_enabled() -> bool:
    return _first_bool(getattr(settings, "PHONE_CONTROL_ASTERISK_CONTACTS_ENABLED", None), default=True)


def _asterisk_contact_commands() -> list[str]:
    raw = _first(getattr(settings, "PHONE_CONTROL_ASTERISK_CONTACT_COMMANDS", ""), default="pjsip show contacts;sip show peers")
    return [p.strip() for p in re.split(r"[;\n]+", raw) if p.strip()]


def _ami_read_message(sock: socket.socket, timeout: float = 5.0) -> str:
    sock.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 1024 * 1024:
            break
    return data.decode("utf-8", errors="replace")


def _ami_send(sock: socket.socket, **fields: object) -> None:
    data = "".join(f"{k}: {v}\r\n" for k, v in fields.items()) + "\r\n"
    sock.sendall(data.encode("utf-8"))


def _ami_command(command: str) -> str:
    if not getattr(settings, "AMI_ENABLED", False):
        return ""
    if not getattr(settings, "AMI_USERNAME", "") or not getattr(settings, "AMI_SECRET", ""):
        return ""
    host = str(getattr(settings, "AMI_HOST", "") or "").strip()
    port = int(getattr(settings, "AMI_PORT", 5038) or 5038)
    if not host:
        return ""

    chunks: list[str] = []
    with socket.create_connection((host, port), timeout=5) as sock:
        chunks.append(_ami_read_message(sock, timeout=5))
        _ami_send(sock, Action="Login", Username=settings.AMI_USERNAME, Secret=settings.AMI_SECRET, Events="off")
        login = _ami_read_message(sock, timeout=5)
        chunks.append(login)
        if "Response: Success" not in login:
            logger.warning("AMI login failed while looking up contacts: %s", login[:500])
            return ""
        _ami_send(sock, Action="Command", Command=command)
        sock.settimeout(1.0)
        deadline = time.time() + 6.0
        while time.time() < deadline:
            try:
                part = sock.recv(16384)
            except socket.timeout:
                continue
            if not part:
                break
            text = part.decode("utf-8", errors="replace")
            chunks.append(text)
            if "--END COMMAND--" in text:
                break
        try:
            _ami_send(sock, Action="Logoff")
        except Exception:
            pass
    return "".join(chunks)


def _contact_line_matches_ext(line: str, ext: str) -> bool:
    # Typical PJSIP contacts:
    #   Contact:  219/sip:219@192.168.30.49:5060 ... Avail
    # Typical chan_sip peers:
    #   219/219  192.168.30.49  D  Yes  Yes  5060  OK
    patterns = [
        rf"(^|\s)Contact:\s*{re.escape(ext)}/",
        rf"(^|\s){re.escape(ext)}/",
        rf"sip:{re.escape(ext)}@",
        rf"(^|\s){re.escape(ext)}\s+",
        rf"Endpoint:\s*{re.escape(ext)}(\s|$)",
        rf"Aor:\s*{re.escape(ext)}(\s|$)",
    ]
    return any(re.search(p, line, flags=re.IGNORECASE) for p in patterns)


def _extract_contact_ip(command_output: str, ext: str) -> str:
    fallback = ""
    for line in command_output.splitlines():
        ip = _extract_ip(line)
        if not ip:
            continue
        if _contact_line_matches_ext(line, ext):
            return ip
        # Weak fallback for unusual PBX output. Do not return immediately: a
        # stronger endpoint/contact match may appear in another line.
        if not fallback and re.search(rf"(?<!\d){re.escape(ext)}(?!\d)", line):
            fallback = ip
    return fallback


def _asterisk_device_for_extension(ext: str) -> PhoneDevice | None:
    if not _asterisk_contacts_enabled():
        return None
    ext = _norm_ext(ext)
    if not ext:
        return None
    cached = _asterisk_contact_cache.get(ext)
    if cached and time.time() - cached[0] < 60:
        return cached[1]
    for command in _asterisk_contact_commands():
        try:
            output = _ami_command(command)
            ip = _extract_contact_ip(output, ext)
            if ip:
                dev = PhoneDevice(ip=ip)
                _asterisk_contact_cache[ext] = (time.time(), dev)
                logger.info("IP phone for extension %s found in Asterisk contacts via '%s': %s", ext, command, ip)
                return dev
        except Exception as e:  # noqa: BLE001
            logger.warning("Asterisk contact lookup failed for extension %s command=%s: %s", ext, command, e)
    return None


def phone_device_for_extension(ext: str) -> PhoneDevice | None:
    ext = _norm_ext(ext)
    # 1) Manual map has priority for exceptions/replacements.
    dev = _parse_phone_map(_phone_map_raw()).get(ext)
    if dev:
        return dev
    # 2) If AD stores physical phone IPs, use it.
    dev = _ad_device_for_extension(ext)
    if dev:
        return dev
    # 3) Use the PBX data that already powers call history: active SIP/PJSIP
    # contacts from Asterisk AMI. This avoids maintaining a huge static map.
    return _asterisk_device_for_extension(ext)


def phone_ip_for_extension(ext: str) -> str:
    dev = phone_device_for_extension(ext)
    return dev.ip if dev else ""


def _device_username(device: PhoneDevice) -> str:
    return _first(device.username, _global_username())


def _device_password(device: PhoneDevice) -> str:
    return _first(device.password, _global_password())


def _device_scheme(device: PhoneDevice) -> str:
    return _first(device.scheme, _global_scheme(), default="http")


def _device_timeout(device: PhoneDevice) -> float:
    return device.timeout if device.timeout > 0 else _global_timeout()


def _device_templates(device: PhoneDevice) -> list[str]:
    raw = _first(device.template, _global_dial_template())
    return [t.strip() for t in raw.split(";") if t.strip()]


def _auth_header(device: PhoneDevice) -> str:
    username = _device_username(device)
    if not username:
        return ""
    token = f"{username}:{_device_password(device)}".encode("utf-8")
    return "Basic " + base64.b64encode(token).decode("ascii")


def _build_url(template: str, device: PhoneDevice, from_ext: str, to_ext: str) -> str:
    # quote target but keep +,*,# common dial chars if they appear.
    to_q = quote(str(to_ext), safe="+#*")
    return template.format(
        scheme=_device_scheme(device),
        ip=device.ip,
        from_ext=from_ext,
        to_ext=to_ext,
        to=to_q,
    )


def _http_get(url: str, device: PhoneDevice) -> tuple[int, str]:
    headers = {"User-Agent": "CorporateChat/2.0 IPPhoneDial"}
    auth = _auth_header(device)
    if auth:
        headers["Authorization"] = auth
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=_device_timeout(device)) as resp:
        body = resp.read(2048).decode("utf-8", errors="replace")
        return int(getattr(resp, "status", 200) or 200), body


def ip_phone_dial(from_ext: str, to_ext: str) -> dict[str, str | bool | int]:
    from_ext = _norm_ext(from_ext)
    to_ext = _norm_ext(to_ext)
    if not _enabled():
        return PhoneDialResult(False, "HTTP-управление IP-телефоном отключено", from_ext, to_ext).as_dict()
    if not from_ext or not to_ext:
        return PhoneDialResult(False, "Не указан номер вызывающего или вызываемого", from_ext, to_ext).as_dict()
    device = phone_device_for_extension(from_ext)
    if not device or not device.ip:
        return PhoneDialResult(False, f"IP телефона для номера {from_ext} не найден в PHONE_CONTROL_MAP", from_ext, to_ext).as_dict()

    last_error = ""
    last_status = 0
    last_body = ""
    last_url = ""
    for template in _device_templates(device):
        url = _build_url(template, device, from_ext, to_ext)
        last_url = url
        try:
            status, body = _http_get(url, device)
            last_status, last_body = status, body
            # For phone HTTP APIs, any 2xx/3xx response after auth is OK.
            if 200 <= status < 400:
                return PhoneDialResult(True, "", from_ext, to_ext, device.ip, url, status, body).as_dict()
            last_error = f"IP phone HTTP status {status}"
        except HTTPError as e:
            last_status = e.code
            try:
                last_body = e.read(2048).decode("utf-8", errors="replace")
            except Exception:
                last_body = ""
            last_error = f"IP phone HTTP {e.code}"
        except URLError as e:
            last_error = f"IP phone connection error: {e.reason}"
        except Exception as e:  # noqa: BLE001
            last_error = f"IP phone error: {e}"
        logger.warning("IP phone dial failed from=%s to=%s ip=%s url=%s error=%s", from_ext, to_ext, device.ip, last_url, last_error)

    return PhoneDialResult(False, last_error or "IP phone dial failed", from_ext, to_ext, device.ip, last_url, last_status, last_body).as_dict()


# Backward-compatible function name for old imports/tests.
def fanvil_dial(from_ext: str, to_ext: str) -> dict[str, str | bool | int]:
    return ip_phone_dial(from_ext, to_ext)
