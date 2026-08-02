from __future__ import annotations

import io
import json
import time

import pytest

from yancuo_win.cloud.cloudbase_auth import (
    CloudBaseSession,
    get_access_token,
    sign_in_with_password,
)
from yancuo_win.domain.rules import DomainError


def test_password_login_saves_session_but_not_password(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    saved = {}
    seen = {}

    def fake_open(request, timeout):  # type: ignore[no-untyped-def]
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        return io.BytesIO(
            json.dumps(
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 7200,
                    "sub": "user-1",
                }
            ).encode()
        )

    monkeypatch.setattr("yancuo_win.cloud.cloudbase_auth.safe_urlopen", fake_open)
    monkeypatch.setattr(
        "yancuo_win.cloud.cloudbase_auth.set_secret",
        lambda key, value: saved.update({key: value}),
    )
    session = sign_in_with_password("env-123", "user@example.com", "secret", "cred")

    assert session.subject == "user-1"
    assert seen["url"] == "https://env-123.api.tcloudbasegateway.com/auth/v1/token"
    assert seen["body"]["grant_type"] == "password"
    assert "secret" not in saved["cred"]
    assert CloudBaseSession.from_json(saved["cred"]).refresh_token == "refresh"


def test_access_token_refresh_rotates_stored_session(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    expired = CloudBaseSession("old", "rotate-me", int(time.time()) - 1, "user-1")
    saved = {}
    monkeypatch.setattr(
        "yancuo_win.cloud.cloudbase_auth.get_secret", lambda _key: expired.to_json()
    )
    monkeypatch.setattr(
        "yancuo_win.cloud.cloudbase_auth.set_secret",
        lambda key, value: saved.update({key: value}),
    )
    monkeypatch.setattr(
        "yancuo_win.cloud.cloudbase_auth.safe_urlopen",
        lambda request, timeout: io.BytesIO(
            json.dumps(
                {
                    "access_token": "new",
                    "refresh_token": "new-refresh",
                    "expires_in": 7200,
                    "sub": "user-1",
                }
            ).encode()
        ),
    )

    assert get_access_token("env-123", "cred") == "new"
    assert CloudBaseSession.from_json(saved["cred"]).refresh_token == "new-refresh"


def test_access_token_accepts_legacy_raw_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("yancuo_win.cloud.cloudbase_auth.get_secret", lambda _key: "legacy")
    assert get_access_token("env-123", "cred") == "legacy"


def test_auth_rejects_invalid_environment_id() -> None:
    with pytest.raises(DomainError, match="环境 ID"):
        sign_in_with_password("https://bad", "user", "password", "cred")


def test_saved_session_with_invalid_expiry_is_rejected() -> None:
    assert CloudBaseSession.from_json(
        '{"access_token":"token","expires_at":"not-a-number"}'
    ) is None


def test_corrupted_structured_session_is_not_used_as_legacy_token(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "yancuo_win.cloud.cloudbase_auth.get_secret",
        lambda _key: '{"access_token":"token","expires_at":"bad"}',
    )
    with pytest.raises(DomainError, match="凭据已损坏"):
        get_access_token("env-123", "cred")


def test_auth_rejects_invalid_expiry_from_service(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "yancuo_win.cloud.cloudbase_auth.safe_urlopen",
        lambda *_args, **_kwargs: io.BytesIO(
            b'{"access_token":"token","expires_in":"not-a-number"}'
        ),
    )
    with pytest.raises(DomainError, match="有效期"):
        sign_in_with_password("env-123", "user", "password", "cred")
