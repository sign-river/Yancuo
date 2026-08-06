"""safe_http proxy-fallback unit tests."""

from __future__ import annotations

import pytest
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request

from yancuo_win.infrastructure.safe_http import (
    _local_proxy_candidates,
    safe_urlopen,
)


class _FakeOpener:
    def __init__(self, behavior):
        self._behavior = behavior

    def open(self, target, timeout=None):
        return self._behavior(target, timeout)


def _raise(exc):
    def _inner(_target, _timeout):
        raise exc
    return _inner


def test_local_proxy_candidates_skips_dead_ports(monkeypatch):
    import socket as _socket

    calls = []

    def fake_connect(address, timeout=0.4):
        calls.append((address, timeout))
        raise OSError("refused")

    monkeypatch.setattr(_socket, "create_connection", fake_connect)
    assert list(_local_proxy_candidates()) == []
    assert len(calls) == 5


def test_direct_success_does_not_touch_proxy(monkeypatch):
    marker = object()
    seen = []

    def fake_build_opener(*handlers):
        seen.append(handlers)
        return _FakeOpener(lambda _t, _to: marker)

    monkeypatch.setattr("yancuo_win.infrastructure.safe_http.build_opener", fake_build_opener)
    result = safe_urlopen(Request("https://example.test"), timeout=5)
    assert result is marker
    assert len(seen) == 1


def test_direct_failure_falls_back_to_local_proxy(monkeypatch):
    marker = object()
    seen = []
    calls = {"direct": 0, "proxy": 0}

    def fake_build_opener(*handlers):
        seen.append(handlers)
        has_proxy = any(isinstance(h, ProxyHandler) for h in handlers)
        if not has_proxy:
            calls["direct"] += 1
            return _FakeOpener(_raise(URLError("broken pipe")))
        calls["proxy"] += 1
        return _FakeOpener(lambda _t, _to: marker)

    monkeypatch.setattr("yancuo_win.infrastructure.safe_http.build_opener", fake_build_opener)
    monkeypatch.setattr(
        "yancuo_win.infrastructure.safe_http._local_proxy_candidates",
        lambda: ["http://127.0.0.1:7897"],
    )
    result = safe_urlopen(Request("https://example.test"), timeout=5)
    assert result is marker
    assert calls == {"direct": 1, "proxy": 1}
    assert isinstance(seen[1][1], ProxyHandler)


def test_http_error_is_not_retried(monkeypatch):
    seen = []

    def fake_build_opener(*handlers):
        seen.append(handlers)
        return _FakeOpener(_raise(HTTPError("https://example.test", 502, "bad", None, None)))

    monkeypatch.setattr("yancuo_win.infrastructure.safe_http.build_opener", fake_build_opener)
    monkeypatch.setattr(
        "yancuo_win.infrastructure.safe_http._local_proxy_candidates",
        lambda: ["http://127.0.0.1:7897"],
    )
    with pytest.raises(HTTPError):
        safe_urlopen(Request("https://example.test"), timeout=5)
    assert len(seen) == 1


def test_all_proxies_fail_raises_direct_error(monkeypatch):
    def fake_build_opener(*handlers):
        has_proxy = any(isinstance(h, ProxyHandler) for h in handlers)
        return _FakeOpener(_raise(URLError("still broken") if has_proxy else URLError("original")))

    monkeypatch.setattr("yancuo_win.infrastructure.safe_http.build_opener", fake_build_opener)
    monkeypatch.setattr(
        "yancuo_win.infrastructure.safe_http._local_proxy_candidates",
        lambda: ["http://127.0.0.1:7897", "http://127.0.0.1:7890"],
    )
    with pytest.raises(URLError) as exc_info:
        safe_urlopen(Request("https://example.test"), timeout=5)
    assert "original" in str(exc_info.value.reason)
