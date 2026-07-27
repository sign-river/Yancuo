"""可复用的轻量 UI 控件。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)


def primary_button(text: str, parent: QWidget | None = None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("PrimaryButton")
    return btn


def danger_button(text: str, parent: QWidget | None = None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("DangerButton")
    return btn


def ghost_button(text: str, parent: QWidget | None = None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("GhostButton")
    return btn


def default_button(text: str, parent: QWidget | None = None) -> QPushButton:
    """Standard secondary action with the global button treatment."""

    return QPushButton(text, parent)


class IconButton(QPushButton):
    """Small accessible icon action backed by Qt's coherent standard icon set."""

    def __init__(
        self,
        icon: QStyle.StandardPixmap,
        tooltip: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("IconButton")
        self.setIcon(self.style().standardIcon(icon))
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setFixedSize(32, 32)


class SearchInput(QLineEdit):
    """A compact search field with standard search and clear affordances."""

    def __init__(
        self,
        placeholder: str = "搜索",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SearchInput")
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setAccessibleName(placeholder)
        search_action = QAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView),
            "搜索",
            self,
        )
        search_action.setEnabled(False)
        self.addAction(search_action, QLineEdit.ActionPosition.LeadingPosition)


class StatusTag(QLabel):
    """A restrained, semantic state label used across lists and details."""

    _variants = {
        "default": "StatusTag",
        "active": "StatusTagActive",
        "success": "StatusTagSuccess",
        "warning": "StatusTagWarning",
        "danger": "StatusTagDanger",
        "muted": "StatusTagMuted",
    }

    def __init__(
        self,
        text: str,
        variant: str = "default",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_variant(variant)

    def set_variant(self, variant: str) -> None:
        self.setObjectName(self._variants.get(variant, self._variants["default"]))
        self.style().unpolish(self)
        self.style().polish(self)


class LoadingSkeleton(QFrame):
    """Neutral loading placeholder; callers choose the amount of expected content."""

    def __init__(self, rows: int = 3, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LoadingSkeleton")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        for index in range(max(1, rows)):
            line = QFrame()
            line.setObjectName("SkeletonLineLong" if index % 3 else "SkeletonLineShort")
            line.setFixedHeight(12)
            layout.addWidget(line)


class ErrorState(QWidget):
    """Explicit retry state for content that failed to load."""

    retry_requested = Signal()

    def __init__(
        self,
        title: str = "加载失败",
        description: str = "暂时无法加载内容，请重试。",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 32, 20, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body = QLabel(description)
        body.setObjectName("MutedLabel")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        retry = default_button("重新加载")
        retry.clicked.connect(self.retry_requested)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addWidget(retry, alignment=Qt.AlignmentFlag.AlignCenter)


class ToastMessage(QFrame):
    """Non-blocking confirmation for short, reversible success feedback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToastMessage")
        self.setVisible(False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self.label = QLabel("")
        self.label.setObjectName("ToastText")
        layout.addWidget(self.label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, message: str, duration_ms: int = 2800) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.label.setText(message)
        self.adjustSize()
        x = max(12, (parent.width() - self.width()) // 2)
        self.move(x, 68)
        self.raise_()
        self.show()
        self._timer.start(duration_ms)


class BatchActionBar(QFrame):
    """Bottom or inline selection toolbar with one intentionally primary action."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BatchActionBar")
        layout = QHBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        self.summary = QLabel("未选择内容")
        self.summary.setObjectName("MutedLabel")
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def set_selection_count(self, count: int, noun: str = "项") -> None:
        self.summary.setText(f"已选择 {count} {noun}" if count else "未选择内容")
        self.setVisible(count > 0)

    def add_action(self, button: QPushButton) -> None:
        self.actions.addWidget(button)

class ConfirmDialog(QDialog):
    """Reusable confirmation dialog for destructive or submit actions."""

    def __init__(
        self,
        title: str,
        message: str,
        confirm_text: str = "确认",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ConfirmDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        message_label = QLabel(message)
        message_label.setObjectName("MutedLabel")
        message_label.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(message_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(confirm_text)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @classmethod
    def ask(
        cls,
        parent: QWidget,
        title: str,
        message: str,
        confirm_text: str = "确认",
    ) -> bool:
        return cls(title, message, confirm_text, parent).exec() == QDialog.DialogCode.Accepted


class PageHeader(QWidget):
    """Consistent title, description, and action alignment for content pages."""

    def __init__(self, title: str, description: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        labels = QVBoxLayout()
        labels.setSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("PageTitle")
        labels.addWidget(self.title)
        self.description = QLabel(description)
        self.description.setObjectName("PageHint")
        self.description.setWordWrap(True)
        if not description:
            self.description.hide()
        labels.addWidget(self.description)
        layout.addLayout(labels)
        layout.addStretch(1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)

    def add_action(self, button: QPushButton) -> None:
        self.actions.addWidget(button)

    def add_leading(self, widget: QWidget) -> None:
        self._layout.insertWidget(0, widget)


class AppHeader(QFrame):
    """Persistent, quiet application header for the current top-level module."""

    def __init__(self, title: str = "工作台", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AppHeader")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(12)
        self.title = QLabel(title)
        self.title.setObjectName("AppHeaderTitle")
        layout.addWidget(self.title)
        layout.addStretch(1)
        self.context = QLabel("本地学习资料")
        self.context.setObjectName("MutedLabel")
        layout.addWidget(self.context)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)
        self.setFixedHeight(56)

    def set_title(self, title: str) -> None:
        self.title.setText(title)

    def add_action(self, button: QPushButton) -> None:
        self.actions.addWidget(button)


class EmptyState(QWidget):
    """Small reusable empty-state surface without decorative artwork."""

    def __init__(self, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 32, 20, 32)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body = QLabel(description)
        body.setObjectName("MutedLabel")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        layout.addWidget(body)


class CardFrame(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CardFrame")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(10)

    @property
    def body(self) -> QVBoxLayout:
        return self._layout

    def add_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        self._layout.addWidget(label)
        return label

    def add_hint(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("MutedLabel")
        label.setWordWrap(True)
        self._layout.addWidget(label)
        return label


def button_row(*buttons: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    for btn in buttons:
        row.addWidget(btn)
    row.addStretch(1)
    return row
