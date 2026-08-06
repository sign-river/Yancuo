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
    "camera": (
        '<path d="m14.5 4.5 1.5 2.5h3a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h3l1.5-2.5z"/>'
        '<circle cx="12" cy="13" r="3"/>'
    ),
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "note": (
        '<path d="M6.5 3.5h8l3 3v14h-11z"/>'
        '<path d="M14.5 3.5v3h3M9 11h6M9 15h6"/>'
    ),
    "eye": (
        '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "eye-off": (
        '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/>'
        '<path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>'
        '<path d="m6.5 6.5 11 11"/>'
        '<path d="m1 1 22 22"/>'
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
