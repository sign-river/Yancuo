"""云备份编排：先上传完整 ebpack 并校验，再更新 latest 指针。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.cloud.base import CloudProvider
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.domain.rules import DomainError
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

    def upload_backup(self) -> dict[str, Any]:
        """手动云备份：上传完整包成功后才更新 latest。"""
        if not self.runtime.settings.cloud.enabled and self.provider.name != "local_folder":
            # 允许 local_folder 在开发时始终可用；gitlink 需 enabled
            if self.provider.name == "gitlink":
                raise DomainError("请先在设置中启用云端备份（cloud.enabled）")

        caps = self.provider.get_capabilities()
        if not caps.release_assets and self.provider.name not in ("local_folder",):
            raise DomainError(
                "当前提供商不支持 Release 附件。请改用 local_folder，或检查 GitLink 适配器。"
            )

        device_id = self.runtime.identity.device_id
        if not self.provider.acquire_lock(self.owner, self.repo, device_id):
            raise DomainError("无法获取主写入锁：另一台设备可能是主编辑设备")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tag = f"data-v1-snapshot-{stamp}"
        pack = self.ebpack.export_ebpack(
            self.runtime.paths.cache_dir / f"{tag}.ebpack"
        )
        sha = _sha256(pack)
        asset_name = "snapshot.ebpack"
        release_name = f"研错库数据备份 · {stamp}"
        release_body = json.dumps(
            {
                "sha256": sha,
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
        # 完整包就绪后再写指针
        self.provider.write_sync_manifest(self.owner, self.repo, latest)
        return {"tag": tag, "sha256": sha, "latest": latest, "release": release.tag}

    def list_backups(self) -> list[dict[str, Any]]:
        releases = self.provider.list_releases(self.owner, self.repo)
        latest = self.provider.read_sync_manifest(self.owner, self.repo) or {}
        rows = []
        for rel in releases:
            if rel.tag == "latest-pointer":
                continue
            rows.append(
                {
                    "tag": rel.tag,
                    "name": rel.name,
                    "assets": rel.assets,
                    "is_latest": latest.get("tag") == rel.tag,
                }
            )
        return rows

    def download_backup(self, tag: str, dest_dir: Path) -> Path:
        latest = self.provider.read_sync_manifest(self.owner, self.repo) or {}
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

    def restore_latest_to(self, target_root: Path) -> dict[str, Any]:
        latest = self.provider.read_sync_manifest(self.owner, self.repo)
        if not latest or not latest.get("tag"):
            raise DomainError("云端尚无 latest 备份指针")
        pack = self.download_backup(str(latest["tag"]), self.runtime.paths.cache_dir / "cloud_dl")
        return self.ebpack.restore_ebpack(pack, Path(target_root))
