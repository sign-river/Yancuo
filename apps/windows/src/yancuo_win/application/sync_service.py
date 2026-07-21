"""增量同步编排：本地 op 日志、推送、拉取合并、冲突进 ReviewSession。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import Problem, ReviewItem, ReviewSession, SyncOperation, Tag
from yancuo_win.domain.operations import build_operation, validate_operation
from yancuo_win.domain.rules import DomainError
from yancuo_win.domain.sync_merge import apply_patch, merge_snapshots
from yancuo_win.import_export.ebpack import EbpackService
from yancuo_win.review.changeset import snapshot_problem_fields


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sync_snapshot(problem: Problem, tag_names: list[str] | None = None) -> dict[str, Any]:
    snap = snapshot_problem_fields(problem)
    snap["chapter_id"] = problem.chapter_id
    snap["is_favorite"] = problem.is_favorite
    snap["mastery"] = problem.mastery
    snap["deleted_at"] = problem.deleted_at.isoformat() if problem.deleted_at else None
    snap["tags"] = list(tag_names or [t.name for t in (problem.tags or [])])
    return snap


class SyncService:
    def __init__(self, runtime: RuntimeContext, provider=None) -> None:
        self.runtime = runtime
        self.provider = provider or get_cloud_provider(runtime.settings)
        self.ebpack = EbpackService(runtime)

    @property
    def owner(self) -> str:
        return (self.runtime.settings.cloud.repository.owner or "local").strip()

    @property
    def repo(self) -> str:
        return (
            self.runtime.settings.cloud.repository.name or "graduate-mistake-book-data"
        ).strip()

    def _require_ops_provider(self) -> LocalFolderProvider:
        if not isinstance(self.provider, LocalFolderProvider):
            raise DomainError(
                "阶段 J 增量同步目前仅完整实现于 local_folder 提供商；"
                "GitLink/GitHub 仍用完整备份通道。"
            )
        return self.provider

    def record_problem_update(
        self,
        problem: Problem,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
        operation: str = "update",
    ) -> dict[str, Any] | None:
        """比较 before/after，写入本地 sync_operations（未推送）。"""
        changed = {k: after[k] for k in after if before.get(k) != after.get(k)}
        if not changed and operation == "update":
            return None
        base_fields = {k: before.get(k) for k in changed}
        op = build_operation(
            device_id=self.runtime.identity.device_id,
            database_id=self.runtime.identity.database_id,
            entity_type="problem",
            entity_id=problem.id,
            operation=operation,
            changed_fields=changed,
            base_revision=int(before.get("revision") or 0),
            new_revision=int(after.get("revision") or problem.revision),
            tombstone=operation == "delete",
        )
        op["base_fields"] = base_fields
        with self.runtime.session_factory() as s:
            existing = s.get(SyncOperation, op["operation_id"])
            if existing:
                return op
            row = SyncOperation(
                id=op["operation_id"],
                device_id=op["device_id"],
                entity_type=op["entity_type"],
                entity_id=op["entity_id"],
                operation=op["operation"],
                payload_json=json.dumps(op, ensure_ascii=False),
                base_revision=op["base_revision"],
                new_revision=op["new_revision"],
                origin="local",
            )
            s.add(row)
            s.commit()
        return op

    def list_unpushed(self) -> list[dict[str, Any]]:
        with self.runtime.session_factory() as s:
            rows = s.scalars(
                select(SyncOperation).where(
                    SyncOperation.origin == "local",
                    SyncOperation.pushed_at.is_(None),
                )
            ).all()
            return [json.loads(r.payload_json) for r in rows]

    def push_operations(self) -> dict[str, Any]:
        provider = self._require_ops_provider()
        ops = self.list_unpushed()
        if not ops:
            return {"pushed": 0}
        device_id = self.runtime.identity.device_id
        if not provider.acquire_lock(self.owner, self.repo, device_id):
            raise DomainError("无法获取同步锁")
        try:
            provider.register_device(
                self.owner,
                self.repo,
                {
                    "device_id": device_id,
                    "database_id": self.runtime.identity.database_id,
                    "updated_at": _utcnow().isoformat(),
                },
            )
            provider.append_operations(self.owner, self.repo, device_id, ops)
            now = _utcnow()
            with self.runtime.session_factory() as s:
                for op in ops:
                    row = s.get(SyncOperation, op["operation_id"])
                    if row:
                        row.pushed_at = now
                s.commit()
            for op in ops:
                if op.get("tombstone") or op.get("operation") == "delete":
                    provider.write_tombstone(
                        self.owner,
                        self.repo,
                        str(op["entity_id"]),
                        {"operation_id": op["operation_id"], "at": now.isoformat()},
                    )
        finally:
            provider.release_lock(self.owner, self.repo, device_id)
        return {"pushed": len(ops)}

    def _local_snapshot_before_merge(self) -> Path | None:
        if not self.runtime.settings.sync.create_snapshot_before_merge:
            return None
        stamp = _utcnow().strftime("%Y%m%dT%H%M%SZ")
        dest = self.runtime.paths.backup_dir / f"pre-sync-{stamp}.ebpack"
        return self.ebpack.export_ebpack(dest)

    def pull_and_merge(self) -> dict[str, Any]:
        provider = self._require_ops_provider()
        snapshot = self._local_snapshot_before_merge()
        remote_ops = provider.list_remote_operations(
            self.owner, self.repo, exclude_device=self.runtime.identity.device_id
        )
        applied = 0
        auto_merged = 0
        conflict_items = 0
        session_id: str | None = None

        # 幂等：已应用的跳过
        with self.runtime.session_factory() as s:
            known = {
                r.id
                for r in s.scalars(select(SyncOperation)).all()
            }

        # 按实体分组
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for raw in remote_ops:
            try:
                op = validate_operation(raw)
            except DomainError:
                continue
            if op["operation_id"] in known:
                continue
            by_entity.setdefault(op["entity_id"], []).append(op)

        for entity_id, ops in by_entity.items():
            result = self._merge_entity_ops(entity_id, ops)
            applied += result["applied"]
            auto_merged += result["auto"]
            conflict_items += result["conflicts"]
            if result.get("session_id"):
                session_id = result["session_id"]

        return {
            "applied": applied,
            "auto_merged_fields": auto_merged,
            "conflicts": conflict_items,
            "review_session_id": session_id,
            "snapshot": str(snapshot) if snapshot else None,
        }

    def _merge_entity_ops(self, entity_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
        with self.runtime.session_factory() as s:
            problem = s.get(Problem, entity_id)
            if not problem:
                # 远端 create 可后置；v1 跳过未知实体
                for op in ops:
                    self._store_remote_op(s, op, applied=False)
                s.commit()
                return {"applied": 0, "auto": 0, "conflicts": 0}

            tag_names = [t.name for t in problem.tags]
            local = sync_snapshot(problem, tag_names)
            # 用各 op 的 base_fields 还原共同祖先：取第一个 op 的 base 覆盖
            base = dict(local)
            remote = dict(local)
            for op in ops:
                bf = op.get("base_fields") or {}
                for k, v in bf.items():
                    base[k] = v
                remote = apply_patch(remote, op.get("changed_fields") or {})

            merged, conflicts = merge_snapshots(base, local, remote)
            auto = 0
            if conflicts:
                session = ReviewSession(
                    id=new_id("review"),
                    source="sync",
                    status="open",
                    summary=f"同步冲突：{entity_id}",
                )
                s.add(session)
                s.flush()
                # proposed = remote 冲突字段；before = local
                proposed = {c["field"]: c["remote"] for c in conflicts}
                # 非冲突字段先自动写入
                for k, v in merged.items():
                    if any(c["field"] == k for c in conflicts):
                        continue
                    if k in {"revision", "tags"}:
                        continue
                    if hasattr(problem, k) and getattr(problem, k) != v:
                        setattr(problem, k, v)
                        auto += 1
                item = ReviewItem(
                    id=new_id("ritem"),
                    session_id=session.id,
                    problem_id=entity_id,
                    status="conflict",
                    base_revision=int(local.get("revision") or problem.revision),
                    before_json=json.dumps(local, ensure_ascii=False),
                    proposed_json=json.dumps(proposed, ensure_ascii=False),
                    uncertain_json=json.dumps(conflicts, ensure_ascii=False),
                )
                s.add(item)
                for op in ops:
                    self._store_remote_op(s, op, applied=True)
                s.commit()
                return {
                    "applied": len(ops),
                    "auto": auto,
                    "conflicts": len(conflicts),
                    "session_id": session.id,
                }

            # 无冲突：应用 merged
            for k, v in merged.items():
                if k in {"revision", "tags", "deleted_at"}:
                    continue
                if hasattr(problem, k):
                    setattr(problem, k, v)
                    auto += 1
            if "tags" in merged:
                self._replace_tags(s, problem, merged["tags"])
            problem.revision = int(problem.revision) + 1
            problem.updated_at = _utcnow()
            for op in ops:
                self._store_remote_op(s, op, applied=True)
            s.commit()
            return {"applied": len(ops), "auto": auto, "conflicts": 0}

    def _replace_tags(self, s, problem: Problem, names: list[str]) -> None:
        problem.tags.clear()
        for name in names:
            tag = s.scalar(select(Tag).where(Tag.name == name))
            if not tag:
                tag = Tag(id=new_id("tag"), name=name)
                s.add(tag)
                s.flush()
            problem.tags.append(tag)

    def _store_remote_op(self, s, op: dict[str, Any], *, applied: bool) -> None:
        if s.get(SyncOperation, op["operation_id"]):
            return
        row = SyncOperation(
            id=op["operation_id"],
            device_id=str(op.get("device_id") or ""),
            entity_type=str(op["entity_type"]),
            entity_id=str(op["entity_id"]),
            operation=str(op["operation"]),
            payload_json=json.dumps(op, ensure_ascii=False),
            base_revision=int(op.get("base_revision") or 0),
            new_revision=int(op.get("new_revision") or 0),
            origin="remote",
            applied_at=_utcnow() if applied else None,
            pushed_at=_utcnow(),
        )
        s.add(row)
