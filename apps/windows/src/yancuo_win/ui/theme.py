"""Application-wide light/dark theme tokens and live theme switching."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

THEME_MODES = {"system", "light", "dark"}


@dataclass(frozen=True)
class ThemeTokens:
    name: str
    bg: str
    sidebar: str
    card: str
    border: str
    text: str
    muted: str
    primary: str
    primary_hover: str
    primary_pressed: str
    danger: str
    danger_bg: str
    danger_border: str
    nav_text: str
    list_hover: str
    list_selected: str
    input_disabled: str
    upload_bg: str
    hover_border: str
    progress_bg: str
    scrollbar: str
    chip_bg: str
    chip_text: str
    tag_bg: str
    tag_text: str
    hidden_bg: str
    fallback_bg: str
    fallback_text: str


LIGHT_THEME = ThemeTokens(
    name="light",
    bg="#F5F7FA",
    sidebar="#EEF2F8",
    card="#FFFFFF",
    border="#E5EAF2",
    text="#1F2329",
    muted="#8F959E",
    primary="#3370FF",
    primary_hover="#2860E1",
    primary_pressed="#1F54C9",
    danger="#F54A45",
    danger_bg="#FEF0F0",
    danger_border="#F8B9B7",
    nav_text="#FFFFFF",
    list_hover="#F0F4FF",
    list_selected="#E8F0FF",
    input_disabled="#F0F2F5",
    upload_bg="#F8FAFD",
    hover_border="#C9D4E8",
    progress_bg="#EEF2F8",
    scrollbar="#D0D7E2",
    chip_bg="#EAF0FF",
    chip_text="#315FB8",
    tag_bg="#EEF1F5",
    tag_text="#566074",
    hidden_bg="#FBFCFE",
    fallback_bg="#FFF3D9",
    fallback_text="#744B00",
)

DARK_THEME = ThemeTokens(
    name="dark",
    bg="#11151C",
    sidebar="#171C24",
    card="#1E2530",
    border="#303A49",
    text="#E8EDF5",
    muted="#9AA6B7",
    primary="#5B8CFF",
    primary_hover="#78A0FF",
    primary_pressed="#4776DB",
    danger="#FF7875",
    danger_bg="#3B2428",
    danger_border="#7A3F45",
    nav_text="#FFFFFF",
    list_hover="#273142",
    list_selected="#2B3D61",
    input_disabled="#272E39",
    upload_bg="#181E27",
    hover_border="#465367",
    progress_bg="#202733",
    scrollbar="#465164",
    chip_bg="#263858",
    chip_text="#A9C2FF",
    tag_bg="#2A313C",
    tag_text="#BAC4D2",
    hidden_bg="#191F29",
    fallback_bg="#3B321F",
    fallback_text="#FFD88A",
)


def normalize_theme_mode(mode: str) -> str:
    normalized = str(mode or "system").strip().lower()
    if normalized not in THEME_MODES:
        raise ValueError(f"unsupported theme mode: {mode}")
    return normalized


def resolve_theme_mode(
    mode: str,
    system_color_scheme: Qt.ColorScheme | None = None,
) -> str:
    """Resolve system/light/dark into the concrete palette to render."""

    normalized = normalize_theme_mode(mode)
    if normalized != "system":
        return normalized
    return (
        "dark"
        if system_color_scheme == Qt.ColorScheme.Dark
        else "light"
    )


def theme_tokens(theme: str) -> ThemeTokens:
    return DARK_THEME if normalize_theme_mode(theme) == "dark" else LIGHT_THEME


def current_theme_name(app: QApplication | None = None) -> str:
    app = app or QApplication.instance()
    if app is None:
        return "light"
    value = app.property("yancuoResolvedTheme")
    return "dark" if value == "dark" else "light"


def app_stylesheet(theme: str = "light") -> str:
    t = theme_tokens(theme)
    return f"""
    QWidget {{
        color: {t.text};
        font-size: 13px;
    }}
    QMainWindow, QDialog, QMenu, QTabWidget::pane {{
        background: {t.bg};
    }}
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QStatusBar {{
        background: {t.card};
        color: {t.muted};
        border-top: 1px solid {t.border};
        padding: 4px 10px;
    }}
    QToolTip, QMenu {{
        background: {t.card};
        color: {t.text};
        border: 1px solid {t.border};
        padding: 6px 8px;
    }}
    QMenu::item:selected {{
        background: {t.list_selected};
    }}

    QFrame#AppSidebar {{
        background: {t.sidebar};
        border-right: 1px solid {t.border};
    }}
    QLabel#BrandTitle {{
        font-size: 18px;
        font-weight: 700;
        color: {t.text};
        padding: 4px 0 0 0;
    }}
    QLabel#BrandSubtitle {{
        font-size: 12px;
        color: {t.muted};
        padding-bottom: 8px;
    }}
    QListWidget#MainNav {{
        background: transparent;
        border: none;
        outline: none;
        padding: 4px 8px;
    }}
    QListWidget#MainNav::item {{
        height: 40px;
        padding: 8px 14px;
        margin: 2px 0;
        border-radius: 8px;
        color: {t.text};
    }}
    QListWidget#MainNav::item:hover {{
        background: {t.list_hover};
    }}
    QListWidget#MainNav::item:selected {{
        background: {t.primary};
        color: {t.nav_text};
        font-weight: 600;
    }}

    QFrame#PageRoot, QWidget#PageRoot {{
        background: {t.bg};
    }}
    QFrame#CardFrame {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 12px;
    }}
    QLabel#PageTitle {{
        font-size: 20px;
        font-weight: 700;
        color: {t.text};
    }}
    QLabel#PageHint, QLabel#MutedLabel {{
        color: {t.muted};
        font-size: 12px;
    }}
    QLabel#SectionTitle {{
        font-size: 14px;
        font-weight: 600;
        color: {t.text};
    }}
    QLabel#ImagePreview {{
        background: {t.upload_bg};
        border: 1px solid {t.border};
        border-radius: 8px;
    }}
    QLabel#DangerLabel {{
        color: {t.danger};
    }}
    QLabel#WarningLabel {{
        color: {t.fallback_text};
    }}
    QLabel#HeroBanner {{
        background: {t.primary};
        color: white;
        border-radius: 12px;
        padding: 18px 20px;
        font-size: 16px;
        font-weight: 600;
    }}
    QFrame#ContextBar {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 10px;
    }}

    QListWidget#FilterNav, QListWidget#ProblemList {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 12px;
        outline: none;
        padding: 6px;
    }}
    QListWidget#FilterNav::item {{
        height: 34px;
        padding: 6px 10px;
        margin: 1px 0;
        border-radius: 8px;
    }}
    QListWidget#ProblemList::item {{
        min-height: 52px;
        padding: 10px 12px;
        margin: 2px 4px;
        border-radius: 8px;
    }}
    QListWidget#FilterNav::item:hover, QListWidget#ProblemList::item:hover {{
        background: {t.list_hover};
    }}
    QListWidget#FilterNav::item:selected, QListWidget#ProblemList::item:selected {{
        background: {t.list_selected};
        color: {t.text};
        font-weight: 600;
    }}
    QListWidget#UploadFileList {{
        background: {t.upload_bg};
        border: 1px solid {t.border};
        border-radius: 8px;
        outline: none;
        padding: 6px;
    }}
    QListWidget#UploadFileList::item {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 6px;
        margin: 3px;
    }}
    QListWidget#UploadFileList::item:hover {{
        background: {t.list_hover};
        border-color: {t.hover_border};
    }}
    QListWidget#UploadFileList::item:selected {{
        background: {t.list_selected};
        border-color: {t.primary};
        color: {t.text};
    }}

    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
        background: {t.card};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 7px 10px;
        selection-background-color: {t.list_selected};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
        border: 1px solid {t.primary};
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled,
    QSpinBox:disabled, QComboBox:disabled {{
        color: {t.muted};
        background: {t.input_disabled};
    }}
    QComboBox QAbstractItemView {{
        background: {t.card};
        color: {t.text};
        border: 1px solid {t.border};
        selection-background-color: {t.list_selected};
    }}
    QTreeWidget, QTableWidget, QTableView {{
        background: {t.card};
        alternate-background-color: {t.sidebar};
        color: {t.text};
        border: 1px solid {t.border};
        gridline-color: {t.border};
        selection-background-color: {t.list_selected};
        selection-color: {t.text};
    }}
    QHeaderView::section {{
        background: {t.sidebar};
        color: {t.text};
        border: none;
        border-right: 1px solid {t.border};
        border-bottom: 1px solid {t.border};
        padding: 6px 8px;
    }}
    QLineEdit#SearchEdit {{
        background: {t.card};
        border: 1px solid {t.border};
        border-radius: 10px;
        padding: 8px 14px;
        min-height: 20px;
    }}

    QPushButton {{
        background: {t.card};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 8px 14px;
        min-height: 18px;
    }}
    QPushButton:hover {{
        background: {t.list_hover};
        border-color: {t.hover_border};
    }}
    QPushButton:pressed {{
        background: {t.list_selected};
    }}
    QPushButton:disabled {{
        color: {t.muted};
        background: {t.input_disabled};
    }}
    QPushButton#PrimaryButton {{
        background: {t.primary};
        color: white;
        border: none;
        font-weight: 600;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {t.primary_hover};
    }}
    QPushButton#PrimaryButton:pressed {{
        background: {t.primary_pressed};
    }}
    QPushButton#DangerButton {{
        background: {t.card};
        color: {t.danger};
        border: 1px solid {t.danger_border};
    }}
    QPushButton#DangerButton:hover {{
        background: {t.danger_bg};
    }}
    QPushButton#GhostButton {{
        background: transparent;
        border: none;
        color: {t.primary};
        padding: 6px 10px;
    }}
    QPushButton#GhostButton:hover {{
        background: {t.list_hover};
        border-radius: 8px;
    }}

    QTabWidget::pane {{
        border: 1px solid {t.border};
        background: {t.card};
    }}
    QTabBar::tab {{
        background: {t.sidebar};
        color: {t.muted};
        border: 1px solid {t.border};
        padding: 8px 14px;
    }}
    QTabBar::tab:selected {{
        background: {t.card};
        color: {t.primary};
        border-bottom-color: {t.card};
        font-weight: 600;
    }}
    QTabBar::tab:hover:!selected {{
        background: {t.list_hover};
        color: {t.text};
    }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 16px;
        height: 16px;
    }}

    QProgressBar {{
        background: {t.progress_bg};
        border: 1px solid {t.border};
        border-radius: 7px;
        min-height: 14px;
        text-align: center;
        color: {t.text};
    }}
    QProgressBar::chunk {{
        background: {t.primary};
        border-radius: 6px;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t.scrollbar};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QSplitter::handle {{
        background: transparent;
        width: 6px;
    }}
    """


def _app_palette(tokens: ThemeTokens) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: tokens.bg,
        QPalette.ColorRole.WindowText: tokens.text,
        QPalette.ColorRole.Base: tokens.card,
        QPalette.ColorRole.AlternateBase: tokens.sidebar,
        QPalette.ColorRole.ToolTipBase: tokens.card,
        QPalette.ColorRole.ToolTipText: tokens.text,
        QPalette.ColorRole.Text: tokens.text,
        QPalette.ColorRole.Button: tokens.card,
        QPalette.ColorRole.ButtonText: tokens.text,
        QPalette.ColorRole.BrightText: "#FFFFFF",
        QPalette.ColorRole.Link: tokens.primary,
        QPalette.ColorRole.Highlight: tokens.primary,
        QPalette.ColorRole.HighlightedText: tokens.nav_text,
        QPalette.ColorRole.PlaceholderText: tokens.muted,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor(tokens.muted),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor(tokens.muted),
    )
    return palette


class ThemeManager(QObject):
    """Apply one resolved palette to Qt widgets and embedded HTML readers."""

    theme_changed = Signal(str)

    def __init__(self, app: QApplication, mode: str = "system") -> None:
        super().__init__(app)
        self.app = app
        self.mode = normalize_theme_mode(mode)
        self.resolved = ""
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_theme_changed)
        self.apply()

    def set_mode(self, mode: str) -> str:
        self.mode = normalize_theme_mode(mode)
        self.app.setProperty("yancuoThemeMode", self.mode)
        return self.apply()

    def apply(self) -> str:
        hints = self.app.styleHints()
        scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else None
        resolved = resolve_theme_mode(self.mode, scheme)
        tokens = theme_tokens(resolved)
        self.app.setPalette(_app_palette(tokens))
        self.app.setStyleSheet(app_stylesheet(resolved))
        self.app.setProperty("yancuoThemeMode", self.mode)
        self.app.setProperty("yancuoResolvedTheme", resolved)
        changed = resolved != self.resolved
        self.resolved = resolved
        if changed:
            self.theme_changed.emit(resolved)
        return resolved

    def _on_system_theme_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self.mode == "system":
            self.apply()


def get_theme_manager(app: QApplication | None = None) -> ThemeManager | None:
    app = app or QApplication.instance()
    return getattr(app, "_yancuo_theme_manager", None) if app else None


def apply_app_theme(
    app: QApplication,
    mode: str = "system",
) -> ThemeManager:
    font = QFont("Microsoft YaHei UI", 10)
    if not font.exactMatch():
        font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setStyle("Fusion")
    manager = get_theme_manager(app)
    if manager is None:
        manager = ThemeManager(app, mode)
        app._yancuo_theme_manager = manager
    else:
        manager.set_mode(mode)
    return manager
