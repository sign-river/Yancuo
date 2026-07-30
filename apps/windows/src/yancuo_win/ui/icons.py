"""Small theme-aware SVG icon service used by desktop controls."""

from __future__ import annotations

from functools import lru_cache
from html import escape

from PySide6.QtCore import QByteArray, QObject, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QAbstractButton

from yancuo_win.ui.theme import current_theme_name, get_theme_manager, theme_tokens


_ICON_CONTENT = {
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m6 15 6-6 6 6"/>',
    "more-horizontal": (
        '<circle cx="5" cy="12" r="1.25" fill="currentColor" stroke="none"/>'
        '<circle cx="12" cy="12" r="1.25" fill="currentColor" stroke="none"/>'
        '<circle cx="19" cy="12" r="1.25" fill="currentColor" stroke="none"/>'
    ),
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "note": (
        '<path d="M6.5 3.5h8l3 3v14h-11z"/>'
        '<path d="M14.5 3.5v3h3M9 11h6M9 15h6"/>'
    ),
}


def available_icons() -> tuple[str, ...]:
    return tuple(sorted(_ICON_CONTENT))


def _svg(name: str, color: str) -> bytes:
    if name not in _ICON_CONTENT:
        raise ValueError(f"unknown icon: {name}")
    safe_color = escape(color, quote=True)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round" '
        f'style="color:{safe_color}">{_ICON_CONTENT[name]}</svg>'
    ).encode()


def _render(name: str, color: str, logical_size: int, dpr: int) -> QPixmap:
    physical_size = logical_size * dpr
    pixmap = QPixmap(physical_size, physical_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, physical_size, physical_size))
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


@lru_cache(maxsize=128)
def _cached_icon(
    name: str,
    normal_color: str,
    active_color: str,
    disabled_color: str,
) -> QIcon:
    icon = QIcon()
    for logical_size in (16, 18, 20, 24):
        for dpr in (1, 2):
            icon.addPixmap(
                _render(name, normal_color, logical_size, dpr),
                QIcon.Mode.Normal,
                QIcon.State.Off,
            )
            active = _render(name, active_color, logical_size, dpr)
            icon.addPixmap(active, QIcon.Mode.Active, QIcon.State.Off)
            icon.addPixmap(active, QIcon.Mode.Selected, QIcon.State.Off)
            icon.addPixmap(
                _render(name, disabled_color, logical_size, dpr),
                QIcon.Mode.Disabled,
                QIcon.State.Off,
            )
    return icon


def symbolic_icon(name: str, theme: str | None = None) -> QIcon:
    """Return a crisp icon whose states use the active theme's semantic colors."""

    tokens = theme_tokens(theme or current_theme_name())
    return _cached_icon(name, tokens.muted, tokens.primary, tokens.border_strong)


class _IconBinding(QObject):
    def __init__(
        self,
        button: QAbstractButton,
        name: str,
        logical_size: int,
    ) -> None:
        super().__init__(button)
        self.button = button
        self.name = name
        self.logical_size = logical_size
        manager = get_theme_manager(QApplication.instance())
        if manager is not None:
            manager.theme_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        self.button.setIcon(symbolic_icon(self.name))
        self.button.setIconSize(QSize(self.logical_size, self.logical_size))


def bind_icon(
    button: QAbstractButton,
    name: str,
    *,
    size: int = 18,
) -> QAbstractButton:
    """Attach a named SVG icon and keep it in sync with live theme changes."""

    existing = getattr(button, "_yancuo_icon_binding", None)
    if existing is not None:
        existing.deleteLater()
    button._yancuo_icon_binding = _IconBinding(button, name, size)  # type: ignore[attr-defined]
    return button
