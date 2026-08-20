"""Yeastar P-Series OpenAPI call control.

Uses the official Yeastar P-Series API:
  POST /openapi/v1.0/get_token
  POST /openapi/v1.0/refresh_token
  POST /openapi/v1.0/call/dial?access_token=...

This is different from the old AMI Originate callback. With auto_answer=yes,
Yeastar asks the caller extension to auto-answer and dial the callee, avoiding
manual pickup when the physical endpoint supports auto-answer.
"""
from __future__ import annotations

import json
import logging
import ssl
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import settings

logger = logging.getLogger("corporate-chat")

_token_cache: dict[str, object] = {
    "access_token": "",
    "refresh_token": "",
    "access_expires_at": 0.0,
    "refresh_expires_at": 0.0,
}


@dataclass
class YeastarResult:
    ok: bool
    error: str = ""
    from_ext: str = ""
    to_ext: str = ""
    status: int = 0
    body: str = ""
    call_id: str = ""

    def as_dict(self) -> dict[str, str | bool | int]:
        return {
            "ok": self.ok,
            "error": self.error,
            "from_ext": self.from_ext,
            "to_ext": self.to_ext,
            "status": self.status,
            "body": self.body[:1000],
            "call_id": self.call_id,
            "method": "yeastar",
        }


def _base_url() -> str:
    return str(settings.YEASTAR_BASE_URL or "").strip().rstrip("/")


def _api_path() -> str:
    return str(settings.YEASTAR_API_PATH or "openapi/v1.0").strip().strip("/")


def _url(path: str, query: dict[str, str] | None = None) -> str:
    u = f"{_base_url()}/{_api_path()}/{path.strip('/')}"
    if query:
        u += "?" + urlencode(query)
    return u


def _ssl_context():
    if str(_base_url()).lower().startswith("https") and not bool(settings.YEASTAR_VERIFY_SSL):
        return ssl._create_unverified_context()  # noqa: SLF001 - self-signed PBX certs are common on LAN
    return None


def _post_json(url: str, payload: dict[str, object]) -> tuple[int, dict[str, object], str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "CorporateChat-YeastarOpenAPI",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=float(settings.YEASTAR_TIMEOUT or 8), context=_ssl_context()) as resp:
            raw = resp.read(4096).decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = {"raw": raw}
            return int(getattr(resp, "status", 200) or 200), parsed, raw
    except HTTPError as e:
        raw = e.read(4096).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw}
        return int(e.code), parsed, raw


def _store_tokens(data: dict[str, object]) -> None:
    now = time.time()
    access_token = str(data.get("access_token") or "")
    refresh_token = str(data.get("refresh_token") or "")
    access_ttl = int(data.get("access_token_expire_time") or 1800)
    refresh_ttl = int(data.get("refresh_token_expire_time") or 86400)
    if access_token:
        _token_cache["access_token"] = access_token
        # Keep 60 seconds safety margin.
        _token_cache["access_expires_at"] = now + max(60, access_ttl - 60)
    if refresh_token:
        _token_cache["refresh_token"] = refresh_token
        _token_cache["refresh_expires_at"] = now + max(60, refresh_ttl - 60)


def _get_token(force_new: bool = False) -> tuple[bool, str, str]:
    if not settings.YEASTAR_ENABLED:
        return False, "", "Yeastar OpenAPI disabled"
    if not _base_url():
        return False, "", "YEASTAR_BASE_URL is empty"
    if not settings.YEASTAR_USERNAME or not settings.YEASTAR_PASSWORD:
        return False, "", "YEASTAR_USERNAME/YEASTAR_PASSWORD are not configured"

    now = time.time()
    if not force_new and _token_cache.get("access_token") and float(_token_cache.get("access_expires_at") or 0) > now:
        return True, str(_token_cache["access_token"]), ""

    # Try refresh first if available.
    if not force_new and _token_cache.get("refresh_token") and float(_token_cache.get("refresh_expires_at") or 0) > now:
        status, data, raw = _post_json(_url("refresh_token"), {"refresh_token": _token_cache["refresh_token"]})
        # errcode=0 is success. Do not use `or -1` here: 0 is falsy in Python.
        if status == 200 and int(data.get("errcode", -1)) == 0 and data.get("access_token"):
            _store_tokens(data)
            return True, str(_token_cache["access_token"]), ""
        logger.warning("Yeastar refresh_token failed status=%s body=%s", status, raw[:500])

    status, data, raw = _post_json(_url("get_token"), {
        "username": settings.YEASTAR_USERNAME,
        "password": settings.YEASTAR_PASSWORD,
    })
    # errcode=0 is success. Do not use `or -1` here: 0 is falsy in Python.
    if status == 200 and int(data.get("errcode", -1)) == 0 and data.get("access_token"):
        _store_tokens(data)
        return True, str(_token_cache["access_token"]), ""
    return False, "", f"Yeastar get_token failed HTTP {status}: {raw[:500]}"


def yeastar_dial(from_ext: str, to_ext: str) -> dict[str, str | bool | int]:
    from_ext = "".join(ch for ch in str(from_ext or "") if ch.isdigit())
    to_ext = "".join(ch for ch in str(to_ext or "") if ch.isdigit())
    if not from_ext or not to_ext:
        return YeastarResult(False, "Не указан номер вызывающего или вызываемого", from_ext, to_ext).as_dict()

    ok, token, err = _get_token()
    if not ok:
        return YeastarResult(False, err, from_ext, to_ext).as_dict()

    payload: dict[str, object] = {
        "caller": to_ext,
        "callee": from_ext,
    }
    #auto_answer = str(settings.YEASTAR_AUTO_ANSWER or "").strip().lower()
    #if auto_answer in ("yes", "no"):
    #    payload["auto_answer"] = auto_answer
    #dial_permission = str(settings.YEASTAR_DIAL_PERMISSION or "").strip()
    #if dial_permission:
    #    payload["dial_permission"] = dial_permission

    def call_with_token(access_token: str) -> tuple[int, dict[str, object], str]:
        return _post_json(_url("call/dial", {"access_token": access_token}), payload)

    status, data, raw = call_with_token(token)
    # If token expired unexpectedly, force new token and retry once.
    if status in (401, 403) or int(data.get("errcode") or 0) in (10004, 10005, 10006):
        ok, token, err = _get_token(force_new=True)
        if ok:
            status, data, raw = call_with_token(token)

    # errcode=0 is success. Do not use `or -1` here: 0 is falsy in Python.
    errcode = int(data.get("errcode", -1))
    call_id = str(data.get("call_id") or "")
    if status == 200 and errcode == 0:
        return YeastarResult(True, "", from_ext, to_ext, status, raw, call_id).as_dict()
    errmsg = str(data.get("errmsg") or "FAILURE")
    return YeastarResult(False, f"Yeastar call/dial failed HTTP {status}, errcode={errcode}, errmsg={errmsg}", from_ext, to_ext, status, raw, call_id).as_dict()
