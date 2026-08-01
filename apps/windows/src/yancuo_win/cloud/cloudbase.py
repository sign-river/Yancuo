"""CloudBase complete-snapshot adapter through a deployed gateway function.

CloudBase client SDK credentials must never be embedded in a desktop client.
The gateway owns Cloud Storage and PostgreSQL access; this adapter talks only
to its documented HTTPS contract and keeps the gateway token in keyring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from yancuo_win.cloud.base import CloudCapabilities, CloudProvider, CloudUser, RemoteRelease
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import get_secret


class CloudBaseGatewayProvider(CloudProvider):
    name = "cloudbase"

    def __init__(self, *, environment_id: str, gateway_url: str, credential_key: str) -> None:
        self.environment_id = environment_id.strip()
        self.gateway_url = gateway_url.rstrip("/")
        self.credential_key = credential_key

    def _token(self) -> str:
        token = get_secret(self.credential_key)
        if not token:
            raise DomainError("请先在设置中保存 CloudBase 网关令牌")
        return token

    def _validate_configuration(self) -> None:
        if not self.environment_id:
            raise DomainError("请填写 CloudBase 环境 ID")
        if not self.gateway_url.startswith(("https://", "http://")):
            raise DomainError("请填写 CloudBase 网关 HTTPS 地址")

    def _action(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._validate_configuration()
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.gateway_url}/actions/{quote(action, safe='/')}",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token()}",
                "Content-Type": "application/json",
                "X-CloudBase-Environment-ID": self.environment_id,
            },
        )
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 - configured gateway
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise DomainError(f"CloudBase 网关请求失败（HTTP {exc.code}）：{detail}") from exc
        except URLError as exc:
            raise DomainError(f"无法连接 CloudBase 网关：{exc.reason}") from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DomainError("CloudBase 网关返回了无效 JSON") from exc
        if not isinstance(result, dict):
            raise DomainError("CloudBase 网关返回格式无效")
        if result.get("ok") is False:
            raise DomainError(str(result.get("error") or "CloudBase 网关操作失败"))
        data = result.get("data", result)
        if not isinstance(data, dict):
            raise DomainError("CloudBase 网关 data 格式无效")
        return data

    @staticmethod
    def _repo(owner: str, repo: str) -> dict[str, str]:
        return {"owner": owner, "repository": repo}

    def authenticate(self) -> None:
        self._action("health")

    def get_current_user(self) -> CloudUser:
        data = self._action("users/me")
        return CloudUser(str(data.get("login") or "cloudbase"), str(data.get("display_name") or ""), data)

    def list_repositories(self) -> list[dict[str, Any]]:
        data = self._action("repositories/list")
        rows = data.get("repositories", [])
        return rows if isinstance(rows, list) else []

    def create_private_repository(self, name: str) -> dict[str, Any]:
        return self._action("repositories/create", {"name": name, "private": True})

    def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        return self._action("repositories/get", self._repo(owner, name))

    def read_sync_manifest(self, owner: str, repo: str) -> dict[str, Any] | None:
        data = self._action("manifest/read", self._repo(owner, repo))
        manifest = data.get("manifest")
        return manifest if isinstance(manifest, dict) else None

    def write_sync_manifest(self, owner: str, repo: str, manifest: dict[str, Any]) -> None:
        self._action("manifest/write", {**self._repo(owner, repo), "manifest": manifest})

    def list_releases(self, owner: str, repo: str) -> list[RemoteRelease]:
        data = self._action("releases/list", self._repo(owner, repo))
        rows = data.get("releases", [])
        if not isinstance(rows, list):
            return []
        return [
            RemoteRelease(
                tag=str(row.get("tag") or ""),
                name=str(row.get("name") or row.get("tag") or ""),
                assets=row.get("assets") if isinstance(row.get("assets"), list) else [],
                raw=row if isinstance(row, dict) else {},
            )
            for row in rows
            if isinstance(row, dict)
        ]

    def create_release(self, owner: str, repo: str, *, tag: str, name: str, body: str = "") -> RemoteRelease:
        data = self._action("releases/create", {**self._repo(owner, repo), "tag": tag, "name": name, "body": body})
        return RemoteRelease(tag=tag, name=name, assets=[], raw=data)

    def upload_release_asset(self, owner: str, repo: str, *, tag: str, file_path: Path, asset_name: str) -> dict[str, Any]:
        data = self._action("assets/upload-url", {**self._repo(owner, repo), "tag": tag, "asset_name": asset_name, "size": file_path.stat().st_size})
        url = str(data.get("url") or "")
        if not url.startswith(("https://", "http://")):
            raise DomainError("CloudBase 网关未返回有效上传地址")
        headers = data.get("headers") if isinstance(data.get("headers"), dict) else {}
        request = Request(url, data=file_path.read_bytes(), method="PUT", headers={str(k): str(v) for k, v in headers.items()})
        try:
            with urlopen(request, timeout=300):  # noqa: S310 - signed upload URL from gateway
                pass
        except (HTTPError, URLError) as exc:
            raise DomainError(f"CloudBase 存储上传失败：{exc}") from exc
        return self._action("assets/commit", {**self._repo(owner, repo), "tag": tag, "asset_name": asset_name, "upload_id": data.get("upload_id")})

    def download_release_asset(self, owner: str, repo: str, *, tag: str, asset_name: str, dest: Path) -> Path:
        data = self._action("assets/download-url", {**self._repo(owner, repo), "tag": tag, "asset_name": asset_name})
        url = str(data.get("url") or "")
        if not url.startswith(("https://", "http://")):
            raise DomainError("CloudBase 网关未返回有效下载地址")
        try:
            with urlopen(url, timeout=300) as response:  # noqa: S310 - signed download URL from gateway
                content = response.read()
        except (HTTPError, URLError) as exc:
            raise DomainError(f"CloudBase 存储下载失败：{exc}") from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return dest

    def delete_release(self, owner: str, repo: str, *, tag: str) -> None:
        self._action("releases/delete", {**self._repo(owner, repo), "tag": tag})

    def acquire_lock(self, owner: str, repo: str, device_id: str) -> bool:
        return bool(self._action("locks/acquire", {**self._repo(owner, repo), "device_id": device_id}).get("acquired"))

    def release_lock(self, owner: str, repo: str, device_id: str) -> None:
        self._action("locks/release", {**self._repo(owner, repo), "device_id": device_id})

    def test_connection(self) -> dict[str, Any]:
        self.authenticate()
        return {"ok": True, "provider": self.name, "environment_id": self.environment_id}

    def get_capabilities(self) -> CloudCapabilities:
        return CloudCapabilities(private_repository=True, release_assets=True, atomic_file_update=True, large_file_upload=True, delete_release=True)
