"""HTTPS opening helpers that keep credentials inside their intended origin."""

from __future__ import annotations

from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
        "api-key",
    }
)


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    port = parsed.port
    if port is None and parsed.scheme.lower() == "https":
        port = 443
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


class SafeHTTPSRedirectHandler(HTTPRedirectHandler):
    """Reject unsafe redirects and strip credentials before allowed cross-origin GETs."""

    def __init__(self, *, allow_cross_origin: bool) -> None:
        super().__init__()
        self.allow_cross_origin = allow_cross_origin

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        destination = urlparse(redirected.full_url)
        if destination.scheme.lower() != "https" or not destination.hostname:
            raise HTTPError(newurl, code, "拒绝非 HTTPS 重定向", headers, fp)
        if _origin(req.full_url) == _origin(redirected.full_url):
            return redirected
        if not self.allow_cross_origin:
            raise HTTPError(newurl, code, "拒绝跨源重定向", headers, fp)
        for header in tuple(redirected.header_items()):
            if header[0].lower() in _SENSITIVE_HEADERS:
                redirected.remove_header(header[0])
        return redirected


def safe_urlopen(
    target: Request | str,
    *,
    timeout: float,
    allow_cross_origin: bool = False,
):
    """Open HTTPS while applying explicit redirect and credential rules."""

    opener = build_opener(
        SafeHTTPSRedirectHandler(allow_cross_origin=allow_cross_origin)
    )
    return opener.open(target, timeout=timeout)
