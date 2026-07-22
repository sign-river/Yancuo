"""本地文件夹云端模拟：U 盘 / 同步盘目录，完整可测。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
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
    # 本地同步目录可能位于 U 盘或网络盘；锁文件无法保证进程崩溃时
    # 自动清理，因此保留一个明确的过期窗口作为最后兜底。
    LOCK_TTL_SECONDS = 15 * 60

    def __init__(self, root: Path, *, lock_ttl_seconds: float | None = None) -> None:
        self.root = Path(root)
        ttl = self.LOCK_TTL_SECONDS if lock_ttl_seconds is None else float(lock_ttl_seconds)
        if ttl <= 0:
            raise ValueError("lock_ttl_seconds 必须大于 0")
        self.lock_ttl_seconds = ttl
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_component(value: str, label: str) -> str:
        """Validate a value that is used as one filesystem path component.

        The local provider is often pointed at a shared/network directory.  Do
        not let repository, release, device, or entity identifiers escape that
        directory through ``..``, separators, drive prefixes, or NTFS ADS
        syntax.  Remote operation IDs are not trusted input either, so the same
        check is applied to every path component below.
        """

        text = str(value)
        if (
            not text
            or text in {".", ".."}
            or "\x00" in text
            or "/" in text
            or "\\" in text
            or ":" in text
        ):
            raise DomainError(f"{label} contains an unsafe path component")
        return text

    def _repo_dir(self, owner: str, repo: str) -> Path:
        owner = self._safe_component(owner, "owner")
        repo = self._safe_component(repo, "repository")
        root = self.root.resolve()
        owner_path = root / owner
        path = owner_path / repo
        # A pre-existing symlink would otherwise make the provider write
        # outside its configured root.  Refuse it instead of following it.
        if owner_path.is_symlink() or path.is_symlink():
            raise DomainError("local cloud repository path must not be a symlink")
        try:
            path.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise DomainError("local cloud repository path escapes provider root") from exc
        path.mkdir(parents=True, exist_ok=True)
        for dirname in (".mistakebook", "releases", "locks"):
            child = path / dirname
            if child.is_symlink():
                raise DomainError(f"local cloud {dirname} directory must not be a symlink")
            child.mkdir(exist_ok=True)
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
            if not owner_dir.is_dir() or owner_dir.is_symlink():
                continue
            for repo_dir in owner_dir.iterdir():
                if repo_dir.is_dir() and not repo_dir.is_symlink():
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
        if path.is_symlink():
            raise DomainError("latest.json must not be a symlink")
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_sync_manifest(self, owner: str, repo: str, manifest: dict[str, Any]) -> None:
        # 先写临时再替换，避免半写入
        path = self._repo_dir(owner, repo) / ".mistakebook" / "latest.json"
        if path.is_symlink():
            raise DomainError("latest.json must not be a symlink")
        tmp = path.with_suffix(".tmp")
        if tmp.is_symlink():
            raise DomainError("latest.json temporary path must not be a symlink")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def list_releases(self, owner: str, repo: str) -> list[RemoteRelease]:
        releases_dir = self._repo_dir(owner, repo) / "releases"
        items: list[RemoteRelease] = []
        for d in sorted(releases_dir.iterdir(), reverse=True):
            if not d.is_dir() or d.is_symlink():
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
        tag = self._safe_component(tag, "release tag")
        d = self._repo_dir(owner, repo) / "releases" / tag
        if d.is_symlink():
            raise DomainError("Release path must not be a symlink")
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
        tag = self._safe_component(tag, "release tag")
        asset_name = self._safe_component(asset_name, "asset name")
        d = self._repo_dir(owner, repo) / "releases" / tag
        if d.is_symlink():
            raise DomainError("Release path must not be a symlink")
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
        tag = self._safe_component(tag, "release tag")
        asset_name = self._safe_component(asset_name, "asset name")
        src = self._repo_dir(owner, repo) / "releases" / tag / asset_name
        if not src.is_file():
            raise DomainError(f"附件不存在：{asset_name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest

    def delete_release(self, owner: str, repo: str, *, tag: str) -> None:
        tag = self._safe_component(tag, "release tag")
        d = self._repo_dir(owner, repo) / "releases" / tag
        if d.is_symlink():
            raise DomainError("Release path must not be a symlink")
        if d.exists():
            shutil.rmtree(d)

    @staticmethod
    def _parse_lock_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _lock_expired(self, data: dict[str, Any], now: datetime) -> bool:
        # 新版本写入 expires_at；旧版本只有 acquired_at，按 TTL 推导，
        # 因而升级后遗留的锁也能自动恢复。
        expires = self._parse_lock_time(data.get("expires_at"))
        if expires is not None:
            return expires <= now
        acquired = self._parse_lock_time(data.get("acquired_at"))
        if acquired is None:
            return True
        return (now - acquired).total_seconds() >= self.lock_ttl_seconds

    @staticmethod
    def _read_lock(lock: Path) -> dict[str, Any] | None:
        try:
            raw = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    @staticmethod
    def _write_lock_atomic(lock: Path, payload: dict[str, Any]) -> None:
        """同目录临时文件 + replace，避免观察到半写入 JSON。"""
        lock.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{lock.name}.", suffix=".tmp", dir=str(lock.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            Path(tmp_name).replace(lock)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def acquire_lock(self, owner: str, repo: str, device_id: str) -> bool:
        lock = self._repo_dir(owner, repo) / "locks" / "primary.json"
        if lock.is_symlink():
            raise DomainError("sync lock must not be a symlink")
        now = datetime.now(timezone.utc)
        # 最多重试一次：删除过期锁后，可能恰好有另一进程先占用。
        for _ in range(2):
            if lock.exists() or lock.is_symlink():
                data = self._read_lock(lock)
                if data is None or self._lock_expired(data, now):
                    try:
                        lock.unlink()
                    except FileNotFoundError:
                        continue
                elif str(data.get("device_id") or "") != str(device_id):
                    return False
                else:
                    # 同一设备可重入，刷新 TTL。
                    payload = {
                        "device_id": device_id,
                        "acquired_at": now.isoformat(),
                        "expires_at": (
                            now.timestamp() + self.lock_ttl_seconds
                        ),
                    }
                    payload["expires_at"] = datetime.fromtimestamp(
                        float(payload["expires_at"]), tz=timezone.utc
                    ).isoformat()
                    self._write_lock_atomic(lock, payload)
                    return True

            payload = {
                "device_id": device_id,
                "acquired_at": now.isoformat(),
                "expires_at": datetime.fromtimestamp(
                    now.timestamp() + self.lock_ttl_seconds, tz=timezone.utc
                ).isoformat(),
            }
            try:
                # 独占创建是关键：两个设备同时看到“无锁”时，只有一个
                # 能成功创建，另一个下一轮会重新读取并返回 False。
                with lock.open("x", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                return True
            except FileExistsError:
                continue
        return False

    def release_lock(self, owner: str, repo: str, device_id: str) -> None:
        lock = self._repo_dir(owner, repo) / "locks" / "primary.json"
        if lock.is_symlink():
            raise DomainError("sync lock must not be a symlink")
        if not (lock.exists() or lock.is_symlink()):
            return
        data = self._read_lock(lock)
        # 损坏的锁无法判断归属，删除它使后续操作可以自愈；正常锁只
        # 能由持有设备释放，避免误删别人的新锁。
        if data is None or str(data.get("device_id") or "") == str(device_id):
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

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
            assets_first=False,
        )

    # —— 阶段 J：增量 Operation ——

    def _changes_dir(self, owner: str, repo: str) -> Path:
        path = self._repo_dir(owner, repo) / "changes"
        if path.is_symlink():
            raise DomainError("changes directory must not be a symlink")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def register_device(self, owner: str, repo: str, device: dict[str, Any]) -> None:
        path = self._repo_dir(owner, repo) / "devices.json"
        if path.is_symlink():
            raise DomainError("devices.json must not be a symlink")
        devices: list[dict[str, Any]] = []
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                devices = raw
        did = str(device.get("device_id") or "")
        devices = [d for d in devices if d.get("device_id") != did]
        devices.append(device)
        path.write_text(json.dumps(devices, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def append_operations(
        self, owner: str, repo: str, device_id: str, operations: list[dict[str, Any]]
    ) -> None:
        if not operations:
            return
        device_id = self._safe_component(device_id, "device id")
        d = self._changes_dir(owner, repo) / device_id
        if d.is_symlink():
            raise DomainError("device changes directory must not be a symlink")
        d.mkdir(parents=True, exist_ok=True)
        file = d / "ops.jsonl"
        if file.is_symlink():
            raise DomainError("ops.jsonl must not be a symlink")
        with file.open("a", encoding="utf-8") as f:
            for op in operations:
                f.write(json.dumps(op, ensure_ascii=False) + "\n")

    def list_remote_operations(
        self, owner: str, repo: str, *, exclude_device: str | None = None
    ) -> list[dict[str, Any]]:
        root = self._changes_dir(owner, repo)
        items: list[dict[str, Any]] = []
        if not root.is_dir():
            return items
        for device_dir in sorted(root.iterdir()):
            if not device_dir.is_dir() or device_dir.is_symlink():
                continue
            if exclude_device and device_dir.name == exclude_device:
                continue
            ops_file = device_dir / "ops.jsonl"
            if not ops_file.is_file():
                continue
            for line in ops_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    items.append(raw)
        items.sort(key=lambda o: str(o.get("timestamp") or ""))
        return items

    def write_tombstone(self, owner: str, repo: str, entity_id: str, payload: dict[str, Any]) -> None:
        entity_id = self._safe_component(entity_id, "entity id")
        d = self._repo_dir(owner, repo) / "tombstones"
        if d.is_symlink():
            raise DomainError("tombstones directory must not be a symlink")
        d.mkdir(parents=True, exist_ok=True)
        target = d / f"{entity_id}.json"
        if target.is_symlink():
            raise DomainError("tombstone path must not be a symlink")
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
