"""阶段 J：字段合并与 LocalFolder 增量同步。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.application.sync_service import SyncService
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import ReviewItem, ReviewSession
from yancuo_win.domain.sync_merge import merge_snapshots
from sqlalchemy import select


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return bootstrap_runtime()


def test_merge_different_fields_auto() -> None:
    base = {"priority": 3, "solution_markdown": ""}
    local = {"priority": 5, "solution_markdown": ""}
    remote = {"priority": 3, "solution_markdown": "解析R"}
    merged, conflicts = merge_snapshots(base, local, remote)
    assert conflicts == []
    assert merged["priority"] == 5
    assert merged["solution_markdown"] == "解析R"


def test_merge_same_body_field_conflicts() -> None:
    base = {"solution_markdown": "旧", "priority": 3}
    local = {"solution_markdown": "本地解析", "priority": 3}
    remote = {"solution_markdown": "远端解析", "priority": 3}
    merged, conflicts = merge_snapshots(base, local, remote)
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "solution_markdown"
    assert merged["solution_markdown"] == "本地解析"


def test_merge_tags_union() -> None:
    base = {"tags": ["a"]}
    local = {"tags": ["a", "b"]}
    remote = {"tags": ["a", "c"]}
    merged, conflicts = merge_snapshots(base, local, remote)
    assert conflicts == []
    assert merged["tags"] == ["a", "b", "c"]


def test_local_folder_push_pull_auto_merge(runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cloud_root = tmp_path / "cloud"
    provider = LocalFolderProvider(cloud_root)
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "sync-repo"
    runtime.settings.sync.create_snapshot_before_merge = False

    services = AppServices(runtime)
    pid = services.create_problem(title="同步题").id
    services.update_problem(pid, {"priority": 5})

    sync_a = SyncService(runtime, provider)
    assert sync_a.push_operations()["pushed"] >= 1

    # 第二设备：独立数据根，导入同题后改解析（模拟：直接在 B 库创建同 id 较麻烦）
    # 简化：在同一库上模拟远端 op 写入 changes/other_device/
    remote_op = {
        "format": "yancuo-operation",
        "format_version": 1,
        "operation_id": "op_remote_solution_1",
        "device_id": "dev_other",
        "database_id": "db_other",
        "timestamp": "2026-07-22T00:00:00+00:00",
        "entity_type": "problem",
        "entity_id": pid,
        "operation": "update",
        "base_revision": 1,
        "new_revision": 2,
        "changed_fields": {"solution_markdown": "解析R"},
        "base_fields": {"solution_markdown": ""},
        "tombstone": False,
    }
    provider.append_operations("local", "sync-repo", "dev_other", [remote_op])

    result = sync_a.pull_and_merge()
    assert result["conflicts"] == 0
    assert result["applied"] >= 1
    got = services.get_problem(pid)
    assert got is not None
    assert got.priority == 5
    assert got.solution_markdown == "解析R"


def test_pull_same_field_creates_review(runtime, tmp_path: Path):
    cloud_root = tmp_path / "cloud2"
    provider = LocalFolderProvider(cloud_root)
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "sync-repo2"
    runtime.settings.sync.create_snapshot_before_merge = False

    services = AppServices(runtime)
    pid = services.create_problem(title="冲突题").id
    services.update_problem(pid, {"solution_markdown": "本地解析"})

    sync = SyncService(runtime, provider)
    sync.push_operations()

    remote_op = {
        "format": "yancuo-operation",
        "format_version": 1,
        "operation_id": "op_remote_conflict_1",
        "device_id": "dev_other",
        "database_id": "db_other",
        "timestamp": "2026-07-22T00:01:00+00:00",
        "entity_type": "problem",
        "entity_id": pid,
        "operation": "update",
        "base_revision": 1,
        "new_revision": 2,
        "changed_fields": {"solution_markdown": "远端解析"},
        "base_fields": {"solution_markdown": ""},
        "tombstone": False,
    }
    provider.append_operations("local", "sync-repo2", "dev_other", [remote_op])

    result = sync.pull_and_merge()
    assert result["conflicts"] >= 1
    assert result["review_session_id"]
    with runtime.session_factory() as s:
        item = s.scalar(
            select(ReviewItem).where(ReviewItem.problem_id == pid, ReviewItem.status == "conflict")
        )
        assert item is not None
        session = s.get(ReviewSession, item.session_id)
        assert session is not None
        assert session.source == "sync"
