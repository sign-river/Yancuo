"""阶段 H：GitHubProvider 单元测试（不打外网）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.cloud.github import GitHubProvider
from yancuo_win.config.settings import load_settings, default_toml_path
from yancuo_win.domain.rules import DomainError


def test_factory_returns_github(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(default_toml_path())
    settings.cloud.default_provider = "github"
    provider = get_cloud_provider(settings)
    assert provider.name == "github"
    assert provider.get_capabilities().release_assets is True
    assert provider.get_capabilities().assets_first is False


def test_create_then_upload_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = GitHubProvider(token="ghp_unit_test_token")
    calls: list[tuple[str, str]] = []

    def fake_request(
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        expect_json: bool = True,
        raw_body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        calls.append((method, url))
        if method == "GET" and "/releases/tags/" in url:
            raise DomainError("GitHub 资源不存在（404）：not found")
        if method == "POST" and url.endswith("/releases"):
            return {
                "id": 1001,
                "tag_name": body["tag_name"] if body else "",
                "name": body.get("name") if body else "",
                "upload_url": "https://uploads.github.com/repos/o/r/releases/1001/assets{?name,label}",
                "assets": [],
            }
        if method == "GET" and url.endswith("/releases/1001"):
            return {"id": 1001, "assets": []}
        if method == "POST" and "uploads.github.com" in url:
            assert raw_body == b"pack-bytes"
            assert content_type == "application/octet-stream"
            return {
                "id": 55,
                "name": "snapshot.ebpack",
                "size": 10,
                "browser_download_url": "https://example/snapshot.ebpack",
            }
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(p, "_request_json", fake_request)
    pack = tmp_path / "x.ebpack"
    pack.write_bytes(b"pack-bytes")

    rel = p.create_release("owner", "repo", tag="data-v1-snapshot-1", name="备份", body="{}")
    assert rel.tag == "data-v1-snapshot-1"
    info = p.upload_release_asset(
        "owner",
        "repo",
        tag="data-v1-snapshot-1",
        file_path=pack,
        asset_name="snapshot.ebpack",
    )
    assert info["name"] == "snapshot.ebpack"
    assert any(m == "POST" and u.endswith("/releases") for m, u in calls)
    assert any("uploads.github.com" in u for _, u in calls)


def test_write_latest_uses_pointer_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    p = GitHubProvider(token="ghp_unit_test_token")
    created: list[str] = []

    def fake_create(owner: str, repo: str, *, tag: str, name: str, body: str = "") -> Any:
        created.append(tag)
        from yancuo_win.cloud.base import RemoteRelease

        return RemoteRelease(tag=tag, name=name, assets=[], raw={"body": body})

    monkeypatch.setattr(p, "create_release", fake_create)
    p.write_sync_manifest("o", "r", {"tag": "data-v1-snapshot-x", "sha256": "abc"})
    assert created == ["yancuo-latest"]
