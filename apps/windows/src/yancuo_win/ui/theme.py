"""研错库浅色蓝白主题（飞书 / 现代桌面工具风格）。"""

from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

# 设计令牌
COLOR_BG = "#F5F7FA"
COLOR_SIDEBAR = "#EEF2F8"
COLOR_CARD = "#FFFFFF"
COLOR_BORDER = "#E5EAF2"
COLOR_TEXT = "#1F2329"
COLOR_MUTED = "#8F959E"
COLOR_PRIMARY = "#3370FF"
COLOR_PRIMARY_HOVER = "#2860E1"
COLOR_PRIMARY_PRESSED = "#1F54C9"
COLOR_DANGER = "#F54A45"
COLOR_DANGER_BG = "#FEF0F0"
COLOR_NAV_ACTIVE = "#3370FF"
COLOR_NAV_ACTIVE_TEXT = "#FFFFFF"
COLOR_LIST_HOVER = "#F0F4FF"
COLOR_LIST_SELECTED = "#E8F0FF"


def app_stylesheet() -> str:
    return f"""
    QWidget {{
        color: {COLOR_TEXT};
        font-size: 13px;
    }}
    QMainWindow, QDialog {{
        background: {COLOR_BG};
    }}
    QStatusBar {{
        background: {COLOR_CARD};
        color: {COLOR_MUTED};
        border-top: 1px solid {COLOR_BORDER};
        padding: 4px 10px;
    }}
    QToolTip {{
        background: {COLOR_CARD};
        color: {COLOR_TEXT};
        border: 1px solid {COLOR_BORDER};
        padding: 6px 8px;
        border-radius: 6px;
    }}

    /* —— 侧栏 —— */
    QFrame#AppSidebar {{
        background: {COLOR_SIDEBAR};
        border-right: 1px solid {COLOR_BORDER};
    }}
    QLabel#BrandTitle {{
        font-size: 18px;
        font-weight: 700;
        color: {COLOR_TEXT};
        padding: 4px 0 0 0;
    }}
    QLabel#BrandSubtitle {{
        font-size: 12px;
        color: {COLOR_MUTED};
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
        color: {COLOR_TEXT};
    }}
    QListWidget#MainNav::item:hover {{
        background: rgba(51, 112, 255, 0.08);
    }}
    QListWidget#MainNav::item:selected {{
        background: {COLOR_NAV_ACTIVE};
        color: {COLOR_NAV_ACTIVE_TEXT};
        font-weight: 600;
    }}

    /* —— 卡片与页面 —— */
    QFrame#PageRoot, QWidget#PageRoot {{
        background: {COLOR_BG};
    }}
    QFrame#CardFrame {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: 12px;
    }}
    QLabel#PageTitle {{
        font-size: 20px;
        font-weight: 700;
        color: {COLOR_TEXT};
    }}
    QLabel#PageHint, QLabel#MutedLabel {{
        color: {COLOR_MUTED};
        font-size: 12px;
    }}
    QLabel#SectionTitle {{
        font-size: 14px;
        font-weight: 600;
        color: {COLOR_TEXT};
    }}
    QLabel#HeroBanner {{
        background: {COLOR_PRIMARY};
        color: white;
        border-radius: 12px;
        padding: 18px 20px;
        font-size: 16px;
        font-weight: 600;
    }}
    QFrame#ContextBar {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: 10px;
    }}

    /* —— 次级筛选 —— */
    QListWidget#FilterNav {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
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
    QListWidget#FilterNav::item:hover {{
        background: {COLOR_LIST_HOVER};
    }}
    QListWidget#FilterNav::item:selected {{
        background: {COLOR_LIST_SELECTED};
        color: {COLOR_PRIMARY};
        font-weight: 600;
    }}

    /* —— 题列表 —— */
    QListWidget#ProblemList {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: 12px;
        outline: none;
        padding: 4px;
    }}
    QListWidget#ProblemList::item {{
        min-height: 52px;
        padding: 10px 12px;
        margin: 2px 4px;
        border-radius: 8px;
        border-bottom: 1px solid transparent;
    }}
    QListWidget#ProblemList::item:hover {{
        background: {COLOR_LIST_HOVER};
    }}
    QListWidget#ProblemList::item:selected {{
        background: {COLOR_LIST_SELECTED};
        color: {COLOR_TEXT};
    }}

    /* —— 输入 —— */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QComboBox {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
        padding: 7px 10px;
        selection-background-color: {COLOR_LIST_SELECTED};
    }}
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
        border: 1px solid {COLOR_PRIMARY};
    }}
    QLineEdit#SearchEdit {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: 10px;
        padding: 8px 14px;
        min-height: 20px;
    }}

    /* —— 按钮 —— */
    QPushButton {{
        background: {COLOR_CARD};
        border: 1px solid {COLOR_BORDER};
        border-radius: 8px;
        padding: 8px 14px;
        min-height: 18px;
    }}
    QPushButton:hover {{
        background: {COLOR_LIST_HOVER};
        border-color: #C9D4E8;
    }}
    QPushButton:pressed {{
        background: {COLOR_LIST_SELECTED};
    }}
    QPushButton:disabled {{
        color: {COLOR_MUTED};
        background: #F0F2F5;
    }}
    QPushButton#PrimaryButton {{
        background: {COLOR_PRIMARY};
        color: white;
        border: none;
        font-weight: 600;
    }}
    QPushButton#PrimaryButton:hover {{
        background: {COLOR_PRIMARY_HOVER};
    }}
    QPushButton#PrimaryButton:pressed {{
        background: {COLOR_PRIMARY_PRESSED};
    }}
    QPushButton#DangerButton {{
        background: {COLOR_CARD};
        color: {COLOR_DANGER};
        border: 1px solid #F8B9B7;
    }}
    QPushButton#DangerButton:hover {{
        background: {COLOR_DANGER_BG};
    }}
    QPushButton#GhostButton {{
        background: transparent;
        border: none;
        color: {COLOR_PRIMARY};
        padding: 6px 10px;
    }}
    QPushButton#GhostButton:hover {{
        background: {COLOR_LIST_HOVER};
        border-radius: 8px;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: #D0D7E2;
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


def apply_app_theme(app: QApplication) -> None:
    font = QFont("Microsoft YaHei UI", 10)
    if not font.exactMatch():
        font = QFont("Segoe UI", 10)
    app.setFont(font)
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())
