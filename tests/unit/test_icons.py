"""Coverage for the shared theme-aware SVG icon service."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from yancuo_win.ui.icons import available_icons, bind_icon, symbolic_icon
from yancuo_win.ui.widgets import IconButton


def test_symbolic_icons_are_available_and_renderable() -> None:
    QApplication.instance() or QApplication([])

    assert {
        "chevron-down",
        "chevron-left",
        "chevron-right",
        "chevron-up",
        "more-horizontal",
        "search",
    } <= set(available_icons())
    assert not symbolic_icon("search", "light").isNull()
    assert not symbolic_icon("search", "dark").isNull()


def test_unknown_symbolic_icon_is_rejected() -> None:
    QApplication.instance() or QApplication([])

    with pytest.raises(ValueError, match="unknown icon"):
        symbolic_icon("not-an-icon")


def test_icon_button_and_binding_preserve_accessibility() -> None:
    QApplication.instance() or QApplication([])

    button = IconButton("chevron-left", "收起导航栏")
    assert button.text() == ""
    assert button.accessibleName() == "收起导航栏"
    assert not button.icon().isNull()

    bind_icon(button, "chevron-right", size=16)
    assert button.iconSize().width() == 16
    assert not button.icon().isNull()
