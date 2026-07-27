"""导入导出。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yancuo_win.import_export.ebpack import EbpackService
    from yancuo_win.import_export.gmshare import GmshareService
    from yancuo_win.import_export.workspace import WorkspaceService

__all__ = ["EbpackService", "GmshareService", "WorkspaceService"]


def __getattr__(name: str) -> Any:
    if name == "EbpackService":
        from yancuo_win.import_export.ebpack import EbpackService

        return EbpackService
    if name == "GmshareService":
        from yancuo_win.import_export.gmshare import GmshareService

        return GmshareService
    if name == "WorkspaceService":
        from yancuo_win.import_export.workspace import WorkspaceService

        return WorkspaceService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
