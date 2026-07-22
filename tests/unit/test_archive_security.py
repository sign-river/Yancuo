"""归档安全边界与 LocalFolder 写锁回归测试。"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.archive import (
    ArchiveSecurityError,
    normalize_archive_name,
    safe_extract_zip,
)


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return path


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return bootstrap_runtime()


@pytest.mark.parametrize(
    "name",
    ["../outside.txt", r"..\outside.txt", "/absolute.txt", "C:/outside.txt"],
)
def test_archive_name_rejects_path_escape(name: str) -> None:
    with pytest.raises(ArchiveSecurityError):
        normalize_archive_name(name)


def test_safe_extract_rejects_path_escape(tmp_path: Path) -> None:
    pack = _zip(tmp_path / "bad.zip", {"../outside.txt": b"nope"})
    destination = tmp_path / "out"
    with zipfile.ZipFile(pack, "r") as zf:
        with pytest.raises(ArchiveSecurityError):
            safe_extract_zip(zf, destination)
    assert not (tmp_path / "outside.txt").exists()


def test_safe_extract_enforces_size_budget(tmp_path: Path) -> None:
    pack = _zip(tmp_path / "large.zip", {"data.bin": b"0123456789"})
    with zipfile.ZipFile(pack, "r") as zf:
        with pytest.raises(ArchiveSecurityError, match="过大|超限"):
            safe_extract_zip(zf, tmp_path / "out", max_member_size=4)


def test_restore_rejects_invalid_database_without_replacing_target(
    runtime, tmp_path: Path
) -> None:
    pack = _zip(
        tmp_path / "invalid-backup.zip",
        {
            "manifest.json": json.dumps(
                {
                    "format": "yancuo-local-backup",
                    "version": 1,
                    "schema_version": 4,
                }
            ).encode(),
            "database/error_book.db": b"not a sqlite database",
            "assets/new.txt": b"new",
        },
    )
    target = tmp_path / "existing"
    target.mkdir()
    (target / "error_book.db").write_bytes(b"old database sentinel")
    (target / "assets").mkdir()
    (target / "assets" / "keep.txt").write_bytes(b"old asset sentinel")

    with pytest.raises(DomainError, match="数据库校验失败"):
        AppServices(runtime).restore_backup(pack, target)

    assert (target / "error_book.db").read_bytes() == b"old database sentinel"
    assert (target / "assets" / "keep.txt").read_bytes() == b"old asset sentinel"
    assert not (target / "assets" / "new.txt").exists()


def test_local_folder_lock_is_released_and_expired(tmp_path: Path) -> None:
    root = tmp_path / "cloud"
    first = LocalFolderProvider(root, lock_ttl_seconds=60)
    second = LocalFolderProvider(root, lock_ttl_seconds=60)
    assert first.acquire_lock("local", "repo", "dev-a")
    assert not second.acquire_lock("local", "repo", "dev-b")

    first.release_lock("local", "repo", "dev-a")
    assert second.acquire_lock("local", "repo", "dev-b")

    lock = root / "local" / "repo" / "locks" / "primary.json"
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    lock.write_text(
        json.dumps(
            {
                "device_id": "dev-stale",
                "acquired_at": old.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert first.acquire_lock("local", "repo", "dev-a")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"owner": "..", "repo": "repo"},
        {"owner": "local", "repo": "../escape"},
    ],
)
def test_local_folder_rejects_unsafe_repository_components(
    tmp_path: Path, kwargs: dict[str, str]
) -> None:
    provider = LocalFolderProvider(tmp_path / "cloud")
    with pytest.raises(DomainError):
        provider.get_repository(kwargs["owner"], kwargs["repo"])


def test_local_folder_rejects_unsafe_release_and_operation_components(
    tmp_path: Path,
) -> None:
    provider = LocalFolderProvider(tmp_path / "cloud")
    provider.create_private_repository("repo")
    with pytest.raises(DomainError):
        provider.create_release("local", "repo", tag="../escape", name="bad")
    with pytest.raises(DomainError):
        provider.append_operations("local", "repo", "../device", [{"x": 1}])
    with pytest.raises(DomainError):
        provider.write_tombstone("local", "repo", "../entity", {})

    provider.create_release("local", "repo", tag="safe-tag", name="safe")
    source = tmp_path / "asset.bin"
    source.write_bytes(b"asset")
    with pytest.raises(DomainError):
        provider.upload_release_asset(
            "local",
            "repo",
            tag="safe-tag",
            file_path=source,
            asset_name="../asset.bin",
        )
    with pytest.raises(DomainError):
        provider.download_release_asset(
            "local",
            "repo",
            tag="safe-tag",
            asset_name="../asset.bin",
            dest=tmp_path / "download.bin",
        )


def test_local_folder_skips_non_object_operation_lines(tmp_path: Path) -> None:
    root = tmp_path / "cloud"
    provider = LocalFolderProvider(root)
    provider.append_operations(
        "local", "repo", "dev-a", [{"operation_id": "op_valid", "timestamp": "1"}]
    )
    ops_file = root / "local" / "repo" / "changes" / "dev-a" / "ops.jsonl"
    with ops_file.open("a", encoding="utf-8") as stream:
        stream.write("[]\n")
        stream.write("not-json\n")

    assert provider.list_remote_operations("local", "repo") == [
        {"operation_id": "op_valid", "timestamp": "1"}
    ]
