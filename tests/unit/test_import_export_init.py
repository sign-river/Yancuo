"""import_export 包导入行为。"""

from __future__ import annotations

import importlib
import sys


def test_import_export_package_is_lazy() -> None:
    for module_name in list(sys.modules):
        if module_name == "yancuo_win.import_export" or module_name.startswith(
            "yancuo_win.import_export."
        ):
            del sys.modules[module_name]

    module = importlib.import_module("yancuo_win.import_export")

    assert module.__all__ == ["EbpackService", "GmshareService", "WorkspaceService"]
    assert "yancuo_win.import_export.ebpack" not in sys.modules
    assert "yancuo_win.import_export.gmshare" not in sys.modules
    assert "yancuo_win.import_export.workspace" not in sys.modules
