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
from yancuo_win.data.models import Problem
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
    assert uploaded["tag"].startswith(f"data-v1-{runtime.identity.profile_id}-")
    assert uploaded["profile_id"] == runtime.identity.profile_id
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

    latest = provider.read_sync_manifest("local", "test-repo")
    assert latest is not None
    profile_snapshot = latest["profiles"][runtime.identity.profile_id]
    assert profile_snapshot["tag"] == uploaded["tag"]
    assert profile_snapshot["parent_snapshot_id"] is None

    backups = cloud.list_backups()
    assert any(b["tag"] == uploaded["tag"] and b["is_latest"] for b in backups)

    target = tmp_path / "restored_from_cloud"
    result = cloud.restore_latest_to(target)
    assert (target / "error_book.db").is_file()
    assert result["schema_version"] >= 1


def test_profiles_are_discovered_and_explicitly_bound(runtime, tmp_path: Path, monkeypatch) -> None:
    cloud_root = tmp_path / "profile-cloud"
    provider = LocalFolderProvider(cloud_root)
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "profiles"
    runtime.settings.cloud.enabled = True
    first = CloudBackupService(runtime, provider)
    first.ensure_repository()
    uploaded_first = first.upload_backup()

    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "second-data"))
    second_runtime = bootstrap_runtime()
    second_runtime.settings.cloud.repository.owner = "local"
    second_runtime.settings.cloud.repository.name = "profiles"
    second_runtime.settings.cloud.enabled = True
    second = CloudBackupService(second_runtime, LocalFolderProvider(cloud_root))
    uploaded_second = second.upload_backup()

    state = second.profile_connection_state()
    assert state["requires_takeover"] is True
    assert {item["profile_id"] for item in state["remote_profiles"]} == {
        uploaded_first["profile_id"],
        uploaded_second["profile_id"],
    }

    restored = first.restore_profile_to(
        uploaded_second["profile_id"], tmp_path / "restored-second-profile"
    )
    restored_identity = Path(restored["target_root"]) / "identity.json"
    assert uploaded_second["profile_id"] in restored_identity.read_text(encoding="utf-8")

    second.record_profile_alias(uploaded_second["profile_id"], uploaded_first["profile_id"])
    bound = second.bind_local_profile(uploaded_second["profile_id"])
    assert bound["previous_profile_id"] == uploaded_second["profile_id"]
    assert bound["profile_id"] == uploaded_first["profile_id"]
    assert second_runtime.identity.profile_id == uploaded_first["profile_id"]


def test_same_profile_branch_blocks_upload_until_user_resolves(runtime, tmp_path: Path, monkeypatch) -> None:
    cloud_root = tmp_path / "branch-cloud"
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "branches"
    runtime.settings.cloud.enabled = True
    first = CloudBackupService(runtime, LocalFolderProvider(cloud_root))
    first.ensure_repository()
    initial = first.upload_backup()

    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "branch-second"))
    second_runtime = bootstrap_runtime()
    second_runtime.settings.cloud.repository.owner = "local"
    second_runtime.settings.cloud.repository.name = "branches"
    second_runtime.settings.cloud.enabled = True
    second = CloudBackupService(second_runtime, LocalFolderProvider(cloud_root))
    second.bind_local_profile(initial["profile_id"])

    first.upload_backup()
    state = second.profile_connection_state()
    assert state["branch_detected"] is True
    with pytest.raises(DomainError, match="其他设备更新"):
        second.upload_backup()


def test_profile_merge_preview_reports_new_rows_and_conflicts(runtime, tmp_path: Path, monkeypatch) -> None:
    services = AppServices(runtime)
    problem = services.create_problem(title="remote problem", question_markdown="before")
    cloud_root = tmp_path / "merge-preview-cloud"
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "merge-preview"
    runtime.settings.cloud.enabled = True
    first = CloudBackupService(runtime, LocalFolderProvider(cloud_root))
    first.ensure_repository()
    uploaded = first.upload_backup()

    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "merge-preview-local"))
    second_runtime = bootstrap_runtime()
    second_runtime.settings.cloud.repository.owner = "local"
    second_runtime.settings.cloud.repository.name = "merge-preview"
    second_runtime.settings.cloud.enabled = True
    second = CloudBackupService(second_runtime, LocalFolderProvider(cloud_root))

    preview = second.preview_profile_merge(uploaded["profile_id"])
    assert preview["write_performed"] is False
    assert preview["tables"]["problems"]["new_remote"] == 1
    assert preview["has_conflicts"] is False

    with second_runtime.session_factory() as session:
        session.add(
            Problem(
                id=problem.id,
                title="local conflict",
                question_markdown="after",
                status="inbox",
                revision=1,
            )
        )
        session.commit()
    conflict_preview = second.preview_profile_merge(uploaded["profile_id"])
    assert conflict_preview["has_conflicts"] is True
    assert conflict_preview["tables"]["problems"]["conflicts"] == 1


def test_explicit_profile_merge_keeps_local_by_default_and_records_alias(
    runtime, tmp_path: Path, monkeypatch
) -> None:
    services = AppServices(runtime)
    shared = services.create_problem(title="remote title", question_markdown="remote question")
    remote_only = services.create_problem(title="remote only", question_markdown="remote only question")
    cloud_root = tmp_path / "merge-cloud"
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "merge"
    runtime.settings.cloud.enabled = True
    first = CloudBackupService(runtime, LocalFolderProvider(cloud_root))
    first.ensure_repository()
    uploaded = first.upload_backup()

    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "merge-local"))
    second_runtime = bootstrap_runtime()
    second_runtime.settings.cloud.repository.owner = "local"
    second_runtime.settings.cloud.repository.name = "merge"
    second_runtime.settings.cloud.enabled = True
    second = CloudBackupService(second_runtime, LocalFolderProvider(cloud_root))
    with second_runtime.session_factory() as session:
        session.add(
            Problem(
                id=shared.id,
                title="local title",
                question_markdown="local question",
                status="inbox",
                revision=1,
            )
        )
        session.commit()

    preview = second.preview_profile_merge(uploaded["profile_id"])
    fields = preview["tables"]["problems"]["conflict_fields"][shared.id]
    assert "question_markdown" in fields
    result = second.merge_profile(
        uploaded["profile_id"],
        primary_profile_id=second_runtime.identity.profile_id,
        field_choices={f"problems:{shared.id}:question_markdown": "remote"},
    )
    assert result["write_performed"] is True
    assert result["inserted_rows"] >= 1
    assert result["remote_fields_applied"] == 1
    with second_runtime.session_factory() as session:
        merged = session.get(Problem, shared.id)
        assert merged is not None
        assert merged.title == "local title"
        assert merged.question_markdown == "remote question"
        assert session.get(Problem, remote_only.id) is not None

    latest = second.provider.read_sync_manifest("local", "merge")
    assert latest["aliases"][uploaded["profile_id"]] == second_runtime.identity.profile_id


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
