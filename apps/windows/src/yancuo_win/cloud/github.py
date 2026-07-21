"""GitHub Releases 云端适配器（开发期 PAT / Bearer；令牌仅系统凭据）。

业务层通过 CloudProvider 接入，不在业务代码散落 if provider == github。
流程：先创建 Release，再上传 asset（GitHub 官方顺序）；latest 指针为专用 tag。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from yancuo_win.cloud.base import (
    CloudCapabilities,
    CloudProvider,
    CloudUser,
    RemoteRelease,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import get_secret

logger = logging.getLogger("yancuo.cloud.github")

_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
_LATEST_TAG = "yancuo-latest"
_API_VERSION = "2022-11-28"


class GitHubProvider(CloudProvider):
    name = "github"

    def __init__(
        self,
        *,
        base_url: str = "https://api.github.com",
        credential_key: str = "yancuo_github_token",
        token: str | None = None,
    ) -> None:
        self.api_base = base_url.rstrip("/")
        self.credential_key = credential_key
        self._token = token
        self._caps = CloudCapabilities(
            private_repository=True,
            release_assets=True,
            atomic_file_update=False,
            oauth=False,
            large_file_upload=True,
            delete_release=True,
            max_asset_bytes=2 * 1024 * 1024 * 1024,  # GitHub 单文件约 2GiB
            assets_first=False,
        )
        # create_release 后暂存 upload_url，供紧随其后的 upload_release_asset 使用
        self._upload_urls: dict[str, str] = {}
        self._release_ids: dict[str, int] = {}

    def _resolve_token(self) -> str:
        if self._token:
            return self._token.strip()
        env = os.environ.get("YANCUO_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if env:
            return env.strip()
        secret = get_secret(self.credential_key)
        if not secret:
            raise DomainError(
                "未配置 GitHub 令牌：请在设置中保存 PAT，或设置环境变量 YANCUO_GITHUB_TOKEN"
            )
        return secret

    def _check_owner_repo(self, owner: str, repo: str) -> None:
        if not _SAFE.fullmatch(owner) or not _SAFE.fullmatch(repo):
            raise DomainError("GitHub owner/repo 格式不正确")

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._resolve_token()}",
            "User-Agent": "Yancuo-Windows",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        expect_json: bool = True,
        raw_body: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        data = raw_body
        headers = self._headers(content_type=content_type)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=300) as resp:
                raw = resp.read()
                logger.info("github %s %s -> %s", method, url.split("?")[0], resp.status)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code == 404:
                raise DomainError(f"GitHub 资源不存在（404）：{detail}") from exc
            raise DomainError(f"GitHub HTTP {exc.code}: {detail}") from exc
        except OSError as exc:
            raise DomainError(f"GitHub 请求失败：{exc}") from exc
        if not expect_json or not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DomainError("GitHub 返回了无效 JSON") from exc

    def _repo_url(self, owner: str, repo: str, suffix: str = "") -> str:
        return f"{self.api_base}/repos/{owner}/{repo}{suffix}"

    def _to_remote(self, item: dict[str, Any]) -> RemoteRelease:
        assets = item.get("assets") or []
        if not isinstance(assets, list):
            assets = []
        normalized = []
        for a in assets:
            if not isinstance(a, dict):
                continue
            entry = dict(a)
            if a.get("name"):
                entry["name"] = a["name"]
            if a.get("browser_download_url"):
                entry["download_url"] = a["browser_download_url"]
            normalized.append(entry)
        return RemoteRelease(
            tag=str(item.get("tag_name") or ""),
            name=str(item.get("name") or item.get("tag_name") or ""),
            assets=normalized,
            raw=item,
        )

    def authenticate(self) -> None:
        data = self._request_json("GET", f"{self.api_base}/user")
        if not isinstance(data, dict) or not data.get("login"):
            raise DomainError("GitHub 令牌无效或无法读取当前用户")

    def get_current_user(self) -> CloudUser:
        data = self._request_json("GET", f"{self.api_base}/user")
        login = str(data.get("login") or "")
        return CloudUser(
            login=login,
            display_name=str(data.get("name") or login),
            raw=data if isinstance(data, dict) else {},
        )

    def list_repositories(self) -> list[dict[str, Any]]:
        # 仅返回当前用户仓库前几页，供调试；正式备份仍靠配置 owner/repo
        data = self._request_json(
            "GET", f"{self.api_base}/user/repos?per_page=30&sort=updated"
        )
        if not isinstance(data, list):
            return []
        rows = []
        for item in data:
            if not isinstance(item, dict):
                continue
            full = str(item.get("full_name") or "")
            if "/" not in full:
                continue
            owner, name = full.split("/", 1)
            rows.append(
                {
                    "owner": owner,
                    "name": name,
                    "private": bool(item.get("private")),
                    "html_url": item.get("html_url"),
                }
            )
        return rows

    def create_private_repository(self, name: str) -> dict[str, Any]:
        if not _SAFE.fullmatch(name):
            raise DomainError("仓库名格式不正确")
        data = self._request_json(
            "POST",
            f"{self.api_base}/user/repos",
            body={
                "name": name,
                "private": True,
                "description": "Yancuo (研错库) cloud backup — not real-time sync",
                "auto_init": True,
            },
        )
        return {
            "owner": (data.get("owner") or {}).get("login"),
            "name": data.get("name"),
            "private": data.get("private"),
            "html_url": data.get("html_url"),
        }

    def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        self._check_owner_repo(owner, name)
        data = self._request_json("GET", self._repo_url(owner, name))
        return {
            "owner": owner,
            "name": name,
            "private": bool(data.get("private")),
            "html_url": data.get("html_url"),
            "raw": data,
        }

    def list_releases(self, owner: str, repo: str) -> list[RemoteRelease]:
        self._check_owner_repo(owner, repo)
        data = self._request_json(
            "GET", self._repo_url(owner, repo, "/releases?per_page=100")
        )
        if not isinstance(data, list):
            raise DomainError("无法列出 GitHub Releases")
        return [self._to_remote(x) for x in data if isinstance(x, dict) and x.get("tag_name")]

    def create_release(
        self, owner: str, repo: str, *, tag: str, name: str, body: str = ""
    ) -> RemoteRelease:
        self._check_owner_repo(owner, repo)
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", tag):
            raise DomainError("Release tag 格式不正确")
        # 若已存在则复用
        try:
            existing = self._request_json(
                "GET",
                self._repo_url(owner, repo, f"/releases/tags/{quote(tag)}"),
            )
            if isinstance(existing, dict) and existing.get("id"):
                self._upload_urls[tag] = str(existing.get("upload_url") or "")
                self._release_ids[tag] = int(existing["id"])
                # 更新说明
                self._request_json(
                    "PATCH",
                    self._repo_url(owner, repo, f"/releases/{existing['id']}"),
                    body={"name": name, "body": body},
                )
                return self._to_remote(existing)
        except DomainError as exc:
            if "404" not in str(exc):
                raise

        data = self._request_json(
            "POST",
            self._repo_url(owner, repo, "/releases"),
            body={
                "tag_name": tag,
                "name": name,
                "body": body,
                "draft": False,
                "prerelease": False,
            },
        )
        if not isinstance(data, dict) or not data.get("upload_url"):
            raise DomainError("创建 GitHub Release 失败：缺少 upload_url")
        self._upload_urls[tag] = str(data["upload_url"])
        self._release_ids[tag] = int(data["id"])
        return self._to_remote(data)

    def upload_release_asset(
        self, owner: str, repo: str, *, tag: str, file_path: Path, asset_name: str
    ) -> dict[str, Any]:
        self._check_owner_repo(owner, repo)
        path = Path(file_path)
        if not path.is_file():
            raise DomainError(f"待上传文件不存在：{path}")

        upload_url = self._upload_urls.get(tag)
        release_id = self._release_ids.get(tag)
        if not upload_url or release_id is None:
            # 从远端补齐
            existing = self._request_json(
                "GET",
                self._repo_url(owner, repo, f"/releases/tags/{quote(tag)}"),
            )
            upload_url = str(existing.get("upload_url") or "")
            release_id = int(existing["id"])
            self._upload_urls[tag] = upload_url
            self._release_ids[tag] = release_id

        # 同名资产先删再传，避免冲突
        release = self._request_json(
            "GET", self._repo_url(owner, repo, f"/releases/{release_id}")
        )
        for asset in release.get("assets") or []:
            if isinstance(asset, dict) and asset.get("name") == asset_name:
                self._request_json(
                    "DELETE",
                    self._repo_url(owner, repo, f"/releases/assets/{asset['id']}"),
                    expect_json=False,
                )

        base = upload_url.split("{", 1)[0]
        url = f"{base}?name={quote(asset_name)}"
        data = path.read_bytes()
        payload = self._request_json(
            "POST",
            url,
            raw_body=data,
            content_type="application/octet-stream",
        )
        if not isinstance(payload, dict):
            raise DomainError("GitHub 上传附件返回异常")
        return {
            "id": payload.get("id"),
            "name": payload.get("name") or asset_name,
            "size": payload.get("size"),
            "download_url": payload.get("browser_download_url"),
        }

    def download_release_asset(
        self, owner: str, repo: str, *, tag: str, asset_name: str, dest: Path
    ) -> Path:
        release = self._request_json(
            "GET",
            self._repo_url(owner, repo, f"/releases/tags/{quote(tag)}"),
        )
        assets = release.get("assets") or []
        asset = next(
            (a for a in assets if isinstance(a, dict) and a.get("name") == asset_name),
            None,
        )
        if not asset:
            raise DomainError(f"未找到附件：{asset_name}")
        # API 下载需 Accept: application/octet-stream
        url = str(asset.get("url") or "")
        if not url:
            raise DomainError("附件缺少 API url")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = self._headers()
        headers["Accept"] = "application/octet-stream"
        req = Request(url, headers=headers, method="GET")
        try:
            with urlopen(req, timeout=600) as resp, dest.open("wb") as out:
                shutil.copyfileobj(resp, out)
        except Exception as exc:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            raise DomainError(f"下载附件失败：{exc}") from exc
        return dest

    def delete_release(self, owner: str, repo: str, *, tag: str) -> None:
        self._check_owner_repo(owner, repo)
        try:
            release = self._request_json(
                "GET",
                self._repo_url(owner, repo, f"/releases/tags/{quote(tag)}"),
            )
        except DomainError as exc:
            if "404" in str(exc):
                return
            raise
        rid = release.get("id")
        if rid is None:
            return
        self._request_json(
            "DELETE",
            self._repo_url(owner, repo, f"/releases/{rid}"),
            expect_json=False,
        )

    def read_sync_manifest(self, owner: str, repo: str) -> dict[str, Any] | None:
        try:
            release = self._request_json(
                "GET",
                self._repo_url(owner, repo, f"/releases/tags/{quote(_LATEST_TAG)}"),
            )
        except DomainError as exc:
            if "404" in str(exc):
                return None
            raise
        body = release.get("body") or ""
        if isinstance(body, str) and body.strip().startswith("{"):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        return {"tag": release.get("tag_name"), "raw_body": body}

    def write_sync_manifest(self, owner: str, repo: str, manifest: dict[str, Any]) -> None:
        body = json.dumps(manifest, ensure_ascii=False)
        self.create_release(
            owner,
            repo,
            tag=_LATEST_TAG,
            name="研错库最新备份指针",
            body=body,
        )

    def acquire_lock(self, owner: str, repo: str, device_id: str) -> bool:
        return True

    def release_lock(self, owner: str, repo: str, device_id: str) -> None:
        return None

    def test_connection(self) -> dict[str, Any]:
        user = self.get_current_user()
        return {
            "ok": True,
            "provider": self.name,
            "login": user.login,
            "capabilities": self.get_capabilities().to_dict(),
            "note": "使用 GitHub Releases；令牌未写入日志。请配置私有仓库 owner/repo。",
        }

    def get_capabilities(self) -> CloudCapabilities:
        return self._caps
