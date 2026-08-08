"""云端网关韧性：网络错误自动重试与友好提示文案。"""

from __future__ import annotations

from unittest.mock import patch
from urllib.error import URLError

import pytest

from yancuo_win.cloud.cloudbase import CloudBaseGatewayProvider
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.widgets import friendly_cloud_error


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, limit: int = -1) -> bytes:
        return self._payload


class _FakeCtx:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> _FakeResponse:
        return self._response

    def __exit__(self, *_args: object) -> bool:
        return False


def _provider() -> CloudBaseGatewayProvider:
    return CloudBaseGatewayProvider(
        environment_id="yancuo-prod-d5g5k97ox5bd6083e",
        gateway_url="https://example.invalid/gateway",
        credential_key="test-credential",
    )


def test_friendly_cloud_error_network_timeout() -> None:
    assert friendly_cloud_error("timed out") == "连接云端超时，请检查网络后重试"
    assert (
        friendly_cloud_error("<urlopen error timed out>")
        == "连接云端超时，请检查网络后重试"
    )


def test_friendly_cloud_error_dns_and_login() -> None:
    assert "无法解析云端服务地址" in friendly_cloud_error(
        "[Errno 11001] getaddrinfo failed"
    )
    assert friendly_cloud_error("HTTP 401 登录已失效") == "登录已失效，请重新登录后重试"


def test_cloudbase_action_retries_network_error_once() -> None:
    provider = _provider()
    payload = b'{"ok": true, "data": {"hello": 1}}'
    with (
        patch(
            "yancuo_win.cloud.cloudbase.safe_urlopen",
            side_effect=[URLError(TimeoutError("timed out")), _FakeCtx(_FakeResponse(payload))],
        ) as opener,
        patch(
            "yancuo_win.cloud.cloudbase.force_refresh_token",
            return_value="fresh-token",
        ) as refresh,
        patch(
            "yancuo_win.cloud.cloudbase.get_access_token",
            return_value="access-token",
        ),
    ):
        result = provider._action("health")

    assert result == {"hello": 1}
    assert opener.call_count == 2
    refresh.assert_called_once()


def test_cloudbase_action_network_error_still_fails_friendly() -> None:
    provider = _provider()
    with (
        patch(
            "yancuo_win.cloud.cloudbase.safe_urlopen",
            side_effect=[URLError(TimeoutError("timed out")), URLError(TimeoutError("timed out"))],
        ),
        patch(
            "yancuo_win.cloud.cloudbase.force_refresh_token",
            side_effect=DomainError("登录已失效，请重新登录"),
        ),
        patch(
            "yancuo_win.cloud.cloudbase.get_access_token",
            return_value="access-token",
        ),
    ):
        with pytest.raises(DomainError, match="已自动重试一次"):
            provider._action("health")
