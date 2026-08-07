"""云端备份管理：删除单条备份与按资料档保留最近 N 份。"""

from __future__ import annotations

from pathlib import Path

import pytest
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.cloud_service import CloudBackupService
from yancuo_win.application.services import AppServices
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.rules import DomainError


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return bootstrap_runtime()


def _cloud(runtime, tmp_path: Path) -> CloudBackupService:
    provider = LocalFolderProvider(tmp_path / "cloud_root")
    cloud = CloudBackupService(runtime, provider)
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "test-repo"
    runtime.settings.cloud.enabled = True
    cloud.ensure_repository()
    return cloud


def _seed_problem(runtime) -> None:
    services = AppServices(runtime)
    pid = services.create_problem(title="云端管理测试").id
    services.update_problem(pid, {"question_markdown": "云端数据管理"})


def test_delete_backup_removes_release_and_manifest_pointer(
    runtime, tmp_path: Path
) -> None:
    _seed_problem(runtime)
    cloud = _cloud(runtime, tmp_path)
    first = cloud.upload_backup()
    second = cloud.upload_backup()
    assert len(cloud.list_backups()) == 2

    cloud.delete_backup(second["tag"])
    rows = cloud.list_backups()
    assert [row["tag"] for row in rows] == [first["tag"]]
    assert cloud._profile_index().get("profiles", {}).get(runtime.identity.profile_id) is None

    cloud.delete_backup(first["tag"])
    assert cloud.list_backups() == []


def test_cleanup_backups_keeps_latest_n(runtime, tmp_path: Path) -> None:
    _seed_problem(runtime)
    cloud = _cloud(runtime, tmp_path)
    tags = [cloud.upload_backup()["tag"] for _ in range(4)]

    deleted = cloud.cleanup_backups(retain=2)
    assert len(deleted) == 2

    rows = cloud.list_backups()
    assert {row["tag"] for row in rows} == set(tags[2:])
    assert any(row["is_latest"] for row in rows)


def test_cleanup_backups_retain_at_least_one(runtime, tmp_path: Path) -> None:
    cloud = _cloud(runtime, tmp_path)
    with pytest.raises(DomainError):
        cloud.cleanup_backups(retain=0)
