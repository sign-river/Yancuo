"""按配置创建云端提供商。"""

from __future__ import annotations

from pathlib import Path

from yancuo_win.cloud.base import CloudProvider
from yancuo_win.cloud.gitlink import GitLinkProvider
from yancuo_win.cloud.local_folder import LocalFolderProvider
from yancuo_win.config.settings import AppSettings
from yancuo_win.domain.rules import DomainError


def get_cloud_provider(settings: AppSettings, *, local_root: Path | None = None) -> CloudProvider:
    import os

    name = settings.cloud.default_provider
    if name == "local_folder":
        root = local_root
        if root is None:
            env = os.environ.get("YANCUO_CLOUD_LOCAL_ROOT")
            root = Path(env) if env else Path(settings.paths.backup_dir) / "cloud_local"
        return LocalFolderProvider(root)
    if name == "gitlink":
        cfg = settings.cloud.gitlink
        return GitLinkProvider(
            base_url=cfg.base_url or "https://www.gitlink.org.cn",
            credential_key=cfg.credential_key or "yancuo_gitlink_token",
        )
    if name == "github":
        raise DomainError("GitHub 提供商将在阶段 H 实现；请先使用 gitlink 或 local_folder")
    raise DomainError(f"未知云端提供商：{name}")
