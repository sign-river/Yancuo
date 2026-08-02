"""GitLink 适配器单元测试（不打外网；对照成熟 Release+Attachment 模式）。"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from yancuo_win.cloud.gitlink import GitLinkProvider, _find_attachment_id
from yancuo_win.cloud.base import RemoteRelease
from yancuo_win.domain.rules import DomainError


def test_find_attachment_id_nested() -> None:
    assert _find_attachment_id({"id": 42}) == "42"
    assert _find_attachment_id({"data": {"attachment": {"id": "abc"}}}) == "abc"


def test_assets_first_capability() -> None:
    p = GitLinkProvider(token="dummy-token-for-unit-test")
    caps = p.get_capabilities()
    assert caps.release_assets is True
    assert caps.assets_first is True


def test_upload_then_create_stages_attachment_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = GitLinkProvider(token="unit-test-token")
    uploaded: list[str] = []

    def fake_upload(file_path: Path, *, filename: str | None = None) -> str:
        uploaded.append(filename or file_path.name)
        return "att-99"

    posts: list[dict[str, Any]] = []

    def fake_json(method: str, path: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if method == "GET" and path.endswith("releases.json?page=1&limit=100"):
            return {"releases": []}
        if method == "POST" and path.endswith("/releases.json"):
            assert payload is not None
            posts.append(payload)
            return {"ok": True, "tag_name": payload["tag_name"]}
        raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setattr(p, "upload_attachment", fake_upload)
    monkeypatch.setattr(p, "_json_request", fake_json)

    pack = tmp_path / "pack.bin"
    pack.write_bytes(b"ebpack-bytes")
    info = p.upload_release_asset(
        "owner", "repo", tag="data-v1-snapshot-1", file_path=pack, asset_name="snapshot.ebpack"
    )
    assert info["attachment_id"] == "att-99"
    assert uploaded == ["snapshot.ebpack"]

    rel = p.create_release(
        "owner", "repo", tag="data-v1-snapshot-1", name="备份", body="{}"
    )
    assert rel.tag == "data-v1-snapshot-1"
    assert posts[0]["attachment_ids"] == ["att-99"]
    assert posts[0]["tag_name"] == "data-v1-snapshot-1"


def test_update_uses_version_id(monkeypatch: pytest.MonkeyPatch) -> None:
    p = GitLinkProvider(token="unit-test-token")
    calls: list[tuple[str, str]] = []

    def fake_json(method: str, path: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        if method == "GET":
            return {
                "releases": [
                    {
                        "id": 7,
                        "version_id": 2318,
                        "tag_name": "yancuo-latest",
                        "attachments": [],
                    }
                ]
            }
        if method == "PUT":
            assert path.endswith("/releases/2318.json")
            assert payload is not None
            assert payload["tag_name"] == "yancuo-latest"
            return {"ok": True}
        raise AssertionError(method)

    monkeypatch.setattr(p, "_json_request", fake_json)
    p.write_sync_manifest("owner", "repo", {"tag": "data-v1-snapshot-x", "sha256": "abc"})
    assert any(m == "PUT" and path.endswith("2318.json") for m, path in calls)


def test_incremental_lock_is_explicitly_unsupported() -> None:
    provider = GitLinkProvider(token="unit-test-token")

    with pytest.raises(DomainError, match="不能用于增量同步锁"):
        provider.acquire_lock("owner", "repo", "device")


def test_upload_rejects_path_like_asset_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = GitLinkProvider(token="unit-test-token")
    source = tmp_path / "snapshot.ebpack"
    source.write_bytes(b"snapshot")
    monkeypatch.setattr(
        provider,
        "upload_attachment",
        lambda *_args, **_kwargs: pytest.fail("unsafe name reached upload"),
    )

    with pytest.raises(DomainError, match="不安全路径"):
        provider.upload_release_asset(
            "owner",
            "repo",
            tag="backup",
            file_path=source,
            asset_name="../outside.ebpack",
        )
    assert not (tmp_path.parent / "outside.ebpack").exists()


def test_public_release_response_size_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GitLinkProvider(token="unit-test-token")
    monkeypatch.setattr(
        provider, "_json_request", lambda *_args, **_kwargs: {"releases": []}
    )
    monkeypatch.setattr("yancuo_win.cloud.gitlink._MAX_PUBLIC_RESPONSE_BYTES", 16)
    monkeypatch.setattr(
        "yancuo_win.cloud.gitlink.safe_urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"{" + b"x" * 32 + b"}"),
    )

    with pytest.raises(DomainError, match="响应过大"):
        provider.list_releases("owner", "repo")


def test_download_enforces_actual_asset_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = GitLinkProvider(token="unit-test-token")
    monkeypatch.setattr("yancuo_win.cloud.gitlink._MAX_ASSET_BYTES", 4)
    monkeypatch.setattr(
        provider,
        "list_releases",
        lambda *_args: [
            RemoteRelease(
                tag="backup",
                name="backup",
                assets=[
                    {
                        "name": "snapshot.ebpack",
                        "url": "https://www.gitlink.org.cn/attachments/1",
                    }
                ],
            )
        ],
    )
    monkeypatch.setattr(
        "yancuo_win.cloud.gitlink.safe_urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"oversized"),
    )
    destination = tmp_path / "download.ebpack"

    with pytest.raises(DomainError, match="实际下载大小超限"):
        provider.download_release_asset(
            "owner",
            "repo",
            tag="backup",
            asset_name="snapshot.ebpack",
            dest=destination,
        )
    assert not destination.exists()
