"""可复用的轻量 UI 控件。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import QModelIndex, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProxyStyle,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.ui.icons import bind_icon, symbolic_icon


@contextmanager
def deferred_view_updates(view: QAbstractItemView) -> Iterator[None]:
    """Avoid repainting an item view for every row during a bulk refresh."""

    updates_were_enabled = view.updatesEnabled()
    view.setUpdatesEnabled(False)
    try:
        yield
    finally:
        view.setUpdatesEnabled(updates_were_enabled)
        if updates_were_enabled:
            view.viewport().update()


def set_tab_order_chain(*widgets: QWidget) -> None:
    """Declare a predictable keyboard focus path for a group of controls."""

    focusable = [widget for widget in widgets if widget is not None]
    for current, following in zip(focusable, focusable[1:]):
        if current.window() is following.window():
            QWidget.setTabOrder(current, following)
            continue

        def apply_when_attached(
            first: QWidget = current,
            second: QWidget = following,
        ) -> None:
            if first.window() is second.window():
                QWidget.setTabOrder(first, second)

        QTimer.singleShot(0, apply_when_attached)


def describe_field(
    widget: QWidget,
    name: str,
    description: str | None = None,
) -> QWidget:
    """Attach a concise screen-reader label to a form control."""

    widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)
    return widget


class ThemedTreeBranchStyle(QProxyStyle):
    """Draw the shared, theme-aware disclosure chevrons for tree controls."""

    def drawPrimitive(self, element, option, painter, widget=None) -> None:  # noqa: N802, ANN001
        if element != QStyle.PrimitiveElement.PE_IndicatorBranch:
            super().drawPrimitive(element, option, painter, widget)
            return

        if not option.state & QStyle.StateFlag.State_Children:
            return

        from yancuo_win.ui.theme import current_theme_name, theme_tokens

        tokens = theme_tokens(current_theme_name())
        rect = option.rect
        center_x = rect.center().x()
        center_y = rect.center().y()
        offset = 3
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(tokens.muted), 1.8))
        if option.state & QStyle.StateFlag.State_Open:
            painter.drawLine(center_x - offset, center_y - 1, center_x, center_y + 2)
            painter.drawLine(center_x, center_y + 2, center_x + offset, center_y - 1)
        else:
            painter.drawLine(center_x - 1, center_y - offset, center_x + 2, center_y)
            painter.drawLine(center_x + 2, center_y, center_x - 1, center_y + offset)
        painter.restore()


def apply_themed_tree_branches(tree: QAbstractItemView) -> QAbstractItemView:
    """Use a transparent disclosure area with the common chevron treatment."""

    # A null base style delegates to the application style without taking
    # ownership of it when this individual tree is destroyed.
    style = ThemedTreeBranchStyle()
    tree.setStyle(style)
    tree._yancuo_tree_branch_style = style  # type: ignore[attr-defined]
    return tree


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


class SoftItemDelegate(QStyledItemDelegate):
    """Paint restrained, inset hover and selection surfaces for item views."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        radius: float = 9.0,
        horizontal_margin: int = 4,
        vertical_margin: int = 2,
        minimum_height: int = 0,
    ) -> None:
        super().__init__(parent)
        self.radius = radius
        self.horizontal_margin = horizontal_margin
        self.vertical_margin = vertical_margin
        self.minimum_height = minimum_height

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        selected = bool(styled.state & QStyle.StateFlag.State_Selected)
        hovered = bool(styled.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(styled.state & QStyle.StateFlag.State_HasFocus)
        styled.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_HasFocus
        )

        if selected or hovered:
            from yancuo_win.ui.theme import current_theme_name, theme_tokens

            tokens = theme_tokens(current_theme_name())
            rect = option.rect.adjusted(
                self.horizontal_margin,
                self.vertical_margin,
                -self.horizontal_margin,
                -self.vertical_margin,
            )
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(
                QColor(tokens.list_selected if selected else tokens.list_hover)
            )
            painter.drawRoundedRect(rect, self.radius, self.radius)
            painter.restore()

        super().paint(painter, styled, index)

        if focused:
            from yancuo_win.ui.theme import current_theme_name, theme_tokens

            tokens = theme_tokens(current_theme_name())
            focus_rect = option.rect.adjusted(
                self.horizontal_margin,
                self.vertical_margin,
                -self.horizontal_margin,
                -self.vertical_margin,
            )
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(tokens.focus_ring), 1))
            painter.drawRoundedRect(focus_rect, self.radius, self.radius)
            painter.restore()

    def sizeHint(
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        size = super().sizeHint(option, index)
        if self.minimum_height:
            size.setHeight(max(size.height(), self.minimum_height))
        return size


class IconButton(QPushButton):
    """Small accessible icon action backed by the shared SVG icon service."""

    def __init__(
        self,
        icon: str | QStyle.StandardPixmap,
        tooltip: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("IconButton")
        if isinstance(icon, str):
            bind_icon(self, icon)
        else:
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
        search_action = QAction(symbolic_icon("search"), "搜索", self)
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


class StateNotice(QFrame):
    """Theme-aware inline loading, failure, disabled and permission state."""

    _accessible_names = {
        "info": "信息",
        "loading": "正在加载",
        "success": "操作成功",
        "error": "操作失败",
        "disabled": "功能不可用",
        "permission": "需要配置或权限",
    }

    def __init__(
        self,
        text: str = "",
        variant: str = "info",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StateNotice")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        self.label = QLabel()
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.set_state(text, variant)

    def set_state(self, text: str, variant: str = "info") -> None:
        if variant not in self._accessible_names:
            raise ValueError(f"unsupported state notice variant: {variant}")
        self.setProperty("state", variant)
        self.label.setText(text)
        self.setAccessibleName(self._accessible_names[variant])
        self.setAccessibleDescription(text)
        self.style().unpolish(self)
        self.style().polish(self)

    def setText(self, text: str) -> None:  # noqa: N802
        self.set_state(text, str(self.property("state") or "info"))

    def text(self) -> str:
        return self.label.text()

    def clear(self) -> None:
        self.label.clear()
        self.setAccessibleDescription("")


class LoadingSkeleton(QFrame):
    """Neutral loading placeholder; callers choose the amount of expected content."""

    def __init__(self, rows: int = 3, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LoadingSkeleton")
        self.setAccessibleName("正在加载")
        self.setAccessibleDescription("内容正在加载，请稍候")
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
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)
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
        self.retry_button = default_button("重新加载")
        self.retry_button.setAccessibleDescription("重新加载刚才失败的内容")
        self.retry_button.clicked.connect(self.retry_requested)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addWidget(self.retry_button, alignment=Qt.AlignmentFlag.AlignCenter)


class ToastMessage(QFrame):
    """Non-blocking confirmation for short, reversible success feedback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ToastMessage")
        self.setAccessibleName("操作反馈")
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
        self.setAccessibleDescription(message)
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


class OperationResultDialog(QDialog):
    """Accessible result surface for import, export, restore and sync operations."""

    RetryCode = 2

    def __init__(
        self,
        title: str,
        summary: str,
        *,
        details: str = "",
        is_error: bool = False,
        retry_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("OperationResultDialog")
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.setAccessibleDescription(summary)
        self.setModal(True)
        self.setMinimumSize(460, 260 if details else 200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        layout.addWidget(heading)
        self.summary_label = QLabel(summary)
        self.summary_label.setObjectName("ErrorLabel" if is_error else "MutedLabel")
        self.summary_label.setWordWrap(True)
        self.summary_label.setAccessibleName("操作结果摘要")
        layout.addWidget(self.summary_label)

        self.details_view = QTextEdit()
        self.details_view.setObjectName("DialogTextSurface")
        self.details_view.setReadOnly(True)
        self.details_view.setPlainText(details)
        self.details_view.setAccessibleName("操作结果详情")
        self.details_view.setAccessibleDescription("只读详情，可使用方向键浏览并复制")
        self.details_view.setVisible(bool(details))
        layout.addWidget(self.details_view, stretch=1)

        buttons = QDialogButtonBox()
        self.retry_button: QPushButton | None = None
        if retry_text:
            self.retry_button = buttons.addButton(
                retry_text,
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            self.retry_button.setAccessibleName(retry_text)
            self.retry_button.setAccessibleDescription("关闭结果窗口并重新执行刚才的操作")
            self.retry_button.clicked.connect(lambda: self.done(self.RetryCode))
        self.close_button = buttons.addButton(
            "关闭",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.close_button.setAccessibleName("关闭操作结果")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(buttons)
        if self.retry_button is not None:
            set_tab_order_chain(self.retry_button, self.close_button)
            self.retry_button.setFocus()
        else:
            self.close_button.setFocus()


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


class ReadingCanvas(QFrame):
    """Centered, width-limited sheet for long-form reading content."""

    def __init__(
        self,
        content: QWidget | None = None,
        parent: QWidget | None = None,
        *,
        maximum_width: int = 920,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ReadingCanvas")
        self.maximum_content_width = maximum_width
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.sheet = QFrame(self)
        self.sheet.setObjectName("ReadingCanvasSheet")
        self.sheet.setMinimumWidth(0)
        self.sheet.setMaximumWidth(maximum_width)
        self._content_layout = QVBoxLayout(self.sheet)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self.content: QWidget | None = None
        if content is not None:
            self.set_content(content)

    def set_content(self, content: QWidget) -> None:
        if self.content is not None:
            self._content_layout.removeWidget(self.content)
        self.content = content
        self._content_layout.addWidget(content)
        self._layout_sheet()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._layout_sheet()

    def _layout_sheet(self) -> None:
        inset = 12
        available_width = max(0, self.width() - inset * 2)
        sheet_width = min(self.maximum_content_width, available_width)
        x = max(inset, (self.width() - sheet_width) // 2)
        self.sheet.setGeometry(
            x,
            inset,
            sheet_width,
            max(0, self.height() - inset * 2),
        )


class WorkflowStepBar(QFrame):
    """Compact progress context for a short, linear desktop workflow."""

    def __init__(
        self,
        steps: tuple[str, ...],
        current_step: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("WorkflowStepBar")
        self.steps = steps
        self.labels: list[QLabel] = []
        self.connectors: list[QFrame] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        for index, text in enumerate(steps):
            label = QLabel(f"{index + 1}  {text}")
            label.setObjectName("WorkflowStep")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setAccessibleName(f"步骤 {index + 1}：{text}")
            label.setWordWrap(False)
            label.setMinimumWidth(
                max(72, label.fontMetrics().horizontalAdvance(label.text()) + 24)
            )
            label.setSizePolicy(
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Fixed,
            )
            layout.addWidget(label)
            self.labels.append(label)
            if index < len(steps) - 1:
                connector = QFrame()
                connector.setObjectName("WorkflowStepConnector")
                connector.setFixedHeight(1)
                connector.setMinimumWidth(8)
                connector.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Fixed,
                )
                layout.addWidget(connector, stretch=1)
                self.connectors.append(connector)
        self.set_current_step(current_step)

    def set_current_step(self, current_step: int) -> None:
        if not 0 <= current_step < len(self.steps):
            raise ValueError(f"invalid workflow step: {current_step}")
        self.current_step = current_step
        for index, label in enumerate(self.labels):
            state = (
                "completed"
                if index < current_step
                else "current"
                if index == current_step
                else "upcoming"
            )
            label.setProperty("state", state)
            label.style().unpolish(label)
            label.style().polish(label)
        for index, connector in enumerate(self.connectors):
            connector.setProperty(
                "state",
                "completed" if index < current_step else "upcoming",
            )
            connector.style().unpolish(connector)
            connector.style().polish(connector)


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
