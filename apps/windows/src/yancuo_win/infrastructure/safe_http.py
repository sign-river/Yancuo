"""HTTPS opening helpers that keep credentials inside their intended origin."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
        "api-key",
    }
)


_LOCAL_PROXY_PORTS = (7897, 7890, 1080, 8888, 8080)


def _local_proxy_candidates() -> Iterator[str]:
    """Yield reachable local HTTP proxy URLs (Clash/mihomo and similar).

    TUN/VPN clients often intercept all traffic at the network layer.  When a
    direct connection fails, retrying through the local mixed port lets the
    proxy apply its own routing rules (domestic DIRECT) without requiring the
    user to disable their proxy/VPN.
    """
    for port in _LOCAL_PROXY_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                yield f"http://127.0.0.1:{port}"
        except OSError:
            continue


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
    try:
        return opener.open(target, timeout=timeout)
    except HTTPError:
        raise
    except (URLError, OSError, TimeoutError) as direct_error:
        # Local VPN/TUN (Clash/mihomo, ...) can break direct HTTPS at the
        # network layer.  Fall back to the local mixed proxy port so users
        # do not have to disable their proxy for this app.
        for proxy in _local_proxy_candidates():
            proxy_opener = build_opener(
                SafeHTTPSRedirectHandler(allow_cross_origin=allow_cross_origin),
                ProxyHandler({"http": proxy, "https": proxy}),
            )
            try:
                return proxy_opener.open(target, timeout=timeout)
            except HTTPError:
                raise
            except (URLError, OSError, TimeoutError):
                continue
        raise direct_error


def iter_file_chunks(path: Path, *, chunk_size: int = 1024 * 1024):
    """Yield a file body without allocating the complete upload in memory."""

    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            yield chunk
