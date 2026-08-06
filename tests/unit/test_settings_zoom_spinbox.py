"""预览缩放输入框：聚焦全选、直接输入替换、越界吸附。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSpinBox, QApplication

from yancuo_win.config.settings import AppSettings
from yancuo_win.ui.settings_dialog import ServiceSettingsPage, _ZoomSpinBox


class _FakePaths:
    root = Path(__import__("tempfile").mkdtemp())
    database = root / "error_book.db"
    asset_dir = root / "assets"
    inbox_dir = root / "inbox"
    cache_dir = root / "cache"
    export_dir = root / "exports"
    backup_dir = root / "backups"
    template_dir = root / "templates"
    workspace_dir = root / "workspaces"
    log_dir = root / "logs"


def _appearance_page() -> tuple[ServiceSettingsPage, object]:
    app = QApplication.instance() or QApplication([])
    del app
    runtime = type(
        "Runtime",
        (),
        {
            "settings": AppSettings(),
            "paths": _FakePaths,
        },
    )()
    page = ServiceSettingsPage(runtime, "appearance")
    page.resize(700, 500)
    page.show()
    return page, runtime


def test_zoom_spinbox_selects_all_on_click_focus() -> None:
    page, _ = _appearance_page()
    app = QApplication.instance()
    app.processEvents()
    spin = page.preview_zoom
    assert isinstance(spin, _ZoomSpinBox)
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=QPoint(30, spin.height() // 2))
    app.processEvents()
    assert spin.lineEdit().selectedText() == str(spin.value())
    page.close()


def test_zoom_spinbox_typing_replaces_and_keeps_value_on_focus_out() -> None:
    page, runtime = _appearance_page()
    app = QApplication.instance()
    app.processEvents()
    spin = page.preview_zoom
    QTest.mouseClick(spin, Qt.MouseButton.LeftButton, pos=QPoint(30, spin.height() // 2))
    app.processEvents()
    QTest.keyClicks(spin, "120")
    app.processEvents()
    assert spin.value() == 120
    page.apply_theme_button.setFocus(Qt.FocusReason.MouseFocusReason)
    app.processEvents()
    assert spin.value() == 120
    assert spin.lineEdit().text() == "120%"
    page.close()


def test_zoom_spinbox_out_of_range_snaps_to_nearest_not_previous() -> None:
    page, _ = _appearance_page()
    app = QApplication.instance()
    app.processEvents()
    spin = page.preview_zoom
    assert (
        spin.correctionMode()
        == QAbstractSpinBox.CorrectionMode.CorrectToNearestValue
    )
    spin.setValue(96)
    page.theme_buttons["light"].setFocus(Qt.FocusReason.MouseFocusReason)
    app.processEvents()
    spin.setFocus(Qt.FocusReason.OtherFocusReason)
    app.processEvents()
    QTest.keyClicks(spin, "2")
    QTest.keyClicks(spin, "0")
    app.processEvents()
    page.theme_buttons["light"].setFocus(Qt.FocusReason.MouseFocusReason)
    app.processEvents()
    # "越界的 20 吸附到最近合法值 80，而不是回退到旧值 96"
    assert spin.value() == 80
    assert spin.value() != 96
    page.close()
