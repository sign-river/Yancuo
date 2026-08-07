"""云备份编排：先上传完整 ebpack 并校验，再更新 latest 指针。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import gc
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid
import zipfile

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.search_service import SearchIndexService
from yancuo_win.application.unified_search_service import UnifiedSearchIndexService
from yancuo_win.cloud.base import CloudProvider
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.domain.rules import DomainError
from yancuo_win.domain.identity import bind_profile, record_snapshot_head
from yancuo_win.data.models import Base
from yancuo_win.import_export.ebpack import EbpackService, FORMAT_NAME


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class CloudBackupService:
    def __init__(self, runtime: RuntimeContext, provider: CloudProvider | None = None) -> None:
        self.runtime = runtime
        self.provider = provider or get_cloud_provider(runtime.settings)
        self.ebpack = EbpackService(runtime)

    @property
    def owner(self) -> str:
        return (self.runtime.settings.cloud.repository.owner or "local").strip()

    @property
    def repo(self) -> str:
        return (self.runtime.settings.cloud.repository.name or "graduate-mistake-book-data").strip()

    def test_connection(self) -> dict[str, Any]:
        return self.provider.test_connection()

    def ensure_repository(self) -> dict[str, Any]:
        if self.provider.name == "local_folder":
            return self.provider.create_private_repository(self.repo)
        return self.provider.get_repository(self.owner, self.repo)

    @staticmethod
    def _release_metadata(release: Any) -> dict[str, Any]:
        raw = getattr(release, "raw", {})
        body = raw.get("body") if isinstance(raw, dict) else None
        if not isinstance(body, str):
            return {}
        try:
            value = json.loads(body)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _profile_index(self) -> dict[str, Any]:
        """Read the mutable pointer while accepting the legacy latest format."""

        latest = self.provider.read_sync_manifest(self.owner, self.repo) or {}
        if latest.get("format") == "yancuo-profile-snapshots" and isinstance(
            latest.get("profiles"), dict
        ):
            return latest
        index: dict[str, Any] = {
            "format": "yancuo-profile-snapshots",
            "format_version": 1,
            "profiles": {},
            "aliases": {},
        }
        return index

    @staticmethod
    def _resolve_profile(index: dict[str, Any], profile_id: str) -> str:
        aliases = index.get("aliases")
        if not isinstance(aliases, dict):
            return profile_id
        seen: set[str] = set()
        current = profile_id
        while isinstance(aliases.get(current), str) and aliases[current] != current:
            if current in seen:
                raise DomainError("云端资料别名映射存在循环")
            seen.add(current)
            current = aliases[current]
        return current

    def discover_profiles(self) -> list[dict[str, Any]]:
        """Return remote profile namespaces without changing local bindings."""

        index = self._profile_index()
        profiles = index.get("profiles")
        rows: dict[str, dict[str, Any]] = {}
        if isinstance(profiles, dict):
            for profile_id, snapshot in profiles.items():
                if isinstance(profile_id, str) and isinstance(snapshot, dict):
                    rows[profile_id] = {
                        "profile_id": profile_id,
                        "canonical_profile_id": self._resolve_profile(index, profile_id),
                        **snapshot,
                    }
        # The index may have been lost or created by an older client. Release
        # metadata is immutable, so it is a safe fallback for discovery.
        for release in self.provider.list_releases(self.owner, self.repo):
            metadata = self._release_metadata(release)
            profile_id = metadata.get("profile_id")
            if isinstance(profile_id, str) and profile_id not in rows:
                rows[profile_id] = {
                    "profile_id": profile_id,
                    "canonical_profile_id": self._resolve_profile(index, profile_id),
                    "tag": release.tag,
                    "asset_name": str(metadata.get("asset_name") or "snapshot.ebpack"),
                    "sha256": metadata.get("sha256"),
                    "snapshot_id": metadata.get("snapshot_id"),
                    "parent_snapshot_id": metadata.get("parent_snapshot_id"),
                    "device_id": metadata.get("device_id"),
                    "uploaded_at": metadata.get("uploaded_at"),
                }
        return sorted(rows.values(), key=lambda row: str(row.get("uploaded_at") or ""), reverse=True)

    def profile_connection_state(self) -> dict[str, Any]:
        """Describe whether explicit restore, binding, or merge is required."""

        local = self.runtime.identity.profile_id
        remote = self.discover_profiles()
        remote_ids = {str(item["profile_id"]) for item in remote}
        index = self._profile_index()
        aliases = index.get("aliases") or {}
        canonical = self._resolve_profile(index, local)
        profiles = index.get("profiles") or {}
        remote_snapshot = profiles.get(canonical) if isinstance(profiles, dict) else {}
        remote_snapshot_id = (
            str(remote_snapshot.get("snapshot_id") or "")
            if isinstance(remote_snapshot, dict)
            else ""
        )
        known_snapshot_id = self.runtime.identity.last_snapshot_id
        return {
            "local_profile_id": local,
            "canonical_profile_id": canonical,
            "local_is_aliased": canonical != local,
            "known_snapshot_id": known_snapshot_id,
            "remote_snapshot_id": remote_snapshot_id,
            "branch_detected": bool(
                known_snapshot_id
                and remote_snapshot_id
                and known_snapshot_id != remote_snapshot_id
            ),
            "remote_profiles": remote,
            "requires_takeover": bool(remote_ids - {local, canonical}),
            "aliases": aliases,
        }

    def bind_local_profile(self, profile_id: str) -> dict[str, str]:
        """Persist an explicitly confirmed cloud profile binding on this device."""

        previous_profile_id = self.runtime.identity.profile_id
        index = self._profile_index()
        canonical = self._resolve_profile(index, profile_id)
        profiles = index.get("profiles")
        if not isinstance(profiles, dict) or canonical not in profiles:
            raise DomainError("云端资料不存在，不能绑定")
        self.runtime.identity = bind_profile(
            self.runtime.paths.identity_file,
            self.runtime.identity,
            canonical,
        )
        snapshot_id = str(profiles[canonical].get("snapshot_id") or "")
        if snapshot_id:
            self.runtime.identity = record_snapshot_head(
                self.runtime.paths.identity_file, self.runtime.identity, snapshot_id
            )
        return {"previous_profile_id": previous_profile_id, "profile_id": canonical}

    def record_profile_alias(self, source_profile_id: str, canonical_profile_id: str) -> None:
        """Record a user-confirmed profile convergence without merging data."""

        source_profile_id = source_profile_id.strip()
        canonical_profile_id = canonical_profile_id.strip()
        if source_profile_id == canonical_profile_id:
            return
        device_id = self.runtime.identity.device_id
        if not self.provider.acquire_lock(self.owner, self.repo, device_id):
            raise DomainError("无法获取主写入锁：另一台设备可能正在修改云端资料")
        try:
            index = self._profile_index()
            profiles = index.setdefault("profiles", {})
            if not isinstance(profiles, dict) or canonical_profile_id not in profiles:
                raise DomainError("主资料不存在，不能创建资料别名")
            aliases = index.setdefault("aliases", {})
            if not isinstance(aliases, dict):
                raise DomainError("云端资料别名记录无效")
            aliases[source_profile_id] = canonical_profile_id
            self._resolve_profile(index, source_profile_id)
            self.provider.write_sync_manifest(self.owner, self.repo, index)
        finally:
            self.provider.release_lock(self.owner, self.repo, device_id)

    def upload_backup(self) -> dict[str, Any]:
        """手动云备份：上传完整包成功后才更新 latest。"""
        if not self.runtime.settings.cloud.enabled and self.provider.name != "local_folder":
            raise DomainError("请先在设置中启用云端备份（cloud.enabled）")

        caps = self.provider.get_capabilities()
        if not caps.release_assets and self.provider.name not in ("local_folder",):
            raise DomainError(
                "当前提供商不支持 Release 附件。请改用 local_folder，或检查云端适配器。"
            )

        device_id = self.runtime.identity.device_id
        if not self.provider.acquire_lock(self.owner, self.repo, device_id):
            raise DomainError("无法获取主写入锁：另一台设备可能是主编辑设备")
        created_tag: str | None = None
        manifest_published = False
        pack: Path | None = None
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            profile_id = self.runtime.identity.profile_id
            index = self._profile_index()
            canonical_profile_id = self._resolve_profile(index, profile_id)
            if canonical_profile_id != profile_id:
                raise DomainError("此本地资料已绑定到另一主资料，请确认后再上传")
            snapshot_id = f"snapshot_{uuid.uuid4().hex}"
            profile_snapshots = index.setdefault("profiles", {})
            if not isinstance(profile_snapshots, dict):
                raise DomainError("云端资料索引无效")
            previous = profile_snapshots.get(profile_id)
            remote_snapshot_id = (
                str(previous.get("snapshot_id") or "")
                if isinstance(previous, dict)
                else ""
            )
            known_snapshot_id = self.runtime.identity.last_snapshot_id
            if remote_snapshot_id and known_snapshot_id != remote_snapshot_id:
                raise DomainError(
                    "云端资料已在其他设备更新；请先恢复最新快照或进行资料合并确认"
                )
            parent_snapshot_id = (
                previous.get("snapshot_id") if isinstance(previous, dict) else None
            )
            tag = f"data-v1-{profile_id[-8:]}-{stamp}-{device_id[-8:]}-{snapshot_id[-8:]}"
            pack = self.ebpack.export_ebpack(
                self.runtime.paths.cache_dir / f"{tag}.ebpack"
            )
            sha = _sha256(pack)
            asset_name = "snapshot.ebpack"
            release_name = f"研错库数据备份 · {stamp}"
            release_body = json.dumps(
                {
                    "format": "yancuo-profile-snapshot",
                    "format_version": 1,
                    "profile_id": profile_id,
                    "snapshot_id": snapshot_id,
                    "parent_snapshot_id": parent_snapshot_id,
                    "sha256": sha,
                    "asset_name": asset_name,
                    "uploaded_at": datetime.now(timezone.utc).isoformat(),
                    "device_id": device_id,
                    "database_id": self.runtime.identity.database_id,
                    "schema_version": self.runtime.schema_version,
                },
                ensure_ascii=False,
            )

            release = self.provider.create_release(
                self.owner,
                self.repo,
                tag=tag,
                name=release_name,
                body=release_body,
            )
            created_tag = tag
            asset_info = self.provider.upload_release_asset(
                self.owner,
                self.repo,
                tag=tag,
                file_path=pack,
                asset_name=asset_name,
            )

            if _sha256(pack) != sha:
                raise DomainError("上传前后哈希不一致，已中止更新 latest")

            snapshot = {
                "tag": tag,
                "asset_name": asset_name,
                "sha256": sha,
                "snapshot_id": snapshot_id,
                "parent_snapshot_id": parent_snapshot_id,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "device_id": device_id,
                "database_id": self.runtime.identity.database_id,
                "schema_version": self.runtime.schema_version,
                "size": pack.stat().st_size,
                "asset": asset_info,
            }
            profile_snapshots[profile_id] = snapshot
            index["updated_at"] = datetime.now(timezone.utc).isoformat()
            index["primary_profile_id"] = profile_id
            # 完整包就绪后才写资料索引；legacy 字段保留给旧客户端读取。
            self.provider.write_sync_manifest(self.owner, self.repo, index)
            manifest_published = True
            self.runtime.identity = record_snapshot_head(
                self.runtime.paths.identity_file, self.runtime.identity, snapshot_id
            )
            return {
                "tag": tag,
                "sha256": sha,
                "snapshot_id": snapshot_id,
                "latest": snapshot,
                "release": release.tag,
                "profile_id": profile_id,
            }
        except Exception as exc:
            if created_tag is not None and not manifest_published:
                try:
                    self.provider.delete_release(
                        self.owner, self.repo, tag=created_tag
                    )
                except Exception as cleanup_exc:
                    raise DomainError(
                        "云快照发布失败，且清理未入索引的 Release 失败："
                        f"{cleanup_exc}"
                    ) from exc
            raise
        finally:
            # 无论导出、上传或写 latest 哪一步失败，都释放主写入锁；
            # LocalFolder 的 TTL 只是最后一道兜底，不替代显式释放。
            try:
                self.provider.release_lock(self.owner, self.repo, device_id)
            finally:
                if pack is not None:
                    pack.unlink(missing_ok=True)

    def storage_usage(self) -> dict[str, Any] | None:
        """Return cloud storage usage/quota; None when the backend has no quota API."""
        try:
            return self.provider.get_storage_usage(self.owner, self.repo)
        except DomainError:
            return None

    def list_backups(self) -> list[dict[str, Any]]:
        releases = self.provider.list_releases(self.owner, self.repo)
        index = self._profile_index()
        latest_profiles = index.get("profiles") if isinstance(index.get("profiles"), dict) else {}
        rows = []
        for rel in releases:
            if rel.tag == "latest-pointer":
                continue
            metadata = self._release_metadata(rel)
            profile_id = metadata.get("profile_id")
            rows.append(
                {
                    "tag": rel.tag,
                    "name": rel.name,
                    "assets": rel.assets,
                    "profile_id": profile_id,
                    "snapshot_id": metadata.get("snapshot_id"),
                    "parent_snapshot_id": metadata.get("parent_snapshot_id"),
                    "uploaded_at": metadata.get("uploaded_at"),
                    "asset_size": int(rel.assets[0].get("size") or 0)
                    if rel.assets and isinstance(rel.assets[0], dict)
                    else 0,
                    "is_latest": any(
                        isinstance(item, dict) and item.get("tag") == rel.tag
                        for item in latest_profiles.values()
                    ),
                }
            )
        return rows

    def delete_backup(self, tag: str) -> dict[str, Any]:
        """删除远端单个备份（Release 与索引引用），本地数据不受影响。"""
        if not tag or tag == "latest-pointer":
            raise DomainError("无效的备份标识")
        index = self._profile_index()
        removed_profiles: list[str] = []
        profiles = index.get("profiles")
        if isinstance(profiles, dict):
            for profile_id, snapshot in list(profiles.items()):
                if isinstance(snapshot, dict) and snapshot.get("tag") == tag:
                    profiles.pop(profile_id, None)
                    removed_profiles.append(profile_id)
        self.provider.delete_release(self.owner, self.repo, tag=tag)
        if removed_profiles:
            index["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.provider.write_sync_manifest(self.owner, self.repo, index)
        return {"tag": tag, "removed_profiles": removed_profiles}

    def cleanup_backups(self, retain: int = 10) -> list[dict[str, Any]]:
        """按资料档保留最近 retain 份快照，删除更旧的远端备份。"""
        if retain < 1:
            raise DomainError("保留份数至少为 1")
        backups = self.list_backups()
        by_profile: dict[str, list[dict[str, Any]]] = {}
        for backup in backups:
            by_profile.setdefault(str(backup.get("profile_id") or "unknown"), []).append(
                backup
            )
        deleted: list[dict[str, Any]] = []
        for profile_id, rows in by_profile.items():
            # uploaded_at 含微秒，比 tag 内嵌秒级时间戳更精确
            rows.sort(
                key=lambda item: str(item.get("uploaded_at") or item.get("tag") or ""),
                reverse=True,
            )
            for old in rows[retain:]:
                tag = str(old["tag"])
                self.delete_backup(tag)
                deleted.append({"tag": tag, "profile_id": profile_id})
        return deleted

    def download_backup(self, tag: str, dest_dir: Path) -> Path:
        index = self._profile_index()
        profiles = index.get("profiles", {})
        latest = next(
            (
                snapshot
                for snapshot in profiles.values()
                if isinstance(snapshot, dict) and snapshot.get("tag") == tag
            ),
            {},
        ) if isinstance(profiles, dict) else {}
        asset_name = "snapshot.ebpack"
        if latest.get("tag") == tag:
            asset_name = str(latest.get("asset_name") or asset_name)
            expected = latest.get("sha256")
        else:
            expected = None
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{tag}.ebpack"
        if dest.is_symlink():
            raise DomainError("云备份下载目标不能是符号链接")
        descriptor, candidate_name = tempfile.mkstemp(
            prefix=".cloud-verify-", suffix=".ebpack", dir=dest_dir
        )
        os.close(descriptor)
        candidate = Path(candidate_name)
        try:
            self.provider.download_release_asset(
                self.owner,
                self.repo,
                tag=tag,
                asset_name=asset_name,
                dest=candidate,
            )
            actual = _sha256(candidate)
            if expected and actual != expected:
                raise DomainError("下载文件哈希与 latest 记录不一致，未替换已有文件")
            if dest.is_symlink():
                raise DomainError("云备份下载目标不能是符号链接")
            os.replace(candidate, dest)
        finally:
            candidate.unlink(missing_ok=True)
        return dest

    def preview_backup(self, tag: str) -> dict[str, Any]:
        """下载远端备份到缓存并读取包内清单，不覆盖本地数据。"""
        backups = self.list_backups()
        row = next(
            (item for item in backups if str(item.get("tag") or "") == tag), None
        )
        if row is None:
            raise DomainError("云端不存在该备份")
        pack = self.download_backup(
            tag, self.runtime.paths.cache_dir / "cloud_preview"
        )
        try:
            try:
                with zipfile.ZipFile(pack, "r") as zf:
                    raw = zf.read("manifest.json")
                manifest = json.loads(raw.decode("utf-8"))
            except (
                zipfile.BadZipFile,
                KeyError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                raise DomainError("备份包清单无效，无法预览") from exc
            if manifest.get("format") != FORMAT_NAME:
                raise DomainError("备份包不是研错库 ebpack，无法预览")
            return {
                "tag": tag,
                "profile_id": row.get("profile_id"),
                "uploaded_at": row.get("uploaded_at"),
                "asset_size": row.get("asset_size"),
                "is_latest": row.get("is_latest"),
                "created_at": manifest.get("created_at"),
                "problem_count": manifest.get("problem_count"),
                "note_count": manifest.get("note_count"),
                "asset_count": manifest.get("asset_count"),
                "schema_version": manifest.get("schema_version"),
                "data_format_version": manifest.get("data_format_version"),
                "app_version": manifest.get("app_version"),
            }
        finally:
            pack.unlink(missing_ok=True)

    def restore_profile_to(self, profile_id: str, target_root: Path) -> dict[str, Any]:
        """Restore a selected remote profile to a user-chosen directory."""

        index = self._profile_index()
        canonical = self._resolve_profile(index, profile_id)
        profiles = index.get("profiles")
        snapshot = profiles.get(canonical) if isinstance(profiles, dict) else None
        if not isinstance(snapshot, dict) or not snapshot.get("tag"):
            raise DomainError("所选云端资料没有可恢复的快照")
        pack = self.download_backup(
            str(snapshot["tag"]), self.runtime.paths.cache_dir / "cloud_dl"
        )
        try:
            return self.ebpack.restore_ebpack(pack, Path(target_root))
        finally:
            pack.unlink(missing_ok=True)

    @staticmethod
    def _table_key_columns(connection: sqlite3.Connection, table: str) -> list[str]:
        return [
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
            if int(row[5]) > 0
        ]

    @classmethod
    def _snapshot_rows(
        cls, connection: sqlite3.Connection, table: str
    ) -> dict[str, dict[str, Any]]:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            return {}
        key_columns = cls._table_key_columns(connection, table)
        if not key_columns:
            return {}
        connection.row_factory = sqlite3.Row
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
        return {
            "|".join(str(row[column]) for column in key_columns): dict(row)
            for row in rows
        }

    @staticmethod
    def _authoritative_tables(connection: sqlite3.Connection) -> list[str]:
        """Return persistent business tables in FK-safe metadata order."""

        # Search projections and recognition cache are rebuildable. Prompts are
        # machine-local provider defaults keyed by name, not profile content;
        # two fresh profiles legitimately contain different IDs for the same
        # default key and must keep their local configuration.
        derived = {
            "meta_kv",
            "search_documents",
            "unified_search_documents",
            "ai_recognition_cache",
            "prompts",
        }
        available = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        return [
            table.name
            for table in Base.metadata.sorted_tables
            if table.name in available and table.name not in derived
        ]

    @classmethod
    def _field_conflicts(
        cls, local_row: dict[str, Any], remote_row: dict[str, Any], key_columns: list[str]
    ) -> list[str]:
        return sorted(
            column
            for column in set(local_row) | set(remote_row)
            if column not in key_columns and local_row.get(column) != remote_row.get(column)
        )

    def preview_profile_merge(self, profile_id: str) -> dict[str, Any]:
        """Compare a remote profile with this data root without mutating either."""

        if profile_id == self.runtime.identity.profile_id:
            raise DomainError("当前资料无需与自身合并")
        with tempfile.TemporaryDirectory(dir=self.runtime.paths.cache_dir) as temporary:
            remote_root = Path(temporary) / "remote"
            restored = self.restore_profile_to(profile_id, remote_root)
            remote_database = remote_root / "error_book.db"
            if not remote_database.is_file():
                raise DomainError("远端资料恢复后缺少数据库")
            summary: dict[str, dict[str, Any]] = {}
            local = sqlite3.connect(self.runtime.paths.database)
            remote = sqlite3.connect(remote_database)
            try:
                tables = self._authoritative_tables(local)
                for table in tables:
                    local_rows = self._snapshot_rows(local, table)
                    remote_rows = self._snapshot_rows(remote, table)
                    shared = set(local_rows) & set(remote_rows)
                    key_columns = self._table_key_columns(local, table)
                    conflict_fields = {
                        row_id: self._field_conflicts(
                            local_rows[row_id], remote_rows[row_id], key_columns
                        )
                        for row_id in shared
                    }
                    conflicts = sorted(row_id for row_id, fields in conflict_fields.items() if fields)
                    summary[table] = {
                        "local": len(local_rows),
                        "remote": len(remote_rows),
                        "new_remote": len(set(remote_rows) - set(local_rows)),
                        "identical": len(shared) - len(conflicts),
                        "conflicts": len(conflicts),
                        "conflict_ids": conflicts[:20],
                        "conflict_fields": {
                            row_id: conflict_fields[row_id] for row_id in conflicts[:20]
                        },
                    }
            finally:
                remote.close()
                local.close()
            result = {
                "profile_id": profile_id,
                "restored_schema_version": restored["schema_version"],
                "tables": summary,
                "has_conflicts": any(item["conflicts"] for item in summary.values()),
                "write_performed": False,
            }
            # Ebpack verification creates short-lived SQLite engines. Ensure
            # their Windows file handles are finalized before temp cleanup.
            gc.collect()
            return result

    def merge_profile(
        self,
        profile_id: str,
        *,
        primary_profile_id: str,
        field_choices: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Merge a selected remote profile after explicit per-field choices.

        Choice keys are ``table:primary-key-value:column`` and values are
        ``local`` or ``remote``. Omitted conflict fields intentionally keep
        the local value, so this method can never silently prefer remote data.
        """

        local_profile_id = self.runtime.identity.profile_id
        if profile_id == local_profile_id:
            raise DomainError("当前资料无需与自身合并")
        if primary_profile_id not in {local_profile_id, profile_id}:
            raise DomainError("主资料必须是当前资料或所选云端资料")
        choices = field_choices or {}

        # The remote side is already an immutable Release. Create the matching
        # immutable local side before any data mutation.
        local_snapshot = self.upload_backup()
        with tempfile.TemporaryDirectory(dir=self.runtime.paths.cache_dir) as temporary:
            remote_root = Path(temporary) / "remote"
            self.restore_profile_to(profile_id, remote_root)
            remote_database = remote_root / "error_book.db"
            if not remote_database.is_file():
                raise DomainError("远端资料恢复后缺少数据库")

            local = sqlite3.connect(self.runtime.paths.database)
            remote = sqlite3.connect(remote_database)
            copied_assets: list[Path] = []
            inserted_rows = 0
            applied_remote_fields = 0
            try:
                tables = self._authoritative_tables(local)
                local.execute("PRAGMA foreign_keys=OFF")
                local.execute("BEGIN IMMEDIATE")
                for table in tables:
                    local_rows = self._snapshot_rows(local, table)
                    remote_rows = self._snapshot_rows(remote, table)
                    key_columns = self._table_key_columns(local, table)
                    columns = [str(row[1]) for row in local.execute(f"PRAGMA table_info({table})")]
                    quoted_columns = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    for row_id, remote_row in remote_rows.items():
                        local_row = local_rows.get(row_id)
                        if local_row is None:
                            local.execute(
                                f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({placeholders})',
                                [remote_row.get(column) for column in columns],
                            )
                            inserted_rows += 1
                            continue
                        for column in self._field_conflicts(local_row, remote_row, key_columns):
                            choice_key = f"{table}:{row_id}:{column}"
                            if choices.get(choice_key) != "remote":
                                continue
                            where = " AND ".join(f'"{key}"=?' for key in key_columns)
                            local.execute(
                                f'UPDATE "{table}" SET "{column}"=? WHERE {where}',
                                [remote_row.get(column)] + [local_row[key] for key in key_columns],
                            )
                            applied_remote_fields += 1

                remote_objects = remote_root / "assets" / "objects"
                local_objects = self.runtime.paths.asset_dir / "objects"
                if remote_objects.is_dir():
                    for source in remote_objects.rglob("*"):
                        if not source.is_file():
                            continue
                        relative = source.relative_to(remote_objects)
                        target = local_objects / relative
                        if target.exists():
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, target)
                        copied_assets.append(target)

                violations = local.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise DomainError("资料合并会破坏关联关系，已取消")
                local.commit()
            except Exception:
                local.rollback()
                for path in copied_assets:
                    path.unlink(missing_ok=True)
                raise
            finally:
                local.close()
                remote.close()

        source_profile_id = profile_id if primary_profile_id == local_profile_id else local_profile_id
        self.record_profile_alias(source_profile_id, primary_profile_id)
        if primary_profile_id != self.runtime.identity.profile_id:
            self.bind_local_profile(primary_profile_id)
        # The SQLite projections are deliberately excluded from the merge.
        # Rebuild them immediately so the running UI observes merged content.
        SearchIndexService(self.runtime).rebuild()
        UnifiedSearchIndexService(self.runtime).rebuild_notes()
        return {
            "local_snapshot_id": local_snapshot["snapshot_id"],
            "primary_profile_id": primary_profile_id,
            "inserted_rows": inserted_rows,
            "remote_fields_applied": applied_remote_fields,
            "write_performed": True,
        }

    def restore_latest_to(self, target_root: Path) -> dict[str, Any]:
        return self.restore_profile_to(self.runtime.identity.profile_id, target_root)
