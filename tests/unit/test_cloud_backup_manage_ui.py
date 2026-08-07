"""云端备份管理 UI：设置页卡片存在，列表填充与删除按钮联动正常。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.config.settings import default_toml_path
from yancuo_win.ui.main_window import MainWindow


@pytest.fixture()
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setenv("YANCUO_AI__DEFAULT_PROVIDER", "mock")
    app = QApplication.instance() or QApplication([])
    runtime = bootstrap_runtime()
    main = MainWindow(runtime)
    app.processEvents()
    yield main
    main.close()


def test_cloud_backup_manage_card_populates(window: MainWindow) -> None:
    main = window
    assert hasattr(main, "cloud_backup_list")
    assert hasattr(main, "cloud_manage_summary")
    assert hasattr(main, "cloud_manage_preview_button")
    assert main.cloud_manage_delete_button.isEnabled() is False
    assert main.cloud_manage_preview_button.isEnabled() is False

    main._on_cloud_backups_loaded(
        [
            {"tag": "data-v1-p1-20260806T120000Z", "asset_size": 2048, "is_latest": True},
            {"tag": "data-v1-p1-20260805T100000Z", "asset_size": 1024, "is_latest": False},
        ]
    )
    assert main.cloud_backup_list.count() == 2
    assert "备份于" in main.cloud_backup_list.item(0).text()
    assert "当前资料" in main.cloud_backup_list.item(0).text()

    main.cloud_backup_list.setCurrentRow(0)
    assert main.cloud_manage_delete_button.isEnabled() is True
    assert main.cloud_manage_preview_button.isEnabled() is True

    main._on_cloud_backups_loaded([])
    assert main.cloud_backup_list.count() == 0
    assert main.cloud_manage_delete_button.isEnabled() is False
    assert main.cloud_manage_preview_button.isEnabled() is False
