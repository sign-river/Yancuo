"""增量同步编排：本地 op 日志、推送、拉取合并、冲突进 ReviewSession。"""

from __future__ import annotations

import base64
import json
import hashlib
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.cloud.base import CloudProvider
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    Asset,
    Problem,
    ReviewItem,
    ReviewSession,
    SyncOperation,
    Tag,
    Version,
)
from yancuo_win.domain.operations import (
    MAX_OPERATION_ATTACHMENT_BYTES,
    build_operation,
    validate_operation,
)
from yancuo_win.domain.rules import DomainError, validate_priority, validate_status
from yancuo_win.domain.sync_merge import apply_patch, merge_snapshots
from yancuo_win.import_export.ebpack import EbpackService
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.review.changeset import snapshot_problem_fields


MAX_REMOTE_OPERATION_BATCHES = 10_000
MAX_REMOTE_OPERATION_BATCH_BYTES = 64 * 1024 * 1024
MAX_REMOTE_OPERATION_LINE_BYTES = 48 * 1024 * 1024
MAX_REMOTE_OPERATIONS_PER_BATCH = 100_000
MAX_REMOTE_OPERATION_TOTAL_BYTES = 256 * 1024 * 1024
MAX_REMOTE_OPERATIONS_TOTAL = 250_000
SYNC_OPERATION_ID_QUERY_BATCH = 500
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


_SYNC_MUTABLE_FIELDS = frozenset(
    {
        "status",
        "subject_id",
        "chapter_id",
        "problem_type",
        "title",
        "question_markdown",
        "question_latex",
        "question_content_json",
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
        "question_content_json",
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
        self.store = ObjectStore(runtime.paths.asset_objects_dir)

    @property
    def owner(self) -> str:
        return (self.runtime.settings.cloud.repository.owner or "local").strip()

    @property
    def repo(self) -> str:
        return (self.runtime.settings.cloud.repository.name or "graduate-mistake-book-data").strip()

    def _require_ops_provider(self) -> CloudProvider:
        provider = self.provider
        if provider is None:
            provider = get_cloud_provider(self.runtime.settings)
            self.provider = provider
        if isinstance(provider, LocalFolderProvider):
            return provider
        if provider.name != "github" or not provider.get_capabilities().release_assets:
            raise DomainError(
                "增量同步仅支持 local_folder 或具备受控批次锁的 GitHub；GitLink 仍用完整备份。"
            )
        return provider

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _cleanup_unindexed_github_batch(
        self, provider: CloudProvider, tag: str, failure: Exception
    ) -> None:
        try:
            provider.delete_release(self.owner, self.repo, tag=tag)
        except Exception as cleanup_exc:
            raise DomainError(
                f"GitHub Operation 批次发布失败，且清理未入索引的 Release 失败：{cleanup_exc}"
            ) from failure

    def _push_github_batch(self, provider: CloudProvider, ops: list[dict[str, Any]]) -> None:
        if len(ops) > MAX_REMOTE_OPERATIONS_PER_BATCH:
            raise DomainError("待推送 Operation 批次记录过多")
        device_id = self.runtime.identity.device_id
        profile_id = self.runtime.identity.profile_id
        batch_id = f"batch_{uuid.uuid4().hex}"
        tag = f"yancuo-ops-v1-{profile_id[-8:]}-{device_id[-8:]}-{batch_id[-8:]}"
        with tempfile.TemporaryDirectory(dir=self.runtime.paths.cache_dir) as temporary:
            payload = Path(temporary) / "operations.jsonl"
            digest = hashlib.sha256()
            total_bytes = 0
            with payload.open("wb") as stream:
                for op in ops:
                    line_bytes = (json.dumps(op, ensure_ascii=False) + "\n").encode("utf-8")
                    if len(line_bytes) > MAX_REMOTE_OPERATION_LINE_BYTES:
                        raise DomainError("待推送 Operation 批次单行过大")
                    total_bytes += len(line_bytes)
                    if total_bytes > MAX_REMOTE_OPERATION_BATCH_BYTES:
                        raise DomainError("待推送 Operation 批次文件过大")
                    stream.write(line_bytes)
                    digest.update(line_bytes)
            sha = digest.hexdigest()
            body = json.dumps(
                {
                    "format": "yancuo-operation-batch",
                    "format_version": 1,
                    "batch_id": batch_id,
                    "profile_id": profile_id,
                    "device_id": device_id,
                    "asset_name": "operations.jsonl",
                    "operation_count": len(ops),
                    "sha256": sha,
                    "created_at": _utcnow().isoformat(),
                },
                ensure_ascii=False,
            )
            remote_index = provider.read_sync_manifest(self.owner, self.repo)
            if remote_index is None:
                remote_index = {
                    "format": "yancuo-profile-snapshots",
                    "format_version": 1,
                    "profiles": {},
                    "aliases": {},
                }
            if not isinstance(remote_index, dict):
                raise DomainError("云端 Operation 批次索引无效")
            index = dict(remote_index)
            existing_batches = index.get("operation_batches", [])
            if not isinstance(existing_batches, list):
                raise DomainError("云端 Operation 批次索引无效")
            if len(existing_batches) >= MAX_REMOTE_OPERATION_BATCHES:
                raise DomainError("云端 Operation 批次索引已达到容量上限")
            batches = list(existing_batches)
            provider.create_release(
                self.owner, self.repo, tag=tag, name="Yancuo operation batch", body=body
            )
            try:
                provider.upload_release_asset(
                    self.owner,
                    self.repo,
                    tag=tag,
                    file_path=payload,
                    asset_name="operations.jsonl",
                )
                verified = Path(temporary) / "verified.jsonl"
                provider.download_release_asset(
                    self.owner,
                    self.repo,
                    tag=tag,
                    asset_name="operations.jsonl",
                    dest=verified,
                )
                if self._sha256(verified) != sha:
                    raise DomainError("远端 Operation 批次哈希不一致，未更新索引")
            except Exception as exc:
                self._cleanup_unindexed_github_batch(provider, tag, exc)
                raise
        try:
            batches.append(
                {
                    "tag": tag,
                    "batch_id": batch_id,
                    "profile_id": profile_id,
                    "device_id": device_id,
                    "asset_name": "operations.jsonl",
                    "operation_count": len(ops),
                    "sha256": sha,
                    "created_at": _utcnow().isoformat(),
                }
            )
            index["operation_batches"] = batches
            provider.write_sync_manifest(self.owner, self.repo, index)
        except Exception as exc:
            self._cleanup_unindexed_github_batch(provider, tag, exc)
            raise

    def _github_remote_operations(self, provider: CloudProvider) -> list[dict[str, Any]]:
        index = provider.read_sync_manifest(self.owner, self.repo) or {}
        batches = index.get("operation_batches")
        if batches is not None and not isinstance(batches, list):
            raise DomainError("云端 Operation 批次索引无效")
        if not isinstance(batches, list):
            return []
        if len(batches) > MAX_REMOTE_OPERATION_BATCHES:
            raise DomainError("云端 Operation 批次索引过大")
        items: list[dict[str, Any]] = []
        seen_batches: set[str] = set()
        total_remote_bytes = 0
        total_remote_lines = 0
        for batch in batches:
            if (
                not isinstance(batch, dict)
                or batch.get("profile_id") != self.runtime.identity.profile_id
                or batch.get("device_id") == self.runtime.identity.device_id
            ):
                continue
            tag, asset_name, expected = (
                str(batch.get("tag") or ""),
                str(batch.get("asset_name") or ""),
                str(batch.get("sha256") or ""),
            )
            batch_id = str(batch.get("batch_id") or "")
            batch_device_id = str(batch.get("device_id") or "")
            expected_count = batch.get("operation_count")
            if (
                not tag
                or asset_name != "operations.jsonl"
                or not _SHA256_RE.fullmatch(expected)
                or not batch_id
                or not batch_device_id
                or batch_id in seen_batches
            ):
                continue
            if expected_count is not None and (
                isinstance(expected_count, bool)
                or not isinstance(expected_count, int)
                or expected_count < 0
                or expected_count > MAX_REMOTE_OPERATIONS_PER_BATCH
            ):
                raise DomainError("远端 Operation 批次记录数无效")
            seen_batches.add(batch_id)
            with tempfile.TemporaryDirectory(dir=self.runtime.paths.cache_dir) as temporary:
                path = Path(temporary) / "operations.jsonl"
                provider.download_release_asset(
                    self.owner, self.repo, tag=tag, asset_name=asset_name, dest=path
                )
                if not path.is_file():
                    raise DomainError("远端 Operation 批次文件过大或不存在")
                batch_size = path.stat().st_size
                if batch_size > MAX_REMOTE_OPERATION_BATCH_BYTES:
                    raise DomainError("远端 Operation 批次文件过大或不存在")
                total_remote_bytes += batch_size
                if total_remote_bytes > MAX_REMOTE_OPERATION_TOTAL_BYTES:
                    raise DomainError("远端 Operation 批次累计大小过大")
                digest = hashlib.sha256()
                batch_items: list[dict[str, Any]] = []
                physical_lines = 0
                total_bytes = 0
                try:
                    with path.open("rb") as stream:
                        while line_bytes := stream.readline(MAX_REMOTE_OPERATION_LINE_BYTES + 1):
                            physical_lines += 1
                            if physical_lines > MAX_REMOTE_OPERATIONS_PER_BATCH:
                                raise DomainError("远端 Operation 批次物理行数过多")
                            total_remote_lines += 1
                            if total_remote_lines > MAX_REMOTE_OPERATIONS_TOTAL:
                                raise DomainError("远端 Operation 批次累计物理行数过多")
                            if len(line_bytes) > MAX_REMOTE_OPERATION_LINE_BYTES:
                                raise DomainError("远端 Operation 批次单行过大")
                            total_bytes += len(line_bytes)
                            if total_bytes > MAX_REMOTE_OPERATION_BATCH_BYTES:
                                raise DomainError("远端 Operation 批次文件过大")
                            digest.update(line_bytes)
                            try:
                                value = json.loads(line_bytes.decode("utf-8"))
                            except json.JSONDecodeError:
                                continue
                            if isinstance(value, dict):
                                if (
                                    value.get("format") == "yancuo-operation"
                                    and value.get("device_id") != batch_device_id
                                ):
                                    raise DomainError(
                                        "远端 Operation 设备与批次声明不一致"
                                    )
                                batch_items.append(value)
                except UnicodeDecodeError as exc:
                    raise DomainError("远端 Operation 批次不是有效 UTF-8") from exc
                if digest.hexdigest() != expected:
                    raise DomainError("远端 Operation 批次哈希不一致")
                if expected_count is not None and physical_lines != expected_count:
                    raise DomainError("远端 Operation 批次记录数不一致")
                items.extend(batch_items)
        return items

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
        op["attachments"] = self._content_block_attachments(problem_id, changed)
        validate_operation(op)
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

    def _content_block_attachments(
        self, problem_id: str, changed_fields: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if "question_content_json" not in changed_fields:
            return []
        from yancuo_win.application.question_content import load_question_content

        referenced_ids = {
            str(block.get("derived_asset_id"))
            for block in load_question_content(changed_fields["question_content_json"])
            if block.get("type") == "figure" and block.get("derived_asset_id")
        }
        if not referenced_ids:
            return []
        with self.runtime.session_factory() as session:
            problem = session.scalar(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.assets))
            )
            if problem is None:
                return []
            result: list[dict[str, Any]] = []
            total_bytes = 0
            for asset in problem.assets:
                if asset.id not in referenced_ids or asset.role != "derived_figure":
                    continue
                path = self.store.resolve(asset.relative_path)
                remaining = MAX_OPERATION_ATTACHMENT_BYTES - total_bytes
                try:
                    size = path.stat().st_size
                    if size <= 0 or size > remaining:
                        raise DomainError("单个 Operation 的派生题图总大小不能超过 32 MiB")
                    with path.open("rb") as stream:
                        payload = stream.read(remaining + 1)
                except OSError:
                    continue
                if len(payload) != size or len(payload) > remaining:
                    raise DomainError("派生题图在读取期间发生变化或超过大小上限")
                total_bytes += len(payload)
                if hashlib.sha256(payload).hexdigest() != asset.sha256:
                    raise DomainError(f"派生题图哈希不一致：{asset.id}")
                result.append(
                    {
                        "id": asset.id,
                        "role": "derived_figure",
                        "sha256": asset.sha256,
                        "mime_type": asset.mime_type,
                        "size_bytes": len(payload),
                        "width": asset.width,
                        "height": asset.height,
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    }
                )
            missing = referenced_ids - {item["id"] for item in result}
            if missing:
                raise DomainError("结构化题目引用的派生题图缺失：" + ", ".join(sorted(missing)))
            return result

    def list_unpushed(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        with self.runtime.session_factory() as s:
            statement = (
                select(SyncOperation).where(
                    SyncOperation.origin == "local",
                    SyncOperation.pushed_at.is_(None),
                )
            )
            if limit is not None:
                statement = statement.limit(limit)
            rows = s.scalars(statement).all()
            return [json.loads(r.payload_json) for r in rows]

    def push_operations(self) -> dict[str, Any]:
        provider = self._require_ops_provider()
        github_batch = not isinstance(provider, LocalFolderProvider)
        ops = self.list_unpushed(
            limit=MAX_REMOTE_OPERATIONS_PER_BATCH + 1 if github_batch else None
        )
        if not ops:
            return {"pushed": 0}
        if github_batch and len(ops) > MAX_REMOTE_OPERATIONS_PER_BATCH:
            raise DomainError("待推送 Operation 批次记录过多")
        device_id = self.runtime.identity.device_id
        if not provider.acquire_lock(self.owner, self.repo, device_id):
            raise DomainError("无法获取同步锁")
        try:
            if github_batch:
                self._push_github_batch(provider, ops)
                now = _utcnow()
                with self.runtime.session_factory() as s:
                    for op in ops:
                        row = s.get(SyncOperation, op["operation_id"])
                        if row:
                            row.pushed_at = now
                    s.commit()
                return {"pushed": len(ops)}
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

    def _known_applied_operations(
        self, operation_ids: set[str]
    ) -> dict[str, dict[str, Any]]:
        """查询本次拉取涉及的已应用 Operation，并返回其不可变载荷。"""

        if not operation_ids:
            return {}
        ordered = sorted(operation_ids)
        known: dict[str, dict[str, Any]] = {}
        with self.runtime.session_factory() as session:
            for offset in range(0, len(ordered), SYNC_OPERATION_ID_QUERY_BATCH):
                batch = ordered[offset : offset + SYNC_OPERATION_ID_QUERY_BATCH]
                rows = session.execute(
                    select(SyncOperation.id, SyncOperation.payload_json).where(
                        SyncOperation.id.in_(batch),
                        SyncOperation.applied_at.is_not(None),
                    )
                ).all()
                for operation_id, payload_json in rows:
                    try:
                        payload = json.loads(payload_json)
                        known[operation_id] = validate_operation(payload)
                    except (json.JSONDecodeError, DomainError) as exc:
                        raise DomainError(
                            f"本地已应用 Operation 载荷损坏：{operation_id}"
                        ) from exc
        return known

    def pull_and_merge(self) -> dict[str, Any]:
        provider = self._require_ops_provider()
        snapshot = self._local_snapshot_before_merge()
        remote_ops = (
            provider.list_remote_operations(
                self.owner, self.repo, exclude_device=self.runtime.identity.device_id
            )
            if isinstance(provider, LocalFolderProvider)
            else self._github_remote_operations(provider)
        )
        applied = 0
        auto_merged = 0
        conflict_items = 0
        session_id: str | None = None

        # 先按 Operation ID 去重。相同 ID 必须代表完全相同的不可变内容；
        # 否则远端来源存在歧义，不能静默选择其中一份继续合并。
        incoming: dict[str, dict[str, Any]] = {}
        for raw in remote_ops:
            try:
                op = validate_operation(raw)
            except DomainError:
                continue
            if op["entity_type"] != "problem":
                # v1 的本地持久化模型预留了其他实体类型，但当前合并器只
                # 实现题目；不能把 asset/tag/review 补丁误套到 Problem。
                continue
            operation_id = op["operation_id"]
            previous = incoming.get(operation_id)
            if previous is not None:
                if previous != op:
                    raise DomainError(f"远端 Operation ID 内容冲突：{operation_id}")
                continue
            incoming[operation_id] = op

        known = self._known_applied_operations(set(incoming))
        for operation_id, stored in known.items():
            if incoming[operation_id] != stored:
                raise DomainError(f"已应用 Operation ID 内容冲突：{operation_id}")

        # 按实体分组
        by_entity: dict[str, list[dict[str, Any]]] = {}
        ordered_incoming = sorted(
            incoming.items(),
            key=lambda item: (
                item[1]["timestamp"],
                int(item[1].get("new_revision") or 0),
                item[0],
            ),
        )
        for operation_id, op in ordered_incoming:
            if operation_id in known:
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
                    self._apply_operation_attachments(s, problem, ops)
                    for op in ops:
                        self._store_remote_op(s, op, applied=True)
                    s.commit()
                    return {"applied": len(ops), "auto": len(fields), "conflicts": 0}
                for op in ops:
                    self._store_remote_op(s, op, applied=False)
                s.commit()
                return {"applied": 0, "auto": 0, "conflicts": 0}

            tag_names = [t.name for t in problem.tags]
            self._apply_operation_attachments(s, problem, ops)
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

    def _apply_operation_attachments(
        self, session, problem: Problem, operations: list[dict[str, Any]]
    ) -> None:
        """Materialize only derived figures referenced by the accompanying blocks."""

        from yancuo_win.application.question_content import load_question_content

        referenced_ids: set[str] = set()
        attachments: dict[str, dict[str, Any]] = {}
        for operation in operations:
            content_json = (operation.get("changed_fields") or {}).get("question_content_json")
            if isinstance(content_json, str):
                referenced_ids.update(
                    str(block.get("derived_asset_id"))
                    for block in load_question_content(content_json)
                    if block.get("derived_asset_id")
                )
            for attachment in operation.get("attachments") or []:
                if isinstance(attachment, dict) and attachment.get("id"):
                    attachments[str(attachment["id"])] = attachment
        for asset_id in sorted(referenced_ids):
            attachment = attachments.get(asset_id)
            if attachment is None:
                existing = session.get(Asset, asset_id)
                if existing is not None and existing.problem_id == problem.id:
                    continue
                raise DomainError(f"同步 Operation 缺少派生题图附件：{asset_id}")
            payload = base64.b64decode(str(attachment["content_base64"]), validate=True)
            expected = str(attachment["sha256"])
            if hashlib.sha256(payload).hexdigest() != expected:
                raise DomainError(f"同步派生题图哈希不一致：{asset_id}")
            existing = session.get(Asset, asset_id)
            if existing is not None:
                if existing.problem_id != problem.id or existing.sha256 != expected:
                    raise DomainError(f"同步派生题图 ID 冲突：{asset_id}")
                continue
            mime_type = str(attachment.get("mime_type") or "image/png")
            suffix = {
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "image/gif": ".gif",
                "image/bmp": ".bmp",
            }.get(mime_type, ".png")
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(payload)
                temporary = Path(handle.name)
            try:
                stored = self.store.store_copy(temporary, role="derived_figure")
            finally:
                temporary.unlink(missing_ok=True)
            if stored.sha256 != expected:
                raise DomainError(f"同步派生题图落盘校验失败：{asset_id}")
            session.add(
                Asset(
                    id=asset_id,
                    problem_id=problem.id,
                    role="derived_figure",
                    sha256=stored.sha256,
                    relative_path=stored.relative_path,
                    mime_type=mime_type,
                    size_bytes=len(payload),
                    width=attachment.get("width"),
                    height=attachment.get("height"),
                    is_immutable=True,
                )
            )

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

    def _create_remote_problem(self, session, entity_id: str, fields: dict[str, Any]) -> Problem:
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
