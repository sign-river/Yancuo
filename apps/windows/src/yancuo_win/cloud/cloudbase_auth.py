"""CloudBase end-user authentication without embedding administrator secrets."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import get_secret, set_secret
from yancuo_win.infrastructure.safe_http import safe_urlopen


_MAX_AUTH_RESPONSE_BYTES = 64 * 1024
_REFRESH_EARLY_SECONDS = 120
_TOKEN_REFRESH_LOCK = threading.Lock()


@dataclass(frozen=True)
class CloudBaseSession:
    access_token: str
    refresh_token: str
    expires_at: int
    subject: str = ""

    @classmethod
    def from_json(cls, raw: str) -> "CloudBaseSession | None":
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(value, dict) or not value.get("access_token"):
            return None
        try:
            return cls(
                access_token=str(value["access_token"]),
                refresh_token=str(value.get("refresh_token") or ""),
                expires_at=int(value.get("expires_at") or 0),
                subject=str(value.get("subject") or value.get("sub") or ""),
            )
        except (TypeError, ValueError, OverflowError):
            return None

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": 1,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "subject": self.subject,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _auth_url(environment_id: str, path: str) -> str:
    environment_id = environment_id.strip()
    if (
        not 1 <= len(environment_id) <= 64
        or any(
            ch
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
            for ch in environment_id
        )
    ):
        raise DomainError("CloudBase 环境 ID 格式无效")
    return f"https://{environment_id}.api.tcloudbasegateway.com{path}"


def _request_token(environment_id: str, payload: dict[str, Any]) -> CloudBaseSession:
    request = Request(
        _auth_url(environment_id, "/auth/v1/token"),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with safe_urlopen(request, timeout=30) as response:
            raw = response.read(_MAX_AUTH_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace")[:300]
        raise DomainError(f"CloudBase 登录失败（HTTP {exc.code}）：{detail}") from exc
    except URLError as exc:
        raise DomainError(f"无法连接 CloudBase 身份服务：{exc.reason}") from exc
    if len(raw) > _MAX_AUTH_RESPONSE_BYTES:
        raise DomainError("CloudBase 身份服务响应过大")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DomainError("CloudBase 身份服务返回无效响应") from exc
    if not isinstance(result, dict) or not result.get("access_token"):
        message = result.get("error_description") or result.get("error") if isinstance(result, dict) else None
        raise DomainError(str(message or "CloudBase 身份服务未返回访问令牌"))
    try:
        expires_in = int(result.get("expires_in") or 7200)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError("CloudBase 身份服务返回无效有效期") from exc
    expires_in = min(7 * 24 * 60 * 60, max(60, expires_in))
    return CloudBaseSession(
        access_token=str(result["access_token"]),
        refresh_token=str(result.get("refresh_token") or ""),
        expires_at=int(time.time()) + expires_in,
        subject=str(result.get("sub") or ""),
    )


def sign_in_with_password(
    environment_id: str,
    username: str,
    password: str,
    credential_key: str,
) -> CloudBaseSession:
    """Authenticate a normal user; the password is never persisted."""

    username = username.strip()
    if not username or not password:
        raise DomainError("请输入 CloudBase 用户名（可使用邮箱）和密码")
    session = _request_token(
        environment_id,
        {
            "grant_type": "password",
            "client_id": environment_id.strip(),
            "username": username,
            "password": password,
        },
    )
    set_secret(credential_key, session.to_json())
    return session


def get_access_token(environment_id: str, credential_key: str) -> str:
    """Return a valid access token, rotating a stored refresh token if needed."""

    with _TOKEN_REFRESH_LOCK:
        return _get_access_token_locked(environment_id, credential_key)


def _get_access_token_locked(environment_id: str, credential_key: str) -> str:
    """Read and possibly rotate a session while holding the process refresh lock."""

    raw = get_secret(credential_key)
    if not raw:
        raise DomainError("请先在设置中登录 CloudBase 账户")
    session = CloudBaseSession.from_json(raw)
    if session is None:
        # Compatibility with manually pasted access tokens from older builds.
        if raw.lstrip().startswith(("{", "[")):
            raise DomainError("CloudBase 登录凭据已损坏，请重新登录")
        return raw
    if session.expires_at > int(time.time()) + _REFRESH_EARLY_SECONDS:
        return session.access_token
    if not session.refresh_token:
        raise DomainError("CloudBase 登录已过期，请重新登录")
    refreshed = _request_token(
        environment_id,
        {
            "grant_type": "refresh_token",
            "client_id": environment_id.strip(),
            "refresh_token": session.refresh_token,
        },
    )
    if not refreshed.refresh_token:
        refreshed = replace(refreshed, refresh_token=session.refresh_token)
    if not refreshed.subject:
        refreshed = replace(refreshed, subject=session.subject)
    set_secret(credential_key, refreshed.to_json())
    return refreshed.access_token
