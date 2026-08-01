"""阶段 J：字段合并与 LocalFolder 增量同步。"""

from __future__ import annotations

import json
import base64
import hashlib
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.application.sync_service import SyncService
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.cloud.github import GitHubProvider
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import (
    Asset,
    Problem,
    ReviewItem,
    ReviewSession,
    SyncOperation,
    Version,
)
from yancuo_win.data.ids import new_id
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


def test_structured_figure_operation_carries_and_restores_derived_asset(
    runtime, tmp_path: Path
):
    provider = LocalFolderProvider(tmp_path / "derived-remote")
    runtime.settings.cloud.repository.owner = "local"
    runtime.settings.cloud.repository.name = "derived-repo"
    runtime.settings.sync.create_snapshot_before_merge = False
    payload = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    sha256 = hashlib.sha256(payload).hexdigest()
    asset_id = new_id("asset")
    content_json = json.dumps(
        [
            {
                "type": "figure",
                "content": "函数图像",
                "source_image_index": 0,
                "source_region": {"x": 0, "y": 0, "width": 1, "height": 1},
                "derived_asset_id": asset_id,
            }
        ],
        ensure_ascii=False,
    )
    remote_op = {
        "format": "yancuo-operation",
        "format_version": 1,
        "operation_id": "op_remote_derived_1",
        "device_id": "dev_other",
        "database_id": "db_other",
        "timestamp": "2026-07-22T00:02:00+00:00",
        "entity_type": "problem",
        "entity_id": "problem_remote_derived_1",
        "operation": "create",
        "base_revision": 0,
        "new_revision": 1,
        "changed_fields": {
            "title": "带题图的远端题目",
            "status": "active",
            "question_markdown": "兼容题干",
            "question_content_json": content_json,
            "revision": 1,
        },
        "base_fields": {},
        "attachments": [
            {
                "id": asset_id,
                "role": "derived_figure",
                "sha256": sha256,
                "mime_type": "image/png",
                "size_bytes": len(payload),
                "width": 1,
                "height": 1,
                "content_base64": base64.b64encode(payload).decode("ascii"),
            }
        ],
        "tombstone": False,
    }
    provider.append_operations("local", "derived-repo", "dev_other", [remote_op])

    result = SyncService(runtime, provider).pull_and_merge()

    assert result["applied"] == 1
    with runtime.session_factory() as session:
        problem = session.get(Problem, "problem_remote_derived_1")
        asset = session.get(Asset, asset_id)
        assert problem is not None
        assert problem.question_content_json == content_json
        assert asset is not None
        assert asset.problem_id == problem.id
        assert asset.is_immutable is True
        assert SyncService(runtime, provider).store.resolve(asset.relative_path).read_bytes() == payload


def test_local_structured_figure_update_embeds_referenced_crop(runtime, tmp_path: Path):
    services = AppServices(runtime)
    problem = services.create_problem(title="本地结构化题")
    crop = tmp_path / "crop.png"
    crop.write_bytes(b"isolated-derived-crop")
    stored = services.store.store_copy(crop, role="derived_figure")
    asset_id = new_id("asset")
    with runtime.session_factory() as session:
        session.add(
            Asset(
                id=asset_id,
                problem_id=problem.id,
                role="derived_figure",
                sha256=stored.sha256,
                relative_path=stored.relative_path,
                mime_type="image/png",
                size_bytes=stored.size_bytes,
                width=10,
                height=10,
                is_immutable=True,
            )
        )
        session.commit()
    content_json = json.dumps(
        [
            {
                "type": "figure",
                "content": "局部题图",
                "source_image_index": 0,
                "source_region": {"x": 0, "y": 0, "width": 1, "height": 1},
                "derived_asset_id": asset_id,
            }
        ]
    )

    services.update_problem(problem.id, {"question_content_json": content_json})

    operations = SyncService(runtime).list_unpushed()
    update = next(
        operation
        for operation in operations
        if operation["changed_fields"].get("question_content_json") == content_json
    )
    assert update["attachments"][0]["id"] == asset_id
    assert base64.b64decode(update["attachments"][0]["content_base64"]) == crop.read_bytes()


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


def test_github_operation_batch_push_pull_and_profile_isolation(runtime, tmp_path: Path, monkeypatch) -> None:
    provider = GitHubProvider(token="ghp_test")
    manifest: dict[str, Any] = {"format": "yancuo-profile-snapshots", "profiles": {}, "aliases": {}}
    assets: dict[tuple[str, str], Path] = {}
    locked = False

    def acquire(*_args) -> bool:
        nonlocal locked
        if locked:
            return False
        locked = True
        return True

    def release(*_args) -> None:
        nonlocal locked
        locked = False

    provider.acquire_lock = acquire  # type: ignore[method-assign]
    provider.release_lock = release  # type: ignore[method-assign]
    provider.read_sync_manifest = lambda *_args: json.loads(json.dumps(manifest))  # type: ignore[method-assign]
    provider.write_sync_manifest = lambda *_args: manifest.update(_args[-1])  # type: ignore[method-assign]
    provider.create_release = lambda *_args, **kwargs: None  # type: ignore[method-assign]
    def upload(_owner, _repo, *, tag, file_path, asset_name):
        target = tmp_path / f"{tag}-{asset_name}"
        shutil.copy2(file_path, target)
        assets[(tag, asset_name)] = target
        return {"name": asset_name}
    def download(_owner, _repo, *, tag, asset_name, dest):
        shutil.copy2(assets[(tag, asset_name)], dest)
        return dest
    provider.upload_release_asset = upload  # type: ignore[method-assign]
    provider.download_release_asset = download  # type: ignore[method-assign]

    problem = AppServices(runtime).create_problem(title="GitHub 同步题")
    pushed = SyncService(runtime, provider).push_operations()
    assert pushed["pushed"] >= 1
    assert manifest["operation_batches"]

    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "github-second"))
    second = bootstrap_runtime()
    second.identity = replace(second.identity, profile_id=runtime.identity.profile_id)
    pulled = SyncService(second, provider).pull_and_merge()
    assert pulled["applied"] >= 1
    assert AppServices(second).get_problem(problem.id) is not None

    repeated = SyncService(second, provider).pull_and_merge()
    assert repeated["applied"] == 0

    batch = manifest["operation_batches"][0]
    assets[(batch["tag"], batch["asset_name"])].write_text("tampered\n", encoding="utf-8")
    with pytest.raises(DomainError, match="哈希"):
        SyncService(second, provider)._github_remote_operations(provider)

    manifest["operation_batches"][0]["profile_id"] = "profile_other"
    third = bootstrap_runtime()
    third.identity = replace(third.identity, profile_id=runtime.identity.profile_id)
    assert SyncService(third, provider)._github_remote_operations(provider) == []


def test_github_batch_upload_failure_keeps_index_and_operations_unpushed(runtime) -> None:
    provider = GitHubProvider(token="ghp_test")
    manifest: dict[str, Any] = {"format": "yancuo-profile-snapshots", "profiles": {}}
    provider.acquire_lock = lambda *_args: True  # type: ignore[method-assign]
    provider.release_lock = lambda *_args: None  # type: ignore[method-assign]
    provider.read_sync_manifest = lambda *_args: manifest  # type: ignore[method-assign]
    provider.create_release = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    provider.upload_release_asset = lambda *_args, **_kwargs: (_ for _ in ()).throw(DomainError("模拟上传失败"))  # type: ignore[method-assign]
    AppServices(runtime).create_problem(title="中断批次")
    sync = SyncService(runtime, provider)
    with pytest.raises(DomainError, match="上传失败"):
        sync.push_operations()
    assert "operation_batches" not in manifest
    assert sync.list_unpushed()


def test_github_batch_rejects_malformed_index(runtime) -> None:
    provider = GitHubProvider(token="ghp_test")
    provider.read_sync_manifest = lambda *_args: {"operation_batches": {}}  # type: ignore[method-assign]
    with pytest.raises(DomainError, match="索引无效"):
        SyncService(runtime, provider)._github_remote_operations(provider)
