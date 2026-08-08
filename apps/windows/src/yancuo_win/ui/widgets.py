"""可复用的轻量 UI 控件。"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QModelIndex,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QPainter, QPen, QPixmap, QWheelEvent
from shiboken6 import isValid as _shiboken_is_valid

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QProgressBar,
    QPushButton,
    QProxyStyle,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.ui.icons import bind_icon, symbolic_icon
from yancuo_win.ui.theme import current_theme_name, theme_tokens


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
            try:
                if not _shiboken_is_valid(first) or not _shiboken_is_valid(second):
                    return
            except RuntimeError:
                return
            if first.window() is second.window():
                QWidget.setTabOrder(first, second)

        attach_timer = QTimer(current)
        attach_timer.setSingleShot(True)
        attach_timer.timeout.connect(apply_when_attached)
        attach_timer.start(0)


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


def friendly_cloud_error(error: str) -> str:
    """Turn common CloudBase gateway errors into user-readable messages."""
    text = str(error or "")
    lowered = text.lower()
    if any(token in lowered for token in ("timed out", "timeout", "timeout exceeded")):
        return "连接云端超时，请检查网络后重试"
    if any(token in lowered for token in ("connection refused", "connection reset", "network is unreachable", "无法连接")):
        return "无法连接云端服务，请检查网络后重试"
    if any(token in lowered for token in ("getaddrinfo", "name or service not known", "dns")):
        return "无法解析云端服务地址，请检查网络/DNS 后重试"
    if "401" in text and "登录已失效" in text:
        return "登录已失效，请重新登录后重试"
    if "HTTP 4" in text or "HTTP 5" in text:
        marker = text.find(":")
        if marker != -1 and marker < 80:
            tail = text[marker + 1:].strip().strip('"')
            if tail and tail != "服务暂时不可用":
                return tail
    return text

def show_dropdown_menu(
    menu: "QMenu",
    anchor: QWidget,
    *,
    gap: int = 6,
    margin: int = 8,
    min_width: int | None = None,
    max_width: int = 320,
) -> None:
    """Open ``menu`` below ``anchor`` with a fixed gap and keep the anchor visible.

    The menu left edge aligns with the anchor left edge, shifts left near the
    right screen edge, and flips above the anchor when there is not enough
    space below.  The anchor widget is never covered by the popup.
    """
    from PySide6.QtGui import QGuiApplication

    menu.adjustSize()
    hint = menu.sizeHint()
    width = max(hint.width(), min_width or anchor.width())
    width = min(width, max_width)
    height = hint.height()
    anchor_global = anchor.mapToGlobal(QPoint(0, 0))
    anchor_rect = QRect(anchor_global, anchor.size())
    screen = (
        QGuiApplication.screenAt(anchor_rect.center())
        or QGuiApplication.primaryScreen()
    )
    available = screen.availableGeometry() if screen is not None else anchor_rect
    x = anchor_rect.left()
    if x + width > available.right() - margin:
        x = max(available.left() + margin, available.right() - margin - width)
    below_y = anchor_rect.bottom() + gap
    if below_y + height <= available.bottom() - margin:
        y = below_y
    else:
        y = max(available.top() + margin, anchor_rect.top() - gap - height)
    menu.setMinimumWidth(width)
    menu.popup(QPoint(x, y))

def action_combo_box(
    placeholder: str,
    actions: Sequence[tuple[str, Callable[[], None]] | None],
    parent: QWidget | None = None,
) -> QComboBox:
    """Create an action picker using the standard form-control dropdown style."""

    combo = QComboBox(parent)
    combo.setPlaceholderText(placeholder)
    combo.setCurrentIndex(-1)
    for action in actions:
        if action is None:
            combo.insertSeparator(combo.count())
            continue
        label, callback = action
        combo.addItem(label, callback)

    def trigger(index: int) -> None:
        callback = combo.itemData(index)
        if not callable(callback):
            return
        combo.setCurrentIndex(-1)
        callback()

    combo.activated.connect(trigger)
    return combo


class ChevronComboBox(QComboBox):
    """Combo box with an explicit chevron that remains visible when editable."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("visibleChevron", True)

    def paintEvent(self, event) -> None:  # noqa: N802, ANN001
        super().paintEvent(event)

        from yancuo_win.ui.theme import current_theme_name, theme_tokens

        tokens = theme_tokens(current_theme_name())
        center_x = 17 if self.layoutDirection() == Qt.LayoutDirection.RightToLeft else self.width() - 17
        center_y = self.height() // 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(tokens.primary if self.view().isVisible() else tokens.muted), 1.8))
        if self.view().isVisible():
            # 打开状态：箭头旋转 180°（向上）并使用主色
            painter.drawLine(center_x - 4, center_y + 2, center_x, center_y - 2)
            painter.drawLine(center_x, center_y - 2, center_x + 4, center_y + 2)
        else:
            painter.drawLine(center_x - 4, center_y - 2, center_x, center_y + 2)
            painter.drawLine(center_x, center_y + 2, center_x + 4, center_y - 2)


class ScrollSafeSpinBox(QSpinBox):
    """Integer input whose wheel gesture remains available to its scroll area."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()


class ScrollSafeDoubleSpinBox(QDoubleSpinBox):
    """Decimal input whose wheel gesture remains available to its scroll area."""

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        event.ignore()


class FineScrollListWidget(QListWidget):
    """QListWidget whose mouse wheel scrolls by a small fixed step.

    Stock item views jump several rows per wheel notch, which makes long
    lists hard to locate.  This subclass scrolls one short row-height per
    notch and scales trackpad pixel deltas down for finer control.
    """

    _WHEEL_STEP_PX = 72

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Scroll in pixels, not items: the default ScrollPerItem mode would
        # treat our pixel step as a row count and jump to the bottom in one
        # wheel notch.
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        pixel = event.pixelDelta().y()
        if pixel:
            step = max(1, round(abs(pixel) * 0.6))
            direction = 1 if pixel > 0 else -1
        else:
            angle = event.angleDelta().y()
            if not angle:
                super().wheelEvent(event)
                return
            direction = 1 if angle > 0 else -1
            step = self._WHEEL_STEP_PX
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - direction * step)
        event.accept()


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


def _status_icon_pixmap(color: str, kind: str, size: int = 20) -> QPixmap:
    """Render a colored status icon: error exclamation, success check or info dot."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size, size)
    painter.setPen(
        QPen(
            QColor("#FFFFFF"),
            max(1.8, size / 9),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    center = size / 2
    if kind == "error":
        painter.drawLine(QPointF(center, size * 0.30), QPointF(center, size * 0.58))
        painter.drawPoint(QPointF(center, size * 0.72))
    elif kind == "success":
        painter.drawPolyline(
            [
                QPointF(size * 0.24, size * 0.52),
                QPointF(size * 0.42, size * 0.68),
                QPointF(size * 0.76, size * 0.34),
            ]
        )
    else:  # info
        painter.drawPoint(QPointF(center, size * 0.34))
        painter.drawLine(QPointF(center, size * 0.48), QPointF(center, size * 0.70))
    painter.end()
    return pixmap


def _error_icon_pixmap(size: int = 20) -> QPixmap:
    """Backward-compatible red exclamation icon."""
    return _status_icon_pixmap("#F54A45", "error", size)


_TOAST_WARNING_MARKERS = (
    "失败",
    "错误",
    "出错",
    "无法",
    "不存在",
    "请先",
    "请选择",
    "请输入",
    "没有可",
    "没有其他",
    "尚无",
    "不能",
    "不可用",
    "无效",
    "未找到",
    "未生成",
    "不允许",
    "至少",
    "必须",
)

_TOAST_SUCCESS_MARKERS = (
    "成功",
    "已保存",
    "已应用",
    "已加入",
    "已更新",
    "已恢复",
    "已删除",
    "已清空",
    "已提交",
    "已入库",
    "已采用",
    "已撤回",
    "已保留",
    "已重建",
    "已创建",
    "已修改",
    "已替换",
    "已合并",
    "已上传",
    "已下载",
    "已同步",
    "已备份",
    "已移动",
    "已记录",
    "已重置",
    "已设置",
)


def _classify_toast_tone(message: str) -> str:
    """Pick an accent tone from message content for generic toast feedback."""
    text = message or ""
    if any(marker in text for marker in _TOAST_WARNING_MARKERS):
        return "warning"
    if any(marker in text for marker in _TOAST_SUCCESS_MARKERS) or text.startswith("已"):
        return "success"
    return "info"


class AppToast(QFrame):
    """A single toast card rendered by :class:`ToastStack`.

    White card with a tone accent (error/warning red, success green,
    info blue), matching icon, close button, countdown progress bar, and
    slide/fade animations.  Positioning and stacking are handled by the
    owning :class:`ToastStack`.
    """

    def __init__(
        self,
        stack: "ToastStack",
        *,
        title: str,
        body: str,
        tone: str = "info",
        duration_ms: int = 3500,
    ) -> None:
        parent = stack.parent()
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self._stack = stack
        self._duration_ms = max(600, duration_ms)
        self._remaining_ms = self._duration_ms
        self._paused = False
        self._dismissing = False
        self.setObjectName("AppToastShell")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(340)
        self.setMinimumHeight(68)
        self.setVisible(False)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("AppToastCard")
        self.card.setProperty("tone", tone)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        row = QHBoxLayout()
        row.setContentsMargins(14, 12, 8, 10)
        row.setSpacing(10)
        tokens = theme_tokens(current_theme_name())
        icon_kind = (
            "error"
            if tone in {"error", "warning"}
            else "success"
            if tone == "success"
            else "info"
        )
        icon_color = (
            tokens.danger
            if tone in {"error", "warning"}
            else tokens.success
            if tone == "success"
            else tokens.primary
        )
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setPixmap(_status_icon_pixmap(icon_color, icon_kind, 20))
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("AppToastTitle")
        if not title:
            self.title_label.setVisible(False)
        self.body_label = QLabel(body)
        self.body_label.setObjectName("AppToastBody")
        self.body_label.setWordWrap(True)
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.body_label)
        row.addLayout(text_column, 1)

        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("AppToastClose")
        self.close_button.setFixedSize(22, 22)
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.setToolTip("关闭")
        self.close_button.clicked.connect(self.dismiss)
        row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(row)

        self.progress = QProgressBar()
        self.progress.setObjectName("AppToastProgress")
        self.progress.setProperty("tone", tone)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, self._duration_ms)
        self.progress.setValue(self._duration_ms)
        self.progress.setInvertedAppearance(True)
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(8, 0, 8, 0)
        progress_row.setSpacing(0)
        progress_row.addWidget(self.progress)
        card_layout.addLayout(progress_row)

        # 单层 QGraphicsOpacityEffect 即可；嵌套 shadow 会触发
        # "one painter at a time" / Painter not active 冲突

        outer.addWidget(self.card)

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(240)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(240)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        # When a slide ends, snap to the exact target.  QPropertyAnimation
        # interpolates QPoint through floats and rounds to pixels, so on slow or
        # virtualized CI runners the final frame can land one pixel off the
        # computed anchor; snapping keeps the card at the precise top-right spot.
        self._slide.finished.connect(self._snap_to_slide_target)

    def _snap_to_slide_target(self) -> None:
        end = self._slide.endValue()
        if isinstance(end, QPoint) and self.pos() != end:
            self.move(end)

    def start_at(self, target: QPoint) -> None:
        """Position off-screen, then slide in and fade in from the right."""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        start = QPoint(parent.width() + 24, target.y())
        self.move(start)
        self.raise_()
        self.show()
        self._opacity.setOpacity(0.0)
        self._slide.stop()
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(target)
        self._slide.start()
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start()

    def _advance(self) -> None:
        if self._paused or self._dismissing:
            return
        self._remaining_ms = max(0, self._remaining_ms - self._timer.interval())
        self.progress.setValue(self._remaining_ms)
        if self._remaining_ms == 0:
            self.dismiss()

    def dismiss(self) -> None:
        """Slide out to the right and fade, then hand back to the stack."""
        if self._dismissing:
            return
        self._dismissing = True
        self._timer.stop()
        self._slide.stop()
        self._fade.stop()
        parent = self.parentWidget()
        target = QPoint(parent.width() + 24, self.y()) if parent is not None else self.pos()
        self._slide.setDuration(200)
        self._slide.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(target)
        self._fade.setDuration(200)
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._slide.finished.connect(self._on_dismissed)
        self._fade.finished.connect(self._on_dismissed)
        self._slide.start()
        self._fade.start()

    def _on_dismissed(self) -> None:
        self._slide.finished.disconnect(self._on_dismissed)
        self._fade.finished.disconnect(self._on_dismissed)
        self._stack._remove(self)

    def enterEvent(self, event) -> None:  # noqa: ANN001, N802
        self._paused = True
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001, N802
        self._paused = False
        super().leaveEvent(event)


class ToastStack(QObject):
    """Top-right stacked toast manager anchored to a parent window.

    New toasts appear below existing ones with a fixed gap; the stack
    reflows remaining toasts when one is dismissed.  The stack keeps the
    old ``show_message`` API for generic feedback and adds
    ``show_error`` for the red title/body style.
    """

    _TOP = 80
    _RIGHT = 24
    _GAP = 12
    _WIDTH = 340
    _MAX_VISIBLE = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._toasts: list[AppToast] = []

    def show_message(
        self,
        message: str,
        duration_ms: int = 2800,
        on_activated: Callable[[], None] | None = None,
        *,
        tone: str = "auto",
    ) -> None:
        del on_activated
        if tone in {"", "auto", "default"}:
            tone = _classify_toast_tone(message)
        self._push(title="", body=message, tone=tone, duration_ms=duration_ms)

    def show_error(self, title: str, message: str, duration_ms: int = 3500) -> None:
        self._push(title=title, body=message, tone="error", duration_ms=duration_ms)

    def _push(
        self,
        *,
        title: str,
        body: str,
        tone: str,
        duration_ms: int,
    ) -> None:
        parent = self.parent()
        if parent is None:
            return
        if len(self._toasts) >= self._MAX_VISIBLE:
            oldest = self._toasts.pop(0)
            oldest.dismiss()
        toast = AppToast(
            self,
            title=title,
            body=body,
            tone=tone,
            duration_ms=duration_ms,
        )
        y = self._TOP
        for existing in self._toasts:
            y += existing.height() + self._GAP
        target = QPoint(max(12, parent.width() - self._WIDTH - self._RIGHT), y)
        self._toasts.append(toast)
        toast.start_at(target)

    def _remove(self, toast: AppToast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._relayout()
        toast.deleteLater()

    def _relayout(self) -> None:
        parent = self.parent()
        if parent is None:
            return
        y = self._TOP
        for toast in self._toasts:
            toast._slide.stop()
            toast.move(max(12, parent.width() - self._WIDTH - self._RIGHT), y)
            y += toast.height() + self._GAP

class CompletionNotification(QFrame):
    """Queued, clickable AI completion notice with a testable countdown."""

    activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CompletionNotification")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setVisible(False)
        self._job_id = ""
        self._queue: list[tuple[str, int]] = []
        self._duration_ms = 8000
        self._remaining_ms = self._duration_ms
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(6)
        self.title = QLabel("AI 识别完成")
        self.title.setObjectName("CompletionNotificationTitle")
        self.summary = QLabel()
        self.summary.setObjectName("MutedLabel")
        self.summary.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setObjectName("CompletionNotificationProgress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, self._duration_ms)
        layout.addWidget(self.title)
        layout.addWidget(self.summary)
        layout.addWidget(self.progress)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._advance)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(180)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)

    def enqueue(self, job_id: str, candidates: int, duration_ms: int) -> None:
        self._queue.append((job_id, candidates))
        self._duration_ms = max(1000, duration_ms)
        # ``isVisible`` also depends on the parent window.  The hidden state
        # alone keeps completed batches queued while the app is minimized.
        if self.isHidden():
            self._show_next()

    def _show_next(self) -> None:
        if not self._queue:
            self.hide()
            return
        self._job_id, candidates = self._queue.pop(0)
        self._remaining_ms = self._duration_ms
        self.summary.setText(f"已生成 {candidates} 道待确认题目，点击继续审核")
        self.setAccessibleName("AI 识别完成，点击继续审核")
        self.setAccessibleDescription(self.summary.text())
        self.progress.setRange(0, self._duration_ms)
        self.progress.setValue(self._remaining_ms)
        self.adjustSize()
        parent = self.parentWidget()
        if parent is None:
            return
        target_x = max(12, parent.width() - self.width() - 20)
        target_y = 64
        self.move(parent.width() + 8, target_y)
        self.show()
        self.raise_()
        self._slide.stop()
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(QPoint(target_x, target_y))
        self._slide.start()
        self._timer.start()

    def _advance(self) -> None:
        self._remaining_ms = max(0, self._remaining_ms - self._timer.interval())
        self.progress.setValue(self._remaining_ms)
        self.progress.setAccessibleDescription(f"通知将在 {max(0, (self._remaining_ms + 999) // 1000)} 秒后关闭")
        if self._remaining_ms == 0:
            self._timer.stop()
            self.hide()
            QTimer.singleShot(0, self._show_next)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._activate()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate()
            event.accept()
            return
        super().keyPressEvent(event)

    def _activate(self) -> None:
        job_id = self._job_id
        self._timer.stop()
        self.hide()
        if job_id:
            self.activated.emit(job_id)
        QTimer.singleShot(0, self._show_next)


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
        self.description.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        if not description:
            self.description.hide()
        labels.addWidget(self.description)
        layout.addLayout(labels, stretch=1)
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


def button_row(*buttons: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    for btn in buttons:
        row.addWidget(btn)
    row.addStretch(1)
    return row
