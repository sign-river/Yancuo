"""基础设施：路径、日志。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from yancuo_win.config.settings import AppSettings, PathsConfig


@dataclass(frozen=True)
class DataPaths:
    root: Path
    database: Path
    asset_dir: Path
    asset_objects_dir: Path
    inbox_dir: Path
    cache_dir: Path
    export_dir: Path
    backup_dir: Path
    template_dir: Path
    workspace_dir: Path
    log_dir: Path
    identity_file: Path

    def ensure_directories(self) -> None:
        for path in (
            self.root,
            self.asset_dir,
            self.asset_objects_dir,
            self.inbox_dir,
            self.cache_dir,
            self.export_dir,
            self.backup_dir,
            self.template_dir,
            self.workspace_dir,
            self.log_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def resolve_data_root(explicit: Path | None = None) -> Path:
    import os

    if explicit is not None:
        return explicit.expanduser().resolve()
    env = os.environ.get("YANCUO_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # 默认：apps/windows/.yancuo_data
    return Path(__file__).resolve().parents[3] / ".yancuo_data"


def _resolve_member(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def build_data_paths(root: Path, paths_cfg: PathsConfig) -> DataPaths:
    asset_dir = _resolve_member(root, paths_cfg.asset_dir)
    return DataPaths(
        root=root.resolve(),
        database=_resolve_member(root, paths_cfg.database),
        asset_dir=asset_dir,
        asset_objects_dir=asset_dir / "objects",
        inbox_dir=_resolve_member(root, paths_cfg.inbox_dir),
        cache_dir=_resolve_member(root, paths_cfg.cache_dir),
        export_dir=_resolve_member(root, paths_cfg.export_dir),
        backup_dir=_resolve_member(root, paths_cfg.backup_dir),
        template_dir=_resolve_member(root, paths_cfg.template_dir),
        workspace_dir=_resolve_member(root, paths_cfg.workspace_dir),
        log_dir=_resolve_member(root, paths_cfg.log_dir),
        identity_file=(root / "identity.json").resolve(),
    )


def setup_logging(log_dir: Path, level: int = logging.INFO) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("yancuo")
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(
        log_dir / "yancuo.log", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def describe_runtime_layout(settings: AppSettings, paths: DataPaths) -> str:
    return (
        f"data_root={paths.root}\n"
        f"database={paths.database}\n"
        f"schema_target={settings.application.schema_version}\n"
    )
