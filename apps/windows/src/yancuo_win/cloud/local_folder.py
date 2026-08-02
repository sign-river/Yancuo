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
from yancuo_win.infrastructure.archive import (
    ArchiveSecurityError,
    read_regular_file_limited,
)


class LocalFolderProvider(CloudProvider):
    name = "local_folder"
    MAX_METADATA_FILE_BYTES = 8 * 1024 * 1024
    MAX_RELEASES = 10_000
    MAX_RELEASE_ASSETS = 10_000
    MAX_ASSET_BYTES = 512 * 1024 * 1024
    MAX_DEVICES = 10_000
    MAX_OPERATION_FILE_BYTES = 64 * 1024 * 1024
    MAX_OPERATION_LINE_BYTES = 48 * 1024 * 1024
    MAX_REMOTE_OPERATIONS = 100_000
    MAX_REMOTE_DEVICES = 10_000
    MAX_LOCK_FILE_BYTES = 64 * 1024
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

    @classmethod
    def _read_json_file(cls, path: Path, label: str) -> Any:
        try:
            payload = read_regular_file_limited(path, max_bytes=cls.MAX_METADATA_FILE_BYTES)
            return json.loads(payload.decode("utf-8"))
        except ArchiveSecurityError as exc:
            if "过大" in str(exc) or "超限" in str(exc):
                raise DomainError(f"{label} exceeds size limit") from exc
            raise DomainError(f"{label} is not valid JSON") from exc
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise DomainError(f"{label} is not valid JSON") from exc

    @classmethod
    def _write_text_atomic(cls, path: Path, content: str, label: str) -> None:
        if path.is_symlink():
            raise DomainError(f"{label} must not be a symlink")
        if len(content.encode("utf-8")) > cls.MAX_METADATA_FILE_BYTES:
            raise DomainError(f"{label} exceeds size limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

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
        self._write_text_atomic(
            path / ".mistakebook" / "repository.json",
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            "repository.json",
        )
        return {"owner": owner, "name": name, "private": True, "path": str(path)}

    def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        path = self._repo_dir(owner, name)
        repo_json = path / ".mistakebook" / "repository.json"
        meta = {}
        if repo_json.is_file():
            raw = self._read_json_file(repo_json, "repository.json")
            if not isinstance(raw, dict):
                raise DomainError("repository.json must contain an object")
            meta = raw
        return {"owner": owner, "name": name, "private": True, "meta": meta, "path": str(path)}

    def read_sync_manifest(self, owner: str, repo: str) -> dict[str, Any] | None:
        path = self._repo_dir(owner, repo) / ".mistakebook" / "latest.json"
        if path.is_symlink():
            raise DomainError("latest.json must not be a symlink")
        if not path.is_file():
            return None
        raw = self._read_json_file(path, "latest.json")
        if not isinstance(raw, dict):
            raise DomainError("latest.json must contain an object")
        return raw

    def write_sync_manifest(self, owner: str, repo: str, manifest: dict[str, Any]) -> None:
        # 先写临时再替换，避免半写入
        path = self._repo_dir(owner, repo) / ".mistakebook" / "latest.json"
        if path.is_symlink():
            raise DomainError("latest.json must not be a symlink")
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        if len(encoded.encode("utf-8")) > self.MAX_METADATA_FILE_BYTES:
            raise DomainError("latest.json exceeds size limit")
        self._write_text_atomic(path, encoded, "latest.json")

    def list_releases(self, owner: str, repo: str) -> list[RemoteRelease]:
        releases_dir = self._repo_dir(owner, repo) / "releases"
        items: list[RemoteRelease] = []
        release_dirs = sorted(releases_dir.iterdir(), reverse=True)
        if len(release_dirs) > self.MAX_RELEASES:
            raise DomainError("release count exceeds limit")
        for d in release_dirs:
            if not d.is_dir() or d.is_symlink():
                continue
            meta_path = d / "release.json"
            meta = self._read_json_file(meta_path, "release.json") if meta_path.is_file() else {}
            if not isinstance(meta, dict):
                raise DomainError("release.json must contain an object")
            assets: list[dict[str, Any]] = []
            for asset_path in d.iterdir():
                if asset_path.name == "release.json" or (
                    asset_path.name.startswith(".up-") and asset_path.suffix == ".tmp"
                ):
                    continue
                if asset_path.is_symlink():
                    raise DomainError("release asset must not be a symlink")
                if asset_path.is_file():
                    assets.append(
                        {
                            "name": asset_path.name,
                            "path": str(asset_path),
                            "size": asset_path.stat().st_size,
                        }
                    )
            if len(assets) > self.MAX_RELEASE_ASSETS:
                raise DomainError("release asset count exceeds limit")
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
        try:
            self._write_text_atomic(
                d / "release.json",
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                "release.json",
            )
        except Exception:
            d.rmdir()
            raise
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
        if dest.is_symlink():
            raise DomainError("Release asset target must not be a symlink")
        fd, temporary_name = tempfile.mkstemp(prefix=".up-", suffix=".tmp", dir=d)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            self._copy_asset_bounded(Path(file_path), temporary)
            if dest.is_symlink():
                raise DomainError("Release asset target must not be a symlink")
            os.replace(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
        return {"name": asset_name, "path": str(dest), "size": dest.stat().st_size}

    def download_release_asset(
        self, owner: str, repo: str, *, tag: str, asset_name: str, dest: Path
    ) -> Path:
        tag = self._safe_component(tag, "release tag")
        asset_name = self._safe_component(asset_name, "asset name")
        src = self._repo_dir(owner, repo) / "releases" / tag / asset_name
        if src.is_symlink():
            raise DomainError("Release asset source must not be a symlink")
        if not src.is_file():
            raise DomainError(f"附件不存在：{asset_name}")
        dest = Path(dest)
        if dest.is_symlink():
            raise DomainError("Download target must not be a symlink")
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".down-", suffix=".tmp", dir=dest.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            self._copy_asset_bounded(src, temporary)
            if dest.is_symlink():
                raise DomainError("Download target must not be a symlink")
            os.replace(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
        return dest

    @classmethod
    def _copy_asset_bounded(cls, source: Path, destination: Path) -> None:
        source = Path(source)
        if source.is_symlink() or not source.is_file():
            raise DomainError("Release asset source must be a regular file")
        size = source.stat().st_size
        if size <= 0 or size > cls.MAX_ASSET_BYTES:
            raise DomainError("Release asset must be between 1 byte and 512 MiB")
        written = 0
        try:
            with source.open("rb") as incoming, destination.open("wb") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    written += len(chunk)
                    if written > cls.MAX_ASSET_BYTES:
                        raise DomainError("Release asset actual size exceeds 512 MiB")
                    outgoing.write(chunk)
                outgoing.flush()
                os.fsync(outgoing.fileno())
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if written != size:
            destination.unlink(missing_ok=True)
            raise DomainError("Release asset changed while being copied")

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

    @classmethod
    def _read_lock(cls, lock: Path) -> dict[str, Any] | None:
        try:
            payload = read_regular_file_limited(lock, max_bytes=cls.MAX_LOCK_FILE_BYTES)
            raw = json.loads(payload.decode("utf-8"))
        except (
            ArchiveSecurityError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
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
                        "expires_at": (now.timestamp() + self.lock_ttl_seconds),
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
            max_asset_bytes=self.MAX_ASSET_BYTES,
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
            raw = self._read_json_file(path, "devices.json")
            if isinstance(raw, list):
                devices = [value for value in raw if isinstance(value, dict)]
            else:
                raise DomainError("devices.json must contain an array")
        did = self._safe_component(str(device.get("device_id") or ""), "device id")
        devices = [d for d in devices if d.get("device_id") != did]
        if len(devices) >= self.MAX_DEVICES:
            raise DomainError("device count exceeds limit")
        devices.append(device)
        encoded = json.dumps(devices, ensure_ascii=False, indent=2) + "\n"
        if len(encoded.encode("utf-8")) > self.MAX_METADATA_FILE_BYTES:
            raise DomainError("devices.json exceeds size limit")
        self._write_text_atomic(path, encoded, "devices.json")

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
        file_existed = file.is_file()
        if len(operations) > self.MAX_REMOTE_OPERATIONS:
            raise DomainError("operation append count exceeds limit")
        current_size: int | None = None
        try:
            with file.open("ab", buffering=0) as stream:
                current_size = stream.tell()
                if current_size > self.MAX_OPERATION_FILE_BYTES:
                    raise DomainError("ops.jsonl exceeds size limit")
                appended_bytes = 0
                for operation in operations:
                    if not isinstance(operation, dict):
                        raise DomainError("operation append item must be an object")
                    encoded = (json.dumps(operation, ensure_ascii=False) + "\n").encode(
                        "utf-8"
                    )
                    if len(encoded) > self.MAX_OPERATION_LINE_BYTES:
                        raise DomainError("single operation exceeds line size limit")
                    appended_bytes += len(encoded)
                    if current_size + appended_bytes > self.MAX_OPERATION_FILE_BYTES:
                        raise DomainError("ops.jsonl append would exceed size limit")
                    view = memoryview(encoded)
                    while view:
                        written = stream.write(view)
                        if not written:
                            raise OSError("zero-byte operation append")
                        view = view[written:]
                os.fsync(stream.fileno())
        except Exception as exc:
            if current_size is None:
                if isinstance(exc, DomainError):
                    raise
                raise DomainError("ops.jsonl append failed") from exc
            try:
                if not file_existed:
                    file.unlink(missing_ok=True)
                else:
                    with file.open("r+b", buffering=0) as stream:
                        stream.truncate(current_size)
                        os.fsync(stream.fileno())
            except OSError as rollback_exc:
                raise DomainError("ops.jsonl append and rollback both failed") from rollback_exc
            if isinstance(exc, DomainError):
                raise
            raise DomainError("ops.jsonl append failed") from exc

    def list_remote_operations(
        self, owner: str, repo: str, *, exclude_device: str | None = None
    ) -> list[dict[str, Any]]:
        root = self._changes_dir(owner, repo)
        items: list[dict[str, Any]] = []
        processed_lines = 0
        if not root.is_dir():
            return items
        device_dirs = sorted(root.iterdir())
        if len(device_dirs) > self.MAX_REMOTE_DEVICES:
            raise DomainError("remote operation device directory count exceeds limit")
        for device_dir in device_dirs:
            if not device_dir.is_dir() or device_dir.is_symlink():
                continue
            if exclude_device and device_dir.name == exclude_device:
                continue
            ops_file = device_dir / "ops.jsonl"
            if ops_file.is_symlink():
                raise DomainError("ops.jsonl must not be a symlink")
            if not ops_file.is_file():
                continue
            if ops_file.stat().st_size > self.MAX_OPERATION_FILE_BYTES:
                raise DomainError("ops.jsonl exceeds size limit")
            try:
                with ops_file.open("rb") as stream:
                    while raw_line := stream.readline(self.MAX_OPERATION_LINE_BYTES + 1):
                        if len(raw_line) > self.MAX_OPERATION_LINE_BYTES:
                            raise DomainError("single remote operation line exceeds limit")
                        processed_lines += 1
                        if processed_lines > self.MAX_REMOTE_OPERATIONS:
                            raise DomainError("remote operation count exceeds limit")
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(raw, dict):
                            items.append(raw)
            except UnicodeDecodeError as exc:
                raise DomainError("ops.jsonl must be valid UTF-8") from exc
        items.sort(key=lambda o: str(o.get("timestamp") or ""))
        return items

    def write_tombstone(
        self, owner: str, repo: str, entity_id: str, payload: dict[str, Any]
    ) -> None:
        entity_id = self._safe_component(entity_id, "entity id")
        d = self._repo_dir(owner, repo) / "tombstones"
        if d.is_symlink():
            raise DomainError("tombstones directory must not be a symlink")
        d.mkdir(parents=True, exist_ok=True)
        target = d / f"{entity_id}.json"
        if target.is_symlink():
            raise DomainError("tombstone path must not be a symlink")
        self._write_text_atomic(
            target,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            "tombstone",
        )
