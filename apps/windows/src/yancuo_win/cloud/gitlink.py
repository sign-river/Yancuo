"""GitLink 云端适配器：按成熟模式使用 Release + Attachment API。

勿按 GitLab/Gitee 兼容性探测 user/projects。
令牌仅来自系统凭据 / 环境变量；认证头使用 Authorization: Bearer。
发布顺序：先 POST /api/attachments.json 取得 attachment_id，再 create/update Release。
更新 Release 必须使用 version_id。
"""

from __future__ import annotations

import http.client
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from yancuo_win.cloud.base import (
    CloudCapabilities,
    CloudProvider,
    CloudUser,
    RemoteRelease,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import get_secret

logger = logging.getLogger("yancuo.cloud.gitlink")

_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
_LATEST_TAG = "yancuo-latest"


class GitLinkProvider(CloudProvider):
    name = "gitlink"

    def __init__(
        self,
        *,
        base_url: str = "https://www.gitlink.org.cn",
        credential_key: str = "yancuo_gitlink_token",
        token: str | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise DomainError("GitLink base_url 必须是 https 源站")
        self.base_url = base_url.rstrip("/")
        self.host = parsed.hostname
        self.port = parsed.port
        self.credential_key = credential_key
        self._token = token
        # tag -> 已上传、待挂到 Release 的 attachment_id 列表
        self._staged: dict[str, list[str]] = {}
        self._caps = CloudCapabilities(
            private_repository=True,
            release_assets=True,
            atomic_file_update=False,
            oauth=False,
            large_file_upload=True,
            delete_release=False,
            max_asset_bytes=None,
            assets_first=True,
        )

    def _resolve_token(self) -> str:
        if self._token:
            return self._token.strip()
        env = os.environ.get("YANCUO_GITLINK_TOKEN") or os.environ.get("GITLINK_TOKEN")
        if env:
            return env.strip()
        secret = get_secret(self.credential_key)
        if not secret:
            raise DomainError(
                "未配置 GitLink 令牌：请在设置中保存，或设置环境变量 YANCUO_GITLINK_TOKEN"
            )
        return secret

    def _check_owner_repo(self, owner: str, repo: str) -> None:
        if not _SAFE.fullmatch(owner) or not _SAFE.fullmatch(repo):
            raise DomainError("GitLink owner/repo 格式不正确（仅字母数字._-）")

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        allow_empty: bool = False,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Yancuo-Windows",
        }
        if auth:
            headers["Authorization"] = f"Bearer {self._resolve_token()}"
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=120)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise DomainError("GitLink 响应过大")
            if resp.status < 200 or resp.status >= 300:
                msg = raw.decode("utf-8", "replace").strip()[:300]
                raise DomainError(f"GitLink HTTP {resp.status}: {msg}")
            if allow_empty and not raw.strip():
                return {}
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DomainError("GitLink 返回了无法识别的 JSON") from exc
            if not isinstance(value, dict):
                raise DomainError("GitLink 返回格式不正确")
            api_status = value.get("status")
            try:
                code = int(api_status) if api_status is not None else 0
            except (TypeError, ValueError):
                code = 0
            if code >= 400:
                raise DomainError(
                    f"GitLink API 错误（{code}）：{value.get('message') or value.get('error') or '请求失败'}"
                )
            logger.info("gitlink %s %s -> %s", method, path.split("?")[0], resp.status)
            return value
        except DomainError:
            raise
        except OSError as exc:
            raise DomainError(f"GitLink 连接失败：{exc}") from exc
        finally:
            conn.close()

    def upload_attachment(self, file_path: Path, *, filename: str | None = None) -> str:
        """POST /api/attachments.json，返回 attachment_id。"""
        token = self._resolve_token()
        path = Path(file_path).resolve()
        if not path.is_file():
            raise DomainError(f"待上传文件不存在：{path}")
        name = _safe_filename(filename or path.name)
        boundary = "----Yancuo" + secrets.token_hex(16)
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        prefix = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
        total = len(prefix) + path.stat().st_size + len(suffix)
        conn = http.client.HTTPSConnection(self.host, self.port, timeout=600)
        try:
            conn.putrequest("POST", "/api/attachments.json")
            conn.putheader("Authorization", f"Bearer {token}")
            conn.putheader("Accept", "application/json")
            conn.putheader("User-Agent", "Yancuo-Windows")
            conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            conn.putheader("Content-Length", str(total))
            conn.endheaders()
            conn.send(prefix)
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    conn.send(chunk)
            conn.send(suffix)
            resp = conn.getresponse()
            raw = resp.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise DomainError("GitLink 上传响应过大")
            if resp.status < 200 or resp.status >= 300:
                msg = raw.decode("utf-8", "replace").strip()[:300]
                raise DomainError(f"上传附件失败（HTTP {resp.status}）：{msg}")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise DomainError("上传附件后收到无效 JSON") from exc
            attachment_id = _find_attachment_id(value)
            if not attachment_id:
                raise DomainError("上传附件后未取得 attachment_id")
            logger.info("gitlink uploaded attachment id=%s name=%s", attachment_id, name)
            return attachment_id
        except DomainError:
            raise
        except OSError as exc:
            raise DomainError(f"上传附件失败：{exc}") from exc
        finally:
            conn.close()

    def delete_attachment(self, attachment_id: str) -> None:
        if not _SAFE.fullmatch(attachment_id):
            raise DomainError("附件 ID 格式不正确")
        try:
            self._json_request(
                "DELETE", f"/api/attachments/{attachment_id}.json", allow_empty=True
            )
        except DomainError as exc:
            logger.warning("回收附件失败 id=%s: %s", attachment_id, exc)

    def _release_payload(
        self, tag: str, name: str, body: str, attachment_ids: list[str]
    ) -> dict[str, Any]:
        return {
            "tag_name": tag,
            "name": name,
            "body": body,
            "target_commitish": "master",
            "draft": False,
            "prerelease": False,
            "attachment_ids": attachment_ids,
        }

    def _parse_releases(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("releases"), list):
            return [x for x in data["releases"] if isinstance(x, dict)]
        releases = payload.get("releases")
        if isinstance(releases, list):
            return [x for x in releases if isinstance(x, dict)]
        return []

    def _find_release_raw(self, owner: str, repo: str, tag: str) -> dict[str, Any] | None:
        payload = self._json_request(
            "GET", f"/api/{owner}/{repo}/releases.json?page=1&limit=100"
        )
        for item in self._parse_releases(payload):
            if str(item.get("tag_name") or "") == tag:
                return item
        return None

    def _version_id(self, item: dict[str, Any]) -> str | None:
        value = item.get("version_id") or item.get("id") or item.get("version_gid")
        return str(value) if value is not None else None

    def _to_remote(self, item: dict[str, Any]) -> RemoteRelease:
        tag = str(item.get("tag_name") or item.get("tag") or "")
        assets = item.get("attachments") or item.get("assets") or []
        if not isinstance(assets, list):
            assets = []
        normalized = []
        for a in assets:
            if not isinstance(a, dict):
                continue
            title = a.get("title") or a.get("name")
            url = a.get("url")
            entry = dict(a)
            if title:
                entry["name"] = title
            if isinstance(url, str) and url:
                entry["download_url"] = urljoin(self.base_url + "/", url)
            normalized.append(entry)
        return RemoteRelease(
            tag=tag,
            name=str(item.get("name") or tag),
            assets=normalized,
            raw=item,
        )

    def authenticate(self) -> None:
        self._resolve_token()
        # 不探测 user/projects；有令牌即视为可写路径已配置

    def get_current_user(self) -> CloudUser:
        return CloudUser(
            login="",
            display_name="GitLink（请在设置填写 owner/repo）",
        )

    def list_repositories(self) -> list[dict[str, Any]]:
        # 故意不调用全站 projects.json
        return []

    def create_private_repository(self, name: str) -> dict[str, Any]:
        raise DomainError(
            "请在 GitLink 网页或 gitlink-cli 创建私有库后，在设置中填写 owner/name"
        )

    def get_repository(self, owner: str, name: str) -> dict[str, Any]:
        self._check_owner_repo(owner, name)
        # 列表接口可验证仓库可访问（需令牌）
        payload = self._json_request(
            "GET", f"/api/{owner}/{name}/releases.json?page=1&limit=1"
        )
        return {
            "owner": owner,
            "name": name,
            "releases_api": True,
            "count_hint": len(self._parse_releases(payload)),
        }

    def list_releases(self, owner: str, repo: str) -> list[RemoteRelease]:
        self._check_owner_repo(owner, repo)
        # 发布侧列表（Bearer）
        try:
            payload = self._json_request(
                "GET", f"/api/{owner}/{repo}/releases.json?page=1&limit=100"
            )
            items = self._parse_releases(payload)
            if items:
                return [self._to_remote(x) for x in items if x.get("tag_name")]
        except DomainError:
            pass
        # 公开只读回退
        url = f"{self.base_url}/api/{owner}/{repo}/releases"
        req = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "Yancuo-Windows"},
        )
        try:
            with urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            raise DomainError(f"无法列出 Release：{exc}") from exc
        if not isinstance(payload, dict):
            raise DomainError("Release 列表格式不正确")
        return [
            self._to_remote(x)
            for x in self._parse_releases(payload)
            if x.get("tag_name")
        ]

    def create_release(
        self, owner: str, repo: str, *, tag: str, name: str, body: str = ""
    ) -> RemoteRelease:
        self._check_owner_repo(owner, repo)
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", tag):
            raise DomainError("Release tag 格式不正确")
        ids = list(self._staged.pop(tag, []))
        existing = self._find_release_raw(owner, repo, tag)
        payload = self._release_payload(tag, name, body, ids)
        try:
            if existing:
                vid = self._version_id(existing)
                if not vid or not _SAFE.fullmatch(vid):
                    raise DomainError("现有 Release 缺少可用的 version_id")
                # 合并已有附件 id，避免 update 清空
                old_ids = [
                    str(a["id"])
                    for a in (existing.get("attachments") or [])
                    if isinstance(a, dict) and a.get("id") is not None
                ]
                merged = list(dict.fromkeys([*old_ids, *ids]))
                payload["attachment_ids"] = merged
                data = self._json_request(
                    "PUT",
                    f"/api/{owner}/{repo}/releases/{vid}.json",
                    payload,
                )
            else:
                data = self._json_request(
                    "POST",
                    f"/api/{owner}/{repo}/releases.json",
                    payload,
                )
        except DomainError:
            for aid in ids:
                self.delete_attachment(aid)
            raise
        return RemoteRelease(tag=tag, name=name, assets=[], raw=data if isinstance(data, dict) else {})

    def upload_release_asset(
        self, owner: str, repo: str, *, tag: str, file_path: Path, asset_name: str
    ) -> dict[str, Any]:
        """先上传附件并暂存 id；随后 create_release 会挂载。"""
        self._check_owner_repo(owner, repo)
        # 若文件名与目标资源名不同，复制到临时同名文件再传
        src = Path(file_path)
        temp: Path | None = None
        try:
            if src.name != asset_name:
                temp = src.parent / asset_name
                if temp.resolve() != src.resolve():
                    shutil.copy2(src, temp)
                    upload_path = temp
                else:
                    upload_path = src
            else:
                upload_path = src
            aid = self.upload_attachment(upload_path, filename=asset_name)
        finally:
            if temp is not None and temp.is_file() and temp.resolve() != src.resolve():
                temp.unlink(missing_ok=True)
        self._staged.setdefault(tag, []).append(aid)
        return {"id": aid, "name": asset_name, "attachment_id": aid}

    def download_release_asset(
        self, owner: str, repo: str, *, tag: str, asset_name: str, dest: Path
    ) -> Path:
        releases = self.list_releases(owner, repo)
        target = next((r for r in releases if r.tag == tag), None)
        if not target:
            raise DomainError(f"未找到 Release：{tag}")
        asset = next(
            (
                a
                for a in target.assets
                if str(a.get("name") or a.get("title") or "") == asset_name
            ),
            None,
        )
        if not asset:
            raise DomainError(f"未找到附件：{asset_name}")
        url = asset.get("download_url") or asset.get("url")
        if not isinstance(url, str) or not url:
            raise DomainError("附件缺少下载地址")
        absolute = urljoin(self.base_url + "/", url)
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or parsed.hostname != self.host:
            raise DomainError("附件下载地址超出 GitLink 源站")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = Request(
            absolute,
            headers={"User-Agent": "Yancuo-Windows", "Accept": "*/*"},
        )
        try:
            with urlopen(req, timeout=600) as resp, dest.open("wb") as out:
                shutil.copyfileobj(resp, out)
        except Exception as exc:  # noqa: BLE001
            dest.unlink(missing_ok=True)
            raise DomainError(f"下载附件失败：{exc}") from exc
        return dest

    def delete_release(self, owner: str, repo: str, *, tag: str) -> None:
        raise DomainError("GitLink 删除 Release 暂未接入；请在网页手动清理旧备份")

    def read_sync_manifest(self, owner: str, repo: str) -> dict[str, Any] | None:
        rel = self._find_release_raw(owner, repo, _LATEST_TAG)
        if not rel:
            # 兼容旧 tag
            rel = self._find_release_raw(owner, repo, "latest-pointer")
        if not rel:
            return None
        body = rel.get("body") or ""
        if isinstance(body, str) and body.strip().startswith("{"):
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                pass
        return {"tag": rel.get("tag_name"), "raw": rel}

    def write_sync_manifest(self, owner: str, repo: str, manifest: dict[str, Any]) -> None:
        body = json.dumps(manifest, ensure_ascii=False)
        # 指针 Release：无附件，仅 body；已存在则用 version_id 更新
        self._staged.pop(_LATEST_TAG, None)
        existing = self._find_release_raw(owner, repo, _LATEST_TAG)
        payload = self._release_payload(
            _LATEST_TAG, "研错库最新备份指针", body, []
        )
        if existing:
            vid = self._version_id(existing)
            if not vid or not _SAFE.fullmatch(vid):
                raise DomainError("latest 指针 Release 缺少 version_id")
            self._json_request(
                "PUT",
                f"/api/{owner}/{repo}/releases/{vid}.json",
                payload,
            )
        else:
            self._json_request(
                "POST",
                f"/api/{owner}/{repo}/releases.json",
                payload,
            )

    def acquire_lock(self, owner: str, repo: str, device_id: str) -> bool:
        return True

    def release_lock(self, owner: str, repo: str, device_id: str) -> None:
        return None

    def test_connection(self) -> dict[str, Any]:
        self.authenticate()
        return {
            "ok": True,
            "provider": self.name,
            "token_configured": True,
            "auth": "Bearer",
            "capabilities": self.get_capabilities().to_dict(),
            "note": "使用 Attachment + Release API；请配置 owner/repo，勿依赖 user/projects 发现。",
        }

    def get_capabilities(self) -> CloudCapabilities:
        return self._caps


def _safe_filename(name: str) -> str:
    return (
        name.replace("\\", "_")
        .replace("/", "_")
        .replace('"', "_")
        .replace("\r", "_")
        .replace("\n", "_")
    )


def _find_attachment_id(value: object) -> str | None:
    if isinstance(value, dict):
        if value.get("id") is not None:
            return str(value["id"])
        for key in ("data", "attachment"):
            found = _find_attachment_id(value.get(key))
            if found:
                return found
    return None
