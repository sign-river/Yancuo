"""统一云端提供商接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CloudCapabilities:
    private_repository: bool = False
    release_assets: bool = False
    atomic_file_update: bool = False
    oauth: bool = False
    large_file_upload: bool = False
    delete_release: bool = False
    max_asset_bytes: int | None = None
    # True：先上传附件再创建/更新 Release（GitLink 成熟模式）
    assets_first: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "private_repository": self.private_repository,
            "release_assets": self.release_assets,
            "atomic_file_update": self.atomic_file_update,
            "oauth": self.oauth,
            "large_file_upload": self.large_file_upload,
            "delete_release": self.delete_release,
            "max_asset_bytes": self.max_asset_bytes,
            "assets_first": self.assets_first,
        }


@dataclass
class RemoteRelease:
    tag: str
    name: str
    assets: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CloudUser:
    login: str
    display_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class CloudProvider(ABC):
    name: str

    @abstractmethod
    def authenticate(self) -> None: ...

    @abstractmethod
    def get_current_user(self) -> CloudUser: ...

    @abstractmethod
    def list_repositories(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def create_private_repository(self, name: str) -> dict[str, Any]: ...

    @abstractmethod
    def get_repository(self, owner: str, name: str) -> dict[str, Any]: ...

    @abstractmethod
    def read_sync_manifest(self, owner: str, repo: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def write_sync_manifest(self, owner: str, repo: str, manifest: dict[str, Any]) -> None: ...

    @abstractmethod
    def list_releases(self, owner: str, repo: str) -> list[RemoteRelease]: ...

    @abstractmethod
    def create_release(
        self, owner: str, repo: str, *, tag: str, name: str, body: str = ""
    ) -> RemoteRelease: ...

    @abstractmethod
    def upload_release_asset(
        self, owner: str, repo: str, *, tag: str, file_path: Path, asset_name: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    def download_release_asset(
        self, owner: str, repo: str, *, tag: str, asset_name: str, dest: Path
    ) -> Path: ...

    @abstractmethod
    def delete_release(self, owner: str, repo: str, *, tag: str) -> None: ...

    @abstractmethod
    def acquire_lock(self, owner: str, repo: str, device_id: str) -> bool: ...

    @abstractmethod
    def release_lock(self, owner: str, repo: str, device_id: str) -> None: ...

    @abstractmethod
    def test_connection(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_capabilities(self) -> CloudCapabilities: ...
