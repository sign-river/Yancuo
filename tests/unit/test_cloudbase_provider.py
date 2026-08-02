"""CloudBase gateway adapter contract tests; no real account is required."""

from __future__ import annotations

import io
import json

import pytest

from yancuo_win.cloud.cloudbase import CloudBaseGatewayProvider
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.config.settings import AppSettings
from yancuo_win.domain.rules import DomainError


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        payload = json.dumps(self.payload).encode("utf-8")
        return payload if size < 0 else payload[:size]


def test_health_uses_gateway_token_and_environment_header(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen = {}

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["body"] = request.data
        seen["timeout"] = timeout
        return _Response({"ok": True, "data": {"healthy": True}})

    monkeypatch.setattr("yancuo_win.cloud.cloudbase.get_secret", lambda _key: "test-token")
    monkeypatch.setattr("yancuo_win.cloud.cloudbase.safe_urlopen", fake_urlopen)
    provider = CloudBaseGatewayProvider(
        environment_id="yancuo-prod-xxxxx",
        gateway_url="https://gateway.example.test/base",
        credential_key="test-key",
    )

    assert provider.test_connection()["ok"] is True
    assert seen["url"] == "https://gateway.example.test/base/actions/health"
    assert seen["headers"]["Authorization"] == "Bearer test-token"
    assert seen["headers"]["X-cloudbase-environment-id"] == "yancuo-prod-xxxxx"
    assert seen["body"] == b"{}"
    assert seen["timeout"] == 60


def test_cloudbase_provider_is_snapshot_capable() -> None:
    provider = CloudBaseGatewayProvider(
        environment_id="environment",
        gateway_url="https://gateway.example.test",
        credential_key="test-key",
    )
    capabilities = provider.get_capabilities()
    assert capabilities.release_assets is True
    assert capabilities.atomic_file_update is True


def test_factory_creates_cloudbase_provider() -> None:
    settings = AppSettings()
    settings.cloud.default_provider = "cloudbase"
    settings.cloud.cloudbase.environment_id = "yancuo-prod-xxxxx"
    settings.cloud.cloudbase.gateway_url = "https://gateway.example.test"

    provider = get_cloud_provider(settings)
    assert isinstance(provider, CloudBaseGatewayProvider)


def test_cloudbase_rejects_insecure_gateway_url() -> None:
    provider = CloudBaseGatewayProvider(
        environment_id="environment",
        gateway_url="http://gateway.example.test",
        credential_key="test-key",
    )

    with pytest.raises(DomainError, match="HTTPS"):
        provider._validate_configuration()


def test_cloudbase_gateway_response_size_is_bounded(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("yancuo_win.cloud.cloudbase.get_secret", lambda _key: "token")
    monkeypatch.setattr("yancuo_win.cloud.cloudbase._MAX_GATEWAY_RESPONSE_BYTES", 16)
    monkeypatch.setattr(
        "yancuo_win.cloud.cloudbase.safe_urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"{" + b"x" * 32 + b"}"),
    )
    provider = CloudBaseGatewayProvider(
        environment_id="environment",
        gateway_url="https://gateway.example.test",
        credential_key="test-key",
    )

    with pytest.raises(DomainError, match="响应过大"):
        provider.authenticate()


def test_cloudbase_download_cleans_oversized_partial_file(
    monkeypatch, tmp_path  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setattr("yancuo_win.cloud.cloudbase._MAX_ASSET_BYTES", 4)
    provider = CloudBaseGatewayProvider(
        environment_id="environment",
        gateway_url="https://gateway.example.test",
        credential_key="test-key",
    )
    monkeypatch.setattr(
        provider,
        "_action",
        lambda *_args, **_kwargs: {"url": "https://storage.example.test/object"},
    )
    monkeypatch.setattr(
        "yancuo_win.cloud.cloudbase.safe_urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"oversized"),
    )
    destination = tmp_path / "snapshot.ebpack"

    with pytest.raises(DomainError, match="超过"):
        provider.download_release_asset(
            "owner", "repo", tag="backup", asset_name="snapshot.ebpack", dest=destination
        )
    assert not destination.exists()
