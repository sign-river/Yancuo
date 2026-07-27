"""Theme resolution and stylesheet regression tests."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

import yancuo_win.ui.widgets as widgets_module
from yancuo_win.ui.theme import (
    DARK_THEME,
    LIGHT_THEME,
    app_stylesheet,
    normalize_theme_mode,
    resolve_theme_mode,
)


def test_page_header_does_not_show_children_during_construction(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    visibility_requests: list[bool] = []

    class TrackingLabel(QLabel):
        def setVisible(self, visible: bool) -> None:  # noqa: N802
            visibility_requests.append(visible)
            super().setVisible(visible)

    monkeypatch.setattr(widgets_module, "QLabel", TrackingLabel)
    header = widgets_module.PageHeader("标题", "说明")

    assert True not in visibility_requests
    header.close()
    app.processEvents()


def test_explicit_theme_ignores_system_color_scheme() -> None:
    assert resolve_theme_mode("light", Qt.ColorScheme.Dark) == "light"
    assert resolve_theme_mode("dark", Qt.ColorScheme.Light) == "dark"


def test_system_theme_resolves_dark_and_defaults_unknown_to_light() -> None:
    assert resolve_theme_mode("system", Qt.ColorScheme.Dark) == "dark"
    assert resolve_theme_mode("system", Qt.ColorScheme.Unknown) == "light"


def test_unknown_theme_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_theme_mode("sepia")


def test_stylesheet_covers_dark_tabs_inputs_and_cards() -> None:
    rendered = app_stylesheet("dark")

    assert DARK_THEME.bg in rendered
    assert DARK_THEME.card in rendered
    assert "QTabBar::tab:selected" in rendered
    assert "QPushButton#LibraryViewButton:checked" in rendered
    assert "QTreeWidget#KnowledgeTree::item:selected" in rendered
    assert "QComboBox QAbstractItemView" in rendered
    assert LIGHT_THEME.bg not in rendered
