"""GitLink 云端适配器（令牌仅来自系统凭据 / 环境变量）。"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from yancuo_win.cloud.base import (
    CloudCapabilities,
    CloudProvider,
    CloudUser,
    RemoteRelease,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import get_secret

logger = logging.getLogger("yancuo.cloud.gitlink")


class GitLinkProvider(CloudProvider):
    name = "gitlink"

    def __init__(
        self,
        *,
        base_url: str = "https://www.gitlink.org.cn",
        credential_key: str = "yancuo_gitlink_token",
        token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.credential_key = credential_key
        self._token = token
        self._caps = CloudCapabilities(
            private_repository=True,
            release_assets=False,  # 附件上传接口待实机确认后打开
            atomic_file_update=False,
            oauth=False,
            large_file_upload=False,
            delete_release=False,
            max_asset_bytes=None,
        )

    def _resolve_token(self) -> str:
        if self._token:
            return self._token
        env = __import__("os").environ.get("YANCUO_GITLINK_TOKEN") or __import__("os").environ.get(
            "GITLINK_TOKEN"
        )
        if env:
            return env.strip()
        secret = get_secret(self.credential_key)
        if not secret:
            raise DomainError(
                "未配置 GitLink 令牌：请在设置中保存，或设置环境变量 YANCUO_GITLINK_TOKEN"
            )
        return secret

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        form: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        content_type: str | None = None,
        auth: bool = True,
    ) -> tuple[int, Any]:
        token = self._resolve_token() if auth else ""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {"User-Agent": "Yancuo-Windows"}
        if auth and token:
            headers["PRIVATE-TOKEN"] = token
            sep = "&" if "?" in url else "?"
            if "private_token=" not in url:
                url = f"{url}{sep}private_token={urllib.parse.quote(token)}"
        data = raw_body
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", "replace")
                # 绝不记录 token
                logger.info("gitlink %s %s -> %s", method, path.split("?")[0], resp.status)
                try:
                    return resp.status, json.loads(text)
                except json.JSONDecodeError:
                    return resp.status, text
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise DomainError(f"GitLink HTTP {exc.code}: {detail}") from exc
        except DomainError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DomainError(f"GitLink 请求失败：{exc}") from exc

    def authenticate(self) -> None:
        self._resolve_token()
        # 用公开仓库 Release 列表验证连通
        status, data = self._request("/api/Gitlink/forgeplus/releases.json")
        if status != 200 or not isinstance(data, dict) or "releases" not in data:
            raise DomainError("GitLink 连通性验证失败（releases.json）")
        self._caps.release_assets = False  # 列表可读；上传另测
        # 标记列表能力通过
        self._caps.private_repository = True

    def get_current_user(self) -> CloudUser:
        # 官方 user 接口在实测中不稳定；返回占位，具体 owner 由配置指定
        return CloudUser(login="", display_name="GitLink 用户（请在设置填写仓库 owner）")

    def list_repositories(self) -> list[dict[str, Any]]:
        # 全站 projects.json 不按用户过滤；返回空并提示用配置 owner/name
        return []

    def create_private_repository(self, name: str) -> dict[str, Any]:
        raise DomainError(
            "GitLink 创建私有库接口需 user_id 等参数，当前请在网页创建后于设置中填写 owner/name"
        )

    def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        status, data = self._request(f"/api/{owner}/{name}/releases.json")
        if status != 200:
            raise DomainError(f"无法访问仓库 {owner}/{name}")
        return {"owner": owner, "name": name, "releases_api": True, "raw_status": data.get("status")}

    def read_sync_manifest(self, owner: str, repo: str) -> dict[str, Any] | None:
        # 优先从最新 release 的 preview/latest 附件语义退化：读 releases 中带 latest 标记的 tag
        releases = self.list_releases(owner, repo)
        for rel in releases:
            if rel.tag.endswith("-latest-pointer"):
                return rel.raw.get("manifest") or rel.raw
        # 或读取名为 latest 的 release body
        for rel in releases:
            if rel.tag == "latest-pointer" or rel.name.startswith("latest"):
                body = rel.raw.get("body") or ""
                try:
                    return json.loads(body)
                except json.JSONDecodeError:
                    return rel.raw
        return None

    def write_sync_manifest(self, owner: str, repo: str, manifest: dict[str, Any]) -> None:
        # 用特殊 release 保存指针（先完整上传附件后再调用）
        tag = "latest-pointer"
        # 删除旧指针（若支持）；当前能力不足则覆盖创建失败时忽略
        try:
            self.delete_release(owner, repo, tag=tag)
        except DomainError:
            pass
        body = json.dumps(manifest, ensure_ascii=False)
        self.create_release(
            owner,
            repo,
            tag=tag,
            name="研错库最新备份指针",
            body=body,
        )

    def list_releases(self, owner: str, repo: str) -> list[RemoteRelease]:
        status, data = self._request(f"/api/{owner}/{repo}/releases.json")
        if status != 200 or not isinstance(data, dict):
            raise DomainError("无法列出 Release")
        items: list[RemoteRelease] = []
        for rel in data.get("releases") or []:
            tag = str(rel.get("tag_name") or rel.get("tag") or rel.get("name") or "")
            if not tag:
                continue
            assets = rel.get("assets") or rel.get("attachments") or []
            if not isinstance(assets, list):
                assets = []
            items.append(
                RemoteRelease(
                    tag=tag,
                    name=str(rel.get("name") or tag),
                    assets=list(assets),
                    raw=rel,
                )
            )
        return items

    def create_release(
        self, owner: str, repo: str, *, tag: str, name: str, body: str = ""
    ) -> RemoteRelease:
        # 尝试几种常见表单字段；失败则明确报错
        attempts = [
            {"tag_name": tag, "name": name, "body": body, "target_commitish": "master"},
            {"tag": tag, "name": name, "body": body},
            {"tag_name": tag, "title": name, "description": body},
        ]
        last_err = "unknown"
        for form in attempts:
            try:
                status, data = self._request(
                    f"/api/{owner}/{repo}/releases.json", method="POST", form=form
                )
                if status in (200, 201) and isinstance(data, dict):
                    return RemoteRelease(tag=tag, name=name, assets=[], raw=data if isinstance(data, dict) else {})
            except DomainError as exc:
                last_err = str(exc)
                continue
        raise DomainError(
            f"GitLink 创建 Release 失败（接口可能需网页/CLI）。详情：{last_err}。"
            "完整备份请改用「本地文件夹」提供商，或待附件 API 验证后重试。"
        )

    def upload_release_asset(
        self, owner: str, repo: str, *, tag: str, file_path: Path, asset_name: str
    ) -> dict[str, Any]:
        raise DomainError(
            "GitLink Release 附件上传 API 尚未在兼容性验证中确认。"
            "请使用本地文件夹提供商完成备份，或等待后续适配。"
        )

    def download_release_asset(
        self, owner: str, repo: str, *, tag: str, asset_name: str, dest: Path
    ) -> Path:
        raise DomainError("GitLink 附件下载 API 尚未确认；请使用本地文件夹提供商或手动下载。")

    def delete_release(self, owner: str, repo: str, *, tag: str) -> None:
        raise DomainError("GitLink 删除 Release API 尚未确认")

    def acquire_lock(self, owner: str, repo: str, device_id: str) -> bool:
        # 单写入设备：写入 latest 清单中的 primary_device 字段由上层处理
        return True

    def release_lock(self, owner: str, repo: str, device_id: str) -> None:
        return None

    def test_connection(self) -> dict[str, Any]:
        self.authenticate()
        return {
            "ok": True,
            "provider": self.name,
            "token_configured": True,
            "capabilities": self.get_capabilities().to_dict(),
            "note": "Release 列表可读；附件上传待确认。令牌未写入日志。",
        }

    def get_capabilities(self) -> CloudCapabilities:
        return self._caps
