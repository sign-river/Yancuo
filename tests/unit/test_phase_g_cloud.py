"""阶段 G：LocalFolder 云备份全流程与 latest 指针安全。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.cloud_service import CloudBackupService
from yancuo_win.application.services import AppServices
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import mask_secret


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return bootstrap_runtime()


def test_local_folder_upload_latest_restore(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    pid = services.create_problem(title="云备份题").id
    services.update_problem(pid, {"question_markdown": "云端往返内容"})

    cloud_root = tmp_path / "cloud_root"
    provider = LocalFolderProvider(cloud_root)
    cloud = CloudBackupService(runtime, provider)
    # 覆盖为测试仓库名
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "test-repo"
    runtime.settings.cloud.enabled = True

    cloud.ensure_repository()
    uploaded = cloud.upload_backup()
    assert uploaded["tag"].startswith("data-v1-snapshot-")
    assert len(uploaded["sha256"]) == 64

    latest_path = cloud_root / "local" / "test-repo" / ".mistakebook" / "latest.json"
    assert latest_path.is_file()
    asset = (
        cloud_root
        / "local"
        / "test-repo"
        / "releases"
        / uploaded["tag"]
        / "snapshot.ebpack"
    )
    assert asset.is_file()

    backups = cloud.list_backups()
    assert any(b["tag"] == uploaded["tag"] and b["is_latest"] for b in backups)

    target = tmp_path / "restored_from_cloud"
    result = cloud.restore_latest_to(target)
    assert (target / "error_book.db").is_file()
    assert result["schema_version"] >= 1


def test_failed_upload_does_not_update_latest(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    services.create_problem(title="中断上传")
    cloud_root = tmp_path / "cloud_fail"
    provider = LocalFolderProvider(cloud_root)
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "fail-repo"
    runtime.settings.cloud.enabled = True
    cloud = CloudBackupService(runtime, provider)
    cloud.ensure_repository()

    def boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise DomainError("模拟上传中断")

    provider.upload_release_asset = boom  # type: ignore[method-assign]

    with pytest.raises(DomainError, match="中断"):
        cloud.upload_backup()

    latest = provider.read_sync_manifest("local", "fail-repo")
    assert latest is None
    assert not (cloud_root / "local" / "fail-repo" / "locks" / "primary.json").exists()


def test_mask_secret_never_full() -> None:
    secret = "abcdefghijklmnop"
    masked = mask_secret(secret)
    assert secret not in masked
    assert "…" in masked or "****" in masked
    assert mask_secret(None) == "（未配置）"
