"""本地文件夹云端模拟：U 盘 / 同步盘目录，完整可测。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yancuo_win.cloud.base import (
    CloudCapabilities,
    CloudProvider,
    CloudUser,
    RemoteRelease,
)
from yancuo_win.domain.rules import DomainError


class LocalFolderProvider(CloudProvider):
    name = "local_folder"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _repo_dir(self, owner: str, repo: str) -> Path:
        path = self.root / owner / repo
        path.mkdir(parents=True, exist_ok=True)
        (path / ".mistakebook").mkdir(exist_ok=True)
        (path / "releases").mkdir(exist_ok=True)
        (path / "locks").mkdir(exist_ok=True)
        return path

    def authenticate(self) -> None:
        if not self.root.exists():
            raise DomainError(f"本地云目录不存在：{self.root}")

    def get_current_user(self) -> CloudUser:
        return CloudUser(login="local", display_name="本地文件夹")

    def list_repositories(self) -> list[dict[str, Any]]:
        repos = []
        if not self.root.exists():
            return repos
        for owner_dir in self.root.iterdir():
            if not owner_dir.is_dir():
                continue
            for repo_dir in owner_dir.iterdir():
                if repo_dir.is_dir():
                    repos.append(
                        {
                            "owner": owner_dir.name,
                            "name": repo_dir.name,
                            "private": True,
                            "path": str(repo_dir),
                        }
                    )
        return repos

    def create_private_repository(self, name: str) -> dict[str, Any]:
        owner = "local"
        path = self._repo_dir(owner, name)
        meta = {
            "format": "graduate-mistake-book",
            "repository_id": f"local_{owner}_{name}",
            "format_version": 1,
            "created_by_app": "1.0.0",
        }
        (path / ".mistakebook" / "repository.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {"owner": owner, "name": name, "private": True, "path": str(path)}

    def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        path = self._repo_dir(owner, name)
        repo_json = path / ".mistakebook" / "repository.json"
        meta = {}
        if repo_json.is_file():
            meta = json.loads(repo_json.read_text(encoding="utf-8"))
        return {"owner": owner, "name": name, "private": True, "meta": meta, "path": str(path)}

    def read_sync_manifest(self, owner: str, repo: str) -> dict[str, Any] | None:
        path = self._repo_dir(owner, repo) / ".mistakebook" / "latest.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_sync_manifest(self, owner: str, repo: str, manifest: dict[str, Any]) -> None:
        # 先写临时再替换，避免半写入
        path = self._repo_dir(owner, repo) / ".mistakebook" / "latest.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def list_releases(self, owner: str, repo: str) -> list[RemoteRelease]:
        releases_dir = self._repo_dir(owner, repo) / "releases"
        items: list[RemoteRelease] = []
        for d in sorted(releases_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            meta_path = d / "release.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            assets = [
                {"name": f.name, "path": str(f), "size": f.stat().st_size}
                for f in d.iterdir()
                if f.is_file() and f.name != "release.json"
            ]
            items.append(
                RemoteRelease(
                    tag=d.name,
                    name=str(meta.get("name") or d.name),
                    assets=assets,
                    raw=meta,
                )
            )
        return items

    def create_release(
        self, owner: str, repo: str, *, tag: str, name: str, body: str = ""
    ) -> RemoteRelease:
        d = self._repo_dir(owner, repo) / "releases" / tag
        if d.exists():
            raise DomainError(f"Release 已存在：{tag}")
        d.mkdir(parents=True)
        meta = {
            "tag": tag,
            "name": name,
            "body": body,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (d / "release.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return RemoteRelease(tag=tag, name=name, assets=[], raw=meta)

    def upload_release_asset(
        self, owner: str, repo: str, *, tag: str, file_path: Path, asset_name: str
    ) -> dict[str, Any]:
        d = self._repo_dir(owner, repo) / "releases" / tag
        if not d.is_dir():
            raise DomainError("Release 不存在")
        dest = d / asset_name
        # 先写临时名再改名
        tmp = d / f".{asset_name}.uploading"
        shutil.copy2(file_path, tmp)
        tmp.replace(dest)
        return {"name": asset_name, "path": str(dest), "size": dest.stat().st_size}

    def download_release_asset(
        self, owner: str, repo: str, *, tag: str, asset_name: str, dest: Path
    ) -> Path:
        src = self._repo_dir(owner, repo) / "releases" / tag / asset_name
        if not src.is_file():
            raise DomainError(f"附件不存在：{asset_name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    def delete_release(self, owner: str, repo: str, *, tag: str) -> None:
        d = self._repo_dir(owner, repo) / "releases" / tag
        if d.exists():
            shutil.rmtree(d)

    def acquire_lock(self, owner: str, repo: str, device_id: str) -> bool:
        lock = self._repo_dir(owner, repo) / "locks" / "primary.json"
        if lock.is_file():
            data = json.loads(lock.read_text(encoding="utf-8"))
            if data.get("device_id") and data.get("device_id") != device_id:
                return False
        payload = {
            "device_id": device_id,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        lock.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True

    def release_lock(self, owner: str, repo: str, device_id: str) -> None:
        lock = self._repo_dir(owner, repo) / "locks" / "primary.json"
        if not lock.is_file():
            return
        data = json.loads(lock.read_text(encoding="utf-8"))
        if data.get("device_id") == device_id:
            lock.unlink()

    def test_connection(self) -> dict[str, Any]:
        self.authenticate()
        return {"ok": True, "provider": self.name, "root": str(self.root)}

    def get_capabilities(self) -> CloudCapabilities:
        return CloudCapabilities(
            private_repository=True,
            release_assets=True,
            atomic_file_update=True,
            oauth=False,
            large_file_upload=True,
            delete_release=True,
            max_asset_bytes=None,
        )
