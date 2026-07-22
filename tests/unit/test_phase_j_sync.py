"""阶段 J：字段合并与 LocalFolder 增量同步。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.application.sync_service import SyncService
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import (
    Problem,
    ReviewItem,
    ReviewSession,
    SyncOperation,
    Version,
)
from yancuo_win.domain.operations import validate_operation
from yancuo_win.domain.rules import DomainError
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


def test_operation_validation_rejects_missing_identity_and_bad_revision() -> None:
    base = {
        "format": "yancuo-operation",
        "format_version": 1,
        "operation_id": "op_valid_shape",
        "device_id": "dev_other",
        "database_id": "db_shared",
        "timestamp": "2026-07-22T00:00:00+00:00",
        "entity_type": "problem",
        "entity_id": "problem_1",
        "operation": "update",
        "base_revision": 1,
        "new_revision": 2,
        "changed_fields": {"title": "x"},
        "tombstone": False,
    }
    for patch in (
        {"entity_id": ""},
        {"database_id": None},
        {"base_revision": -1},
        {"new_revision": "not-an-int"},
        {"base_fields": []},
    ):
        with pytest.raises(DomainError):
            validate_operation({**base, **patch})


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
    with runtime.session_factory() as s:
        sync_version = s.scalar(
            select(Version).where(
                Version.problem_id == pid,
                Version.source == "sync",
            )
        )
        assert sync_version is not None


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


def test_local_mutations_are_recorded_as_operations(runtime):
    services = AppServices(runtime)
    problem = services.create_problem(title="操作日志题")

    services.update_problem(problem.id, {"priority": 5})
    services.set_problem_status(problem.id, "active")
    services.schedule_initial_review(problem.id)
    services.record_review(problem.id, 4)
    services.trash_problem(problem.id)
    services.restore_problem(problem.id, "active")

    with runtime.session_factory() as s:
        rows = list(s.scalars(select(SyncOperation).order_by(SyncOperation.created_at)).all())

    operations = [row.operation for row in rows if row.entity_id == problem.id]
    assert "create" in operations
    assert "update" in operations
    assert "delete" in operations
    assert "undelete" in operations
    payloads = [json.loads(row.payload_json) for row in rows if row.entity_id == problem.id]
    review_payloads = [p for p in payloads if "next_review_at" in p["changed_fields"]]
    assert review_payloads
    assert all(p["new_revision"] > p["base_revision"] for p in review_payloads)


def test_remote_create_materializes_unknown_problem(runtime, tmp_path: Path):
    cloud_root = tmp_path / "remote-create"
    provider = LocalFolderProvider(cloud_root)
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "create-repo"
    runtime.settings.sync.create_snapshot_before_merge = False

    remote_op = {
        "format": "yancuo-operation",
        "format_version": 1,
        "operation_id": "op_remote_create_1",
        "device_id": "dev_other",
        "database_id": "db_other",
        "timestamp": "2026-07-22T00:02:00+00:00",
        "entity_type": "problem",
        "entity_id": "problem_remote_1",
        "operation": "create",
        "base_revision": 0,
        "new_revision": 1,
        "changed_fields": {
            "title": "远端新题",
            "status": "active",
            "question_markdown": "题干",
            "tags": ["远端"],
            "next_review_at": "2026-07-23T00:00:00+00:00",
            "revision": 1,
        },
        "base_fields": {},
        "tombstone": False,
    }
    provider.append_operations("local", "create-repo", "dev_other", [remote_op])

    result = SyncService(runtime, provider).pull_and_merge()

    assert result["applied"] == 1
    with runtime.session_factory() as s:
        problem = s.get(Problem, "problem_remote_1")
        assert problem is not None
        assert problem.title == "远端新题"
        assert problem.status == "active"
        assert [tag.name for tag in problem.tags] == ["远端"]
        assert problem.next_review_at is not None


def test_remote_update_cannot_overwrite_identity_or_relationships(runtime, tmp_path: Path):
    provider = LocalFolderProvider(tmp_path / "remote-safe-fields")
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "safe-fields-repo"
    runtime.settings.sync.create_snapshot_before_merge = False
    services = AppServices(runtime)
    problem = services.create_problem(title="原题")

    remote_op = {
        "format": "yancuo-operation",
        "format_version": 1,
        "operation_id": "op_remote_safe_fields_1",
        "device_id": "dev_other",
        "database_id": runtime.identity.database_id,
        "timestamp": "2026-07-22T00:03:00+00:00",
        "entity_type": "problem",
        "entity_id": problem.id,
        "operation": "update",
        "base_revision": 1,
        "new_revision": 2,
        "changed_fields": {
            "title": "合法标题",
            "id": "problem_hijack",
            "revision": 9999,
            "updated_at": "1999-01-01T00:00:00+00:00",
            "assets": [],
        },
        "base_fields": {"title": "原题"},
        "tombstone": False,
    }
    provider.append_operations("local", "safe-fields-repo", "dev_other", [remote_op])

    result = SyncService(runtime, provider).pull_and_merge()

    assert result["applied"] == 1
    got = services.get_problem(problem.id)
    assert got is not None
    assert got.id == problem.id
    assert got.title == "合法标题"
    assert got.revision != 9999
    assert got.assets == []


def test_remote_update_waits_for_late_create(runtime, tmp_path: Path):
    provider = LocalFolderProvider(tmp_path / "late-create")
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "late-create-repo"
    runtime.settings.sync.create_snapshot_before_merge = False
    entity_id = "problem_late_create"
    update = {
        "format": "yancuo-operation",
        "format_version": 1,
        "operation_id": "op_late_update",
        "device_id": "dev_other",
        "database_id": runtime.identity.database_id,
        "timestamp": "2026-07-22T00:02:00+00:00",
        "entity_type": "problem",
        "entity_id": entity_id,
        "operation": "update",
        "base_revision": 1,
        "new_revision": 2,
        "changed_fields": {"title": "后续更新"},
        "base_fields": {"title": None},
        "tombstone": False,
    }
    provider.append_operations("local", "late-create-repo", "dev_other", [update])
    first = SyncService(runtime, provider).pull_and_merge()
    assert first["applied"] == 0

    create = {
        **update,
        "operation_id": "op_late_create",
        "timestamp": "2026-07-22T00:01:00+00:00",
        "operation": "create",
        "base_revision": 0,
        "new_revision": 1,
        "changed_fields": {
            "title": "初始题",
            "question_markdown": "题干",
            "revision": 1,
        },
        "base_fields": {},
    }
    provider.append_operations("local", "late-create-repo", "dev_other", [create])
    second = SyncService(runtime, provider).pull_and_merge()
    assert second["applied"] == 2
    with runtime.session_factory() as s:
        problem = s.get(Problem, entity_id)
        assert problem is not None
        assert problem.title == "后续更新"
        rows = list(
            s.scalars(
                select(SyncOperation).where(SyncOperation.entity_id == entity_id)
            ).all()
        )
        assert len(rows) == 2
        assert all(row.applied_at is not None for row in rows)
