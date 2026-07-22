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
from yancuo_win.data.models import (
    Problem,
    ReviewItem,
    ReviewSession,
    SyncOperation,
    Tag,
    Version,
)
from yancuo_win.domain.operations import build_operation, validate_operation
from yancuo_win.domain.rules import DomainError, validate_priority, validate_status
from yancuo_win.domain.sync_merge import apply_patch, merge_snapshots
from yancuo_win.import_export.ebpack import EbpackService
from yancuo_win.review.changeset import snapshot_problem_fields


_SYNC_MUTABLE_FIELDS = frozenset(
    {
        "status",
        "subject_id",
        "chapter_id",
        "problem_type",
        "title",
        "question_markdown",
        "question_latex",
        "user_answer",
        "correct_answer",
        "solution_markdown",
        "error_analysis",
        "notes",
        "source_book",
        "source_year",
        "page_number",
        "original_number",
        "priority",
        "difficulty",
        "mastery",
        "is_favorite",
        "needs_redo",
        "allow_print",
        "human_confirmed",
        "next_review_at",
        "review_count",
        "deleted_at",
    }
)
_SYNC_REQUIRED_TEXT_FIELDS = frozenset(
    {
        "question_markdown",
        "question_latex",
        "user_answer",
        "correct_answer",
        "solution_markdown",
        "error_analysis",
        "notes",
    }
)
_SYNC_OPTIONAL_TEXT_FIELDS = frozenset(
    {
        "subject_id",
        "chapter_id",
        "problem_type",
        "title",
        "source_book",
        "source_year",
        "page_number",
        "original_number",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_datetime(value: datetime | None) -> str | None:
    """将数据库 datetime 转成稳定的跨端 JSON 表示。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    try:
        parsed = datetime.fromisoformat(str(value))
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc)
        )
    except ValueError as exc:
        raise DomainError(f"同步时间字段格式错误：{value!r}") from exc


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "1"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "0"}:
        return False
    raise DomainError(f"sync boolean is invalid: {value!r}")


def _coerce_sync_value(field: str, value: Any) -> Any:
    if field not in _SYNC_MUTABLE_FIELDS:
        raise DomainError(f"同步字段不可修改：{field}")
    if field == "status":
        return validate_status(str(value))
    if field in {"next_review_at", "deleted_at"}:
        return _parse_datetime(value)
    if field == "priority":
        try:
            return validate_priority(int(value))
        except (TypeError, ValueError) as exc:
            raise DomainError(f"同步 priority 字段无效：{value!r}") from exc
    if field in {"difficulty", "mastery", "review_count"}:
        if value is None and field in {"difficulty", "mastery"}:
            return None
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise DomainError(f"同步整数字段无效：{field}={value!r}") from exc
        if field == "review_count" and number < 0:
            raise DomainError("同步 review_count 不得为负数")
        return number
    if field in {"is_favorite", "needs_redo", "allow_print", "human_confirmed"}:
        return _coerce_bool(value)
    if field in _SYNC_REQUIRED_TEXT_FIELDS:
        if not isinstance(value, str):
            raise DomainError(f"同步文本字段无效：{field}={value!r}")
        return value
    if field in _SYNC_OPTIONAL_TEXT_FIELDS:
        if value is not None and not isinstance(value, str):
            raise DomainError(f"同步文本字段无效：{field}={value!r}")
        return value
    return value


def sync_snapshot(problem: Problem, tag_names: list[str] | None = None) -> dict[str, Any]:
    """返回完整、可 JSON 序列化的题目同步快照。

    旧实现只覆盖正文和少数属性，导致状态、来源、复习字段等本地写入
    无法进入 Operation。这里集中定义可同步字段，避免各调用点自行拼接。
    """
    snap = snapshot_problem_fields(problem)
    for field in (
        "subject_id",
        "chapter_id",
        "problem_type",
        "source_book",
        "source_year",
        "page_number",
        "original_number",
        "difficulty",
        "is_favorite",
        "needs_redo",
        "allow_print",
        "human_confirmed",
        "mastery",
        "review_count",
    ):
        snap[field] = getattr(problem, field)
    snap["next_review_at"] = _iso_datetime(problem.next_review_at)
    snap["deleted_at"] = _iso_datetime(problem.deleted_at)
    names = tag_names if tag_names is not None else [t.name for t in (problem.tags or [])]
    snap["tags"] = sorted({str(name) for name in names if str(name).strip()})
    return snap


class SyncService:
    def __init__(self, runtime: RuntimeContext, provider=None) -> None:
        self.runtime = runtime
        # 记录本地 Operation 不应因为云端配置无效而失败；仅在真正执行
        # push/pull 时解析默认提供商。显式传入的 provider 仍立即复用。
        self.provider = provider
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
        provider = self.provider
        if provider is None:
            provider = get_cloud_provider(self.runtime.settings)
            self.provider = provider
        if not isinstance(provider, LocalFolderProvider):
            raise DomainError(
                "阶段 J 增量同步目前仅完整实现于 local_folder 提供商；"
                "GitLink/GitHub 仍用完整备份通道。"
            )
        return provider

    def record_problem_update(
        self,
        problem: Problem | str,
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
        problem_id = str(getattr(problem, "id", problem))
        problem_revision = int(getattr(problem, "revision", 0) or 0)
        op = build_operation(
            device_id=self.runtime.identity.device_id,
            database_id=self.runtime.identity.database_id,
            entity_type="problem",
            entity_id=problem_id,
            operation=operation,
            changed_fields=changed,
            base_revision=int(before.get("revision") or 0),
            new_revision=int(after.get("revision") or problem_revision),
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
                for r in s.scalars(
                    select(SyncOperation).where(SyncOperation.applied_at.is_not(None))
                ).all()
            }

        # 按实体分组
        by_entity: dict[str, list[dict[str, Any]]] = {}
        for raw in remote_ops:
            try:
                op = validate_operation(raw)
            except DomainError:
                continue
            if op["entity_type"] != "problem":
                # v1 的本地持久化模型预留了其他实体类型，但当前合并器只
                # 实现题目；不能把 asset/tag/review 补丁误套到 Problem。
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
                # create 必须在首次拉取时落地，否则后续 update 永远会被标记
                # 为已处理而丢失。其余未知实体仍保留为未应用，等待用户恢复快照。
                create_op = next((op for op in ops if op["operation"] == "create"), None)
                if create_op:
                    fields: dict[str, Any] = {}
                    for op in ops:
                        fields.update(op.get("changed_fields") or {})
                    problem = self._create_remote_problem(s, entity_id, fields)
                    for op in ops:
                        self._store_remote_op(s, op, applied=True)
                    s.commit()
                    return {"applied": len(ops), "auto": len(fields), "conflicts": 0}
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
            local_revision = int(local.get("revision") or problem.revision)
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
                    if self._apply_problem_field(problem, k, v):
                        auto += 1
                if "tags" in merged:
                    before_tags = {t.name for t in problem.tags}
                    self._replace_tags(s, problem, merged["tags"])
                    if before_tags != {t.name for t in problem.tags}:
                        auto += 1
                if "status" in merged or "deleted_at" in merged:
                    self._normalize_deleted_at(problem)
                problem.revision = max(
                    problem.revision + 1,
                    max((int(op.get("new_revision") or 0) for op in ops), default=0),
                )
                problem.updated_at = _utcnow()
                s.add(
                    Version(
                        id=new_id("ver"),
                        problem_id=problem.id,
                        revision=problem.revision,
                        source="sync",
                        summary="同步自动合并（待处理冲突）",
                        snapshot_json=json.dumps(
                            sync_snapshot(problem, [t.name for t in problem.tags]),
                            ensure_ascii=False,
                        ),
                    )
                )
                item = ReviewItem(
                    id=new_id("ritem"),
                    session_id=session.id,
                    problem_id=entity_id,
                    status="conflict",
                    base_revision=local_revision,
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
                if k in {"revision", "tags"}:
                    continue
                if self._apply_problem_field(problem, k, v):
                    auto += 1
            if "tags" in merged:
                before_tags = {t.name for t in problem.tags}
                self._replace_tags(s, problem, merged["tags"])
                if before_tags != {t.name for t in problem.tags}:
                    auto += 1
            if "status" in merged or "deleted_at" in merged:
                self._normalize_deleted_at(problem)
            problem.revision = max(
                int(problem.revision) + 1,
                max((int(op.get("new_revision") or 0) for op in ops), default=0),
            )
            problem.updated_at = _utcnow()
            s.add(
                Version(
                    id=new_id("ver"),
                    problem_id=problem.id,
                    revision=problem.revision,
                    source="sync",
                    summary="同步自动合并",
                    snapshot_json=json.dumps(
                        sync_snapshot(problem, [t.name for t in problem.tags]),
                        ensure_ascii=False,
                    ),
                )
            )
            for op in ops:
                self._store_remote_op(s, op, applied=True)
            s.commit()
            return {"applied": len(ops), "auto": auto, "conflicts": 0}

    @staticmethod
    def _apply_problem_field(problem: Problem, field: str, value: Any) -> bool:
        """应用一个同步字段，并处理 SQLite DateTime 的 JSON 往返。"""
        if field not in _SYNC_MUTABLE_FIELDS:
            return False
        value = _coerce_sync_value(field, value)
        if getattr(problem, field) == value:
            return False
        setattr(problem, field, value)
        return True

    @staticmethod
    def _normalize_deleted_at(problem: Problem) -> None:
        if problem.status == "trashed" and problem.deleted_at is None:
            problem.deleted_at = _utcnow()
        elif problem.status != "trashed":
            problem.deleted_at = None

    def _create_remote_problem(
        self, session, entity_id: str, fields: dict[str, Any]
    ) -> Problem:
        """从远端 create Operation 创建本地题目。"""
        kwargs: dict[str, Any] = {}
        for field, value in fields.items():
            if field not in _SYNC_MUTABLE_FIELDS:
                continue
            kwargs[field] = _coerce_sync_value(field, value)
        status = kwargs.get("status") or "inbox"
        if status not in {"inbox", "active", "archived", "trashed"}:
            status = "inbox"
        kwargs["status"] = status
        if status == "trashed" and kwargs.get("deleted_at") is None:
            kwargs["deleted_at"] = _utcnow()
        elif status != "trashed":
            kwargs["deleted_at"] = None
        kwargs["id"] = entity_id
        try:
            kwargs["revision"] = max(1, int(fields.get("revision") or 1))
        except (TypeError, ValueError) as exc:
            raise DomainError("远端 revision 字段无效") from exc
        problem = Problem(**kwargs)
        session.add(problem)
        session.flush()
        tags = fields.get("tags")
        if isinstance(tags, list):
            self._replace_tags(session, problem, tags)
        session.add(
            Version(
                id=new_id("ver"),
                problem_id=problem.id,
                revision=problem.revision,
                source="sync",
                summary="从远端创建题目",
                snapshot_json=json.dumps(
                    sync_snapshot(problem, [t.name for t in problem.tags]),
                    ensure_ascii=False,
                ),
            )
        )
        return problem

    def _replace_tags(self, s, problem: Problem, names: list[str]) -> None:
        problem.tags.clear()
        if not isinstance(names, list):
            return
        seen: set[str] = set()
        for raw_name in names[:20]:
            name = str(raw_name).strip()
            if not name or name in seen or len(name) > 128:
                continue
            seen.add(name)
            tag = s.scalar(select(Tag).where(Tag.name == name))
            if not tag:
                tag = Tag(id=new_id("tag"), name=name)
                s.add(tag)
                s.flush()
            problem.tags.append(tag)

    def _store_remote_op(self, s, op: dict[str, Any], *, applied: bool) -> None:
        existing = s.get(SyncOperation, op["operation_id"])
        if existing:
            if applied and existing.applied_at is None:
                existing.applied_at = _utcnow()
                existing.pushed_at = existing.pushed_at or _utcnow()
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
