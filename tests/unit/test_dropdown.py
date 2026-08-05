"""Dropdown menu positioning behavior tests."""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from yancuo_win.ui.widgets import show_dropdown_menu


class _FakeScreen:
    def availableGeometry(self) -> QRect:
        return QRect(0, 0, 1920, 1080)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _anchor(x: int, y: int, width: int = 160, height: int = 40) -> QWidget:
    app = _app()
    widget = QWidget()
    widget.resize(width, height)
    widget.move(x, y)
    widget.show()
    app.processEvents()
    return widget


def _capture_popup(monkeypatch, menu: QMenu) -> dict:
    captured: dict = {}
    monkeypatch.setattr(menu, "popup", lambda pos: captured.update(pos=pos))
    monkeypatch.setattr(
        "PySide6.QtGui.QGuiApplication.screenAt", lambda *_a: _FakeScreen()
    )
    return captured


def test_dropdown_opens_below_anchor_with_gap() -> None:
    anchor = _anchor(100, 100)
    menu = QMenu()
    menu.addAction("动作一")
    captured: dict = {}
    original = menu.popup
    menu.popup = lambda pos: captured.update(pos=pos)
    import PySide6.QtGui as _qtgui
    original_screen = _qtgui.QGuiApplication.screenAt
    _qtgui.QGuiApplication.screenAt = lambda *_a: _FakeScreen()
    try:
        show_dropdown_menu(menu, anchor)
    finally:
        menu.popup = original
        _qtgui.QGuiApplication.screenAt = original_screen
    pos = captured["pos"]
    global_anchor = anchor.mapToGlobal(anchor.rect().topLeft())
    assert pos.x() == global_anchor.x()
    assert pos.y() == global_anchor.y() + anchor.height() - 1 + 6
    menu.deleteLater()
    anchor.close()


def test_dropdown_shifts_left_near_right_edge() -> None:
    anchor = _anchor(1800, 100, width=200)
    menu = QMenu()
    menu.addAction("一个非常长的动作名称用来撑宽菜单")
    menu.addAction("另一个动作")
    captured: dict = {}
    original = menu.popup
    menu.popup = lambda pos: captured.update(pos=pos)
    import PySide6.QtGui as _qtgui
    original_screen = _qtgui.QGuiApplication.screenAt
    _qtgui.QGuiApplication.screenAt = lambda *_a: _FakeScreen()
    try:
        show_dropdown_menu(menu, anchor)
    finally:
        menu.popup = original
        _qtgui.QGuiApplication.screenAt = original_screen
    pos = captured["pos"]
    assert pos.x() + menu.minimumWidth() <= 1920 - 8
    menu.deleteLater()
    anchor.close()


def test_dropdown_flips_above_when_bottom_space_insufficient() -> None:
    anchor = _anchor(100, 1020, width=200)
    menu = QMenu()
    menu.addAction("动作")
    menu.addAction("动作二")
    menu.addAction("动作三")
    captured: dict = {}
    original = menu.popup
    menu.popup = lambda pos: captured.update(pos=pos)
    import PySide6.QtGui as _qtgui
    original_screen = _qtgui.QGuiApplication.screenAt
    _qtgui.QGuiApplication.screenAt = lambda *_a: _FakeScreen()
    try:
        show_dropdown_menu(menu, anchor)
    finally:
        menu.popup = original
        _qtgui.QGuiApplication.screenAt = original_screen
    pos = captured["pos"]
    global_anchor = anchor.mapToGlobal(anchor.rect().topLeft())
    assert pos.y() <= global_anchor.y() - 6
    menu.deleteLater()
    anchor.close()


def test_dropdown_min_width_uses_anchor_and_caps_at_max() -> None:
    anchor = _anchor(50, 50, width=260)
    menu = QMenu()
    menu.addAction("A")
    captured: dict = {}
    original = menu.popup
    menu.popup = lambda pos: captured.update(pos=pos)
    import PySide6.QtGui as _qtgui
    original_screen = _qtgui.QGuiApplication.screenAt
    _qtgui.QGuiApplication.screenAt = lambda *_a: _FakeScreen()
    try:
        show_dropdown_menu(menu, anchor)
    finally:
        menu.popup = original
        _qtgui.QGuiApplication.screenAt = original_screen
    assert menu.minimumWidth() >= 260
    assert menu.minimumWidth() <= 320
    menu.deleteLater()
    anchor.close()
