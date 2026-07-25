"""云备份编排：先上传完整 ebpack 并校验，再更新 latest 指针。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import gc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.cloud.base import CloudProvider
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.domain.rules import DomainError
from yancuo_win.domain.identity import bind_profile, record_snapshot_head
from yancuo_win.import_export.ebpack import EbpackService


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
        # GitLink：仅探测访问
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
        # Old installations exposed one repository-wide latest pointer. Keep it
        # readable but never silently assign it to a newly generated profile.
        if latest.get("tag"):
            index["legacy_latest"] = latest
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
            "legacy_latest_available": bool(self._profile_index().get("legacy_latest")),
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
            tag = f"data-v1-{profile_id}-{stamp}-{device_id[-8:]}-{snapshot_id[-8:]}"
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

            # GitLink：先附件后 Release；LocalFolder：先建目录再拷文件
            if caps.assets_first:
                asset_info = self.provider.upload_release_asset(
                    self.owner,
                    self.repo,
                    tag=tag,
                    file_path=pack,
                    asset_name=asset_name,
                )
                release = self.provider.create_release(
                    self.owner,
                    self.repo,
                    tag=tag,
                    name=release_name,
                    body=release_body,
                )
            else:
                release = self.provider.create_release(
                    self.owner,
                    self.repo,
                    tag=tag,
                    name=release_name,
                    body=release_body,
                )
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
            latest = {
                "format": "graduate-mistake-book-latest",
                "format_version": 1,
                "tag": tag,
                "asset_name": asset_name,
                "sha256": sha,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "device_id": device_id,
                "database_id": self.runtime.identity.database_id,
                "schema_version": self.runtime.schema_version,
                "primary_device": device_id,
                "size": pack.stat().st_size,
                "asset": asset_info,
            }
            # 完整包就绪后才写资料索引；legacy 字段保留给旧客户端读取。
            index["legacy_latest"] = latest
            self.provider.write_sync_manifest(self.owner, self.repo, index)
            self.runtime.identity = record_snapshot_head(
                self.runtime.paths.identity_file, self.runtime.identity, snapshot_id
            )
            return {
                "tag": tag,
                "sha256": sha,
                "latest": snapshot,
                "release": release.tag,
                "profile_id": profile_id,
            }
        finally:
            # 无论导出、上传或写 latest 哪一步失败，都释放主写入锁；
            # LocalFolder 的 TTL 只是最后一道兜底，不替代显式释放。
            self.provider.release_lock(self.owner, self.repo, device_id)

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
                    "is_latest": any(
                        isinstance(item, dict) and item.get("tag") == rel.tag
                        for item in latest_profiles.values()
                    ),
                }
            )
        return rows

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
        self.provider.download_release_asset(
            self.owner, self.repo, tag=tag, asset_name=asset_name, dest=dest
        )
        actual = _sha256(dest)
        if expected and actual != expected:
            dest.unlink(missing_ok=True)
            raise DomainError("下载文件哈希与 latest 记录不一致，已删除损坏文件")
        return dest

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
        return self.ebpack.restore_ebpack(pack, Path(target_root))

    @staticmethod
    def _snapshot_rows(connection: sqlite3.Connection, table: str) -> dict[str, tuple[Any, ...]]:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is None:
            return {}
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        if "id" not in columns:
            return {}
        return {
            str(row[0]): tuple(row)
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
        }

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
            tables = ("problems", "note_documents", "study_records")
            summary: dict[str, dict[str, Any]] = {}
            local = sqlite3.connect(self.runtime.paths.database)
            remote = sqlite3.connect(remote_database)
            try:
                for table in tables:
                    local_rows = self._snapshot_rows(local, table)
                    remote_rows = self._snapshot_rows(remote, table)
                    shared = set(local_rows) & set(remote_rows)
                    conflicts = sorted(
                        row_id
                        for row_id in shared
                        if local_rows[row_id] != remote_rows[row_id]
                    )
                    summary[table] = {
                        "local": len(local_rows),
                        "remote": len(remote_rows),
                        "new_remote": len(set(remote_rows) - set(local_rows)),
                        "identical": len(shared) - len(conflicts),
                        "conflicts": len(conflicts),
                        "conflict_ids": conflicts[:20],
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

    def restore_latest_to(self, target_root: Path) -> dict[str, Any]:
        return self.restore_profile_to(self.runtime.identity.profile_id, target_root)
