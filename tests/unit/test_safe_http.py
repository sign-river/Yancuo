"""Credential-safe HTTPS redirect behavior."""

from __future__ import annotations

from urllib.error import HTTPError
from urllib.request import Request

import pytest

from yancuo_win.infrastructure.safe_http import SafeHTTPSRedirectHandler


def _redirect(
    destination: str, *, allow_cross_origin: bool
) -> Request | None:
    request = Request(
        "https://api.example.test/start",
        headers={"Authorization": "Bearer secret", "Accept": "application/json"},
    )
    return SafeHTTPSRedirectHandler(
        allow_cross_origin=allow_cross_origin
    ).redirect_request(request, None, 302, "Found", {}, destination)


def test_same_origin_https_redirect_keeps_authorization() -> None:
    redirected = _redirect(
        "https://api.example.test/next", allow_cross_origin=False
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") == "Bearer secret"


def test_cross_origin_redirect_is_rejected_by_default() -> None:
    with pytest.raises(HTTPError, match="跨源"):
        _redirect("https://storage.example.test/object", allow_cross_origin=False)


def test_allowed_cross_origin_redirect_strips_authorization() -> None:
    redirected = _redirect(
        "https://storage.example.test/object", allow_cross_origin=True
    )

    assert redirected is not None
    assert redirected.get_header("Authorization") is None
    assert redirected.get_header("Accept") == "application/json"


def test_https_redirect_handler_rejects_downgrade() -> None:
    with pytest.raises(HTTPError, match="HTTPS"):
        _redirect("http://api.example.test/next", allow_cross_origin=True)
