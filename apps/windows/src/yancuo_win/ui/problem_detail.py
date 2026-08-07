"""Dedicated problem reading page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QScrollArea,
    QTextEdit,
    QBoxLayout,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.problem_chat_service import ProblemChatService, ProblemReference
from yancuo_win.data.models import Problem
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.ai_coordinator import AIJobCoordinator
from yancuo_win.ui.image_viewer import ImageViewerDialog
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import (
    CardFrame,
    IconButton,
    PageHeader,
    describe_field,
    ghost_button,
    primary_button,
    set_tab_order_chain,
    show_dropdown_menu,
)
from yancuo_win.ui.icons import bind_icon


class _DetailImage(QLabel):
    def __init__(self, parent=None) -> None:
        super().__init__("暂无原始图片", parent)
        self._source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(280, 300))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("双击放大查看原始图片")
        self.setObjectName("ImagePreview")

    def set_path(self, path: Path | None) -> bool:
        self._source = QPixmap(str(path)) if path and path.is_file() else QPixmap()
        self._render()
        return not self._source.isNull()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._render()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._source.isNull():
            ImageViewerDialog(self._source, self).exec()
        super().mouseDoubleClickEvent(event)

    def _render(self) -> None:
        if self._source.isNull():
            self.setPixmap(QPixmap())
            self.setText("暂无原始图片")
            return
        self.setText("")
        self.setPixmap(
            self._source.scaled(
                self.size() - QSize(24, 24),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class _ReferenceCanvas(QWidget):
    """Image-backed multi-selection canvas that persists normalized coordinates."""

    changed = Signal()
    selection_finished = Signal()
    exit_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._display_size = QSize()
        self._asset_id = ""
        self._page_index = 0
        self._regions: list[ProblemReference] = []
        self._drawing = False
        self._selection_enabled = False
        self._selected_index = -1
        self._hover_index = -1
        self._interaction = ""
        self._interaction_origin = QPoint()
        self._interaction_reference: ProblemReference | None = None
        self._start = QPoint()
        self._draft = QRect()
        self.setMinimumHeight(160)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setVisible(False)

    def set_source(
        self,
        asset_id: str,
        page_index: int,
        path: Path | None,
        fit_width: int | None = None,
    ) -> None:
        self._asset_id, self._page_index = asset_id, page_index
        self._pixmap = QPixmap(str(path)) if path else QPixmap()
        self._display_size = QSize()
        if fit_width and not self._pixmap.isNull():
            self._display_size = self._pixmap.size().scaled(
                QSize(fit_width, 100_000),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            self.setFixedSize(self._display_size)
        else:
            self.setMinimumSize(0, 160)
            self.setMaximumSize(16_777_215, 16_777_215)
        self._selected_index = next(
            (index for index, value in enumerate(self._regions) if value.asset_id == asset_id),
            -1,
        )
        self._selection_enabled = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def references(self) -> list[ProblemReference]:
        return list(self._regions)

    def clear(self) -> None:
        self._regions.clear()
        self._selected_index = -1
        self._draft = QRect()
        self.changed.emit()
        self.update()

    def add_normalized_region(self, x: float, y: float, width: float, height: float) -> None:
        if not self._asset_id:
            return
        self._regions.append(
            ProblemReference(self._asset_id, self._page_index, x, y, width, height)
        )
        self._selected_index = len(self._regions) - 1
        self.changed.emit()
        self.update()

    def begin_selection(self) -> None:
        self._selection_enabled = not self._pixmap.isNull()
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._selection_enabled else Qt.CursorShape.ArrowCursor
        )
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def cancel_selection(self) -> None:
        self._selection_enabled = False
        self._drawing = False
        self._draft = QRect()
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def delete_selected(self) -> None:
        if 0 <= self._selected_index < len(self._regions):
            self._regions.pop(self._selected_index)
            self._selected_index = min(self._selected_index, len(self._regions) - 1)
            self.changed.emit()
            self.update()

    def select_reference(self, index: int) -> None:
        if 0 <= index < len(self._regions):
            self._selected_index = index
            self.update()

    def _image_rect(self) -> QRect:
        if self._pixmap.isNull():
            return QRect()
        if not self._display_size.isEmpty():
            size = self._display_size
        else:
            size = self._pixmap.size().scaled(
                self.size() - QSize(12, 12), Qt.AspectRatioMode.KeepAspectRatio
            )
        return QRect(
            (self.width() - size.width()) // 2,
            (self.height() - size.height()) // 2,
            size.width(),
            size.height(),
        )

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        del event
        painter = QPainter(self)
        rect = self._image_rect()
        if rect.isEmpty():
            return
        painter.drawPixmap(rect, self._pixmap)
        for index, reference in enumerate(self._regions):
            if reference.asset_id != self._asset_id:
                continue
            region = self._region_rect(reference)
            active = index == self._selected_index
            hovered = index == self._hover_index
            painter.setPen(QPen(QColor("#1858D8" if active else "#3370FF"), 3 if active else 2))
            painter.setBrush(QColor(51, 112, 255, 70 if hovered or active else 38))
            painter.drawRect(region)
            painter.drawText(region.topLeft() + QPoint(4, 15), str(index + 1))
            if active:
                painter.fillRect(
                    QRect(region.right() - 5, region.bottom() - 5, 10, 10), QColor("#1858D8")
                )
        if not self._draft.isEmpty():
            painter.drawRect(self._draft.normalized())

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if (
            self._selection_enabled
            and event.button() == Qt.MouseButton.LeftButton
            and self._image_rect().contains(event.position().toPoint())
        ):
            self._drawing, self._start = True, event.position().toPoint()
            self._draft = QRect(self._start, self._start)
            self._interaction = "draw"
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            index = self._hit_test(point)
            self._selected_index = index
            if index >= 0:
                region = self._region_rect(self._regions[index])
                self._interaction = (
                    "resize" if (point - region.bottomRight()).manhattanLength() <= 16 else "move"
                )
                self._interaction_origin = point
                self._interaction_reference = self._regions[index]
            else:
                self._interaction = ""
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drawing:
            self._draft = QRect(self._start, event.position().toPoint()).intersected(
                self._image_rect()
            )
            self.update()
            return
        if self._selection_enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        point = event.position().toPoint()
        if self._interaction in {"move", "resize"} and self._interaction_reference is not None:
            self._update_interaction(point)
            return
        self._hover_index = self._hit_test(point)
        self.setCursor(
            Qt.CursorShape.OpenHandCursor if self._hover_index >= 0 else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._drawing or event.button() != Qt.MouseButton.LeftButton:
            if event.button() == Qt.MouseButton.LeftButton and self._interaction:
                self._interaction = ""
                self._interaction_reference = None
                self.changed.emit()
                event.accept()
            return
        self._drawing = False
        self._selection_enabled = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        rect, image = self._draft.normalized().intersected(self._image_rect()), self._image_rect()
        self._draft = QRect()
        if rect.width() >= 8 and rect.height() >= 8:
            self._regions.append(
                ProblemReference(
                    self._asset_id,
                    self._page_index,
                    (rect.x() - image.x()) / image.width(),
                    (rect.y() - image.y()) / image.height(),
                    rect.width() / image.width(),
                    rect.height() / image.height(),
                )
            )
            self._selected_index = len(self._regions) - 1
            self.changed.emit()
        self.selection_finished.emit()
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() == Qt.Key.Key_Escape:
            if self._drawing or self._selection_enabled:
                self.cancel_selection()
                self.selection_finished.emit()
            else:
                self.exit_requested.emit()
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self.delete_selected()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Tab and self._regions:
            step = -1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self._selected_index = (self._selected_index + step) % len(self._regions)
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def _region_rect(self, reference: ProblemReference) -> QRect:
        image = self._image_rect()
        return QRect(
            round(image.x() + image.width() * reference.x),
            round(image.y() + image.height() * reference.y),
            max(1, round(image.width() * reference.width)),
            max(1, round(image.height() * reference.height)),
        )

    def _hit_test(self, point: QPoint) -> int:
        for index in range(len(self._regions) - 1, -1, -1):
            reference = self._regions[index]
            if reference.asset_id == self._asset_id and self._region_rect(reference).contains(
                point
            ):
                return index
        return -1

    def _update_interaction(self, point: QPoint) -> None:
        image = self._image_rect()
        original = self._interaction_reference
        if image.isEmpty() or original is None or self._selected_index < 0:
            return
        dx = (point.x() - self._interaction_origin.x()) / image.width()
        dy = (point.y() - self._interaction_origin.y()) / image.height()
        if self._interaction == "move":
            x = min(max(0.0, original.x + dx), 1.0 - original.width)
            y = min(max(0.0, original.y + dy), 1.0 - original.height)
            updated = ProblemReference(
                original.asset_id, original.page_index, x, y, original.width, original.height
            )
        else:
            width = min(max(8 / image.width(), original.width + dx), 1.0 - original.x)
            height = min(max(8 / image.height(), original.height + dy), 1.0 - original.y)
            updated = ProblemReference(
                original.asset_id, original.page_index, original.x, original.y, width, height
            )
        self._regions[self._selected_index] = updated
        self.changed.emit()
        self.update()


class _ChatBubble(QFrame):
    """One chat message: user bubbles align right, assistant content on the left."""

    def __init__(self, role: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.role = role
        self.setObjectName("ChatBubbleUser" if role == "user" else "ChatBubbleAssistant")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(0)
        if role == "user":
            self.label = QLabel(text)
            self.label.setObjectName("ChatBubbleUserText")
            self.label.setWordWrap(True)
            self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self.label)
            self.view = None
        else:
            self.view = MathContentView()
            self.view.set_compact(True)
            self.view.set_adaptive_content_height(1200, reserve_height=True)
            layout.addWidget(self.view)
            self.label = None
            self.set_markdown(text)

    def set_markdown(self, text: str) -> None:
        if self.view is not None:
            self.view.set_fragment("", text)

    def set_plain(self, text: str) -> None:
        if self.label is not None:
            self.label.setText(text)


class _ChatFlow(QScrollArea):
    """Vertical message stream: only vertical scrolling, hidden scrollbars."""

    follow_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setContentsMargins(12, 8, 12, 8)
        self._layout.setSpacing(16)
        self._layout.addStretch(1)
        self.setWidget(self._container)
        self._follow = True
        self.verticalScrollBar().rangeChanged.connect(self._on_range_changed)
        self.verticalScrollBar().valueChanged.connect(self._on_value_changed)

    def add_message(self, role: str, text: str) -> _ChatBubble:
        bubble = _ChatBubble(role, text)
        if role == "user":
            bubble.setMaximumWidth(480)
            self._layout.insertWidget(
                self._layout.count() - 1,
                bubble,
                0,
                Qt.AlignmentFlag.AlignRight,
            )
        else:
            self._layout.insertWidget(self._layout.count() - 1, bubble)
        self._scroll_to_bottom()
        return bubble

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._follow = True

    def scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._follow = True

    def _scroll_to_bottom(self) -> None:
        if self._follow:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _on_range_changed(self, _minimum: int, _maximum: int) -> None:
        if self._follow:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _on_value_changed(self, value: int) -> None:
        bar = self.verticalScrollBar()
        at_bottom = bar.maximum() - value <= 8
        if self._follow != at_bottom:
            self._follow = at_bottom
            self.follow_changed.emit(at_bottom)


class _ChatInputEdit(QTextEdit):
    """Multi-line chat input: Enter submits, Shift+Enter inserts a newline."""

    submit_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAccessibleName("AI 讨论问题")
        self.setPlaceholderText("向当前题目提问（Enter 发送，Shift+Enter 换行）")
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().contentsChanged.connect(self._resize_to_content)
        self._max_lines = 6
        self.setFixedHeight(40)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        # IME composition consumes Enter before it reaches here, so this only
        # fires for a real submit key; Shift+Enter inserts a newline instead.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.submit_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def _resize_to_content(self) -> None:
        doc = self.document()
        doc.setTextWidth(max(1, self.viewport().width()))
        line_height = self.fontMetrics().lineSpacing() + 4
        max_height = line_height * self._max_lines + 10
        height = min(max_height, max(40, int(doc.size().height()) + 12))
        if self.height() != height:
            self.setFixedHeight(height)

class ProblemDetailPage(QWidget):
    """A distraction-free reader shown inside the app's persistent shell."""

    back_requested = Signal()
    edit_requested = Signal(str)
    previous_requested = Signal()
    next_requested = Signal()
    schedule_review_requested = Signal(str)
    favorite_requested = Signal(str, bool)
    archive_requested = Signal(str)
    trash_requested = Signal(str)
    restore_requested = Signal(str)
    chat_requested = Signal(str)
    status_message = Signal(str)

    @staticmethod
    def _toolbar_group() -> QWidget:
        group = QWidget()
        layout = QHBoxLayout(group)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        return group

    @staticmethod
    def _toolbar_divider() -> QFrame:
        divider = QFrame()
        divider.setObjectName("ToolbarDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        divider.setFixedWidth(1)
        return divider

    @staticmethod
    def _clear_layout(layout: QBoxLayout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _update_toolbar_layout(self, *, force: bool = False) -> None:
        compact = self.width() < 980
        if not force and compact == self._toolbar_compact:
            return
        self._toolbar_compact = compact
        self._clear_layout(self.toolbar_actions)
        self._clear_layout(self._toolbar_priority_layout)
        if compact:
            self.toolbar_actions.setDirection(QBoxLayout.Direction.TopToBottom)
            self._toolbar_priority_layout.addWidget(self.switch_group)
            self._toolbar_priority_layout.addStretch(1)
            self._toolbar_priority_layout.addWidget(self.management_group)
            self.toolbar_actions.addWidget(self._toolbar_priority_row)
            self.toolbar_actions.addWidget(self.learning_group)
            for divider in self._toolbar_dividers:
                divider.setVisible(False)
        else:
            self.toolbar_actions.setDirection(QBoxLayout.Direction.LeftToRight)
            self.toolbar_actions.addWidget(self.switch_group)
            self.toolbar_actions.addWidget(self._toolbar_dividers[0])
            self.toolbar_actions.addWidget(self.learning_group)
            self.toolbar_actions.addStretch(1)
            self.toolbar_actions.addWidget(self._toolbar_dividers[1])
            self.toolbar_actions.addWidget(self.management_group)
            for divider in self._toolbar_dividers:
                divider.setVisible(True)

    def __init__(
        self,
        chat: ProblemChatService | None = None,
        coordinator: AIJobCoordinator | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.chat = chat
        self.ai_coordinator = coordinator
        self.problem_id: str | None = None
        self._chat_job_id: str | None = None
        self._streaming_bubble: _ChatBubble | None = None
        self._streaming_text = ""
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(120)
        self._stream_timer.timeout.connect(self._flush_stream_bubble)
        self._conversation_by_problem: dict[str, str] = {}
        if coordinator is not None:
            coordinator.register_handler("problem_chat", self._run_problem_chat_job)
            coordinator.job_finished.connect(self._on_problem_chat_job_finished)
            coordinator.job_failed.connect(self._on_problem_chat_job_failed)
            coordinator.job_progress.connect(self._on_problem_chat_progress)
        self._reader_scroll_by_problem: dict[str, int] = {}
        self._pending_reader_scroll = 0
        self._focus_before_chat: QWidget | None = None
        self._reference_sources: list[dict[str, Any]] = []
        self.setObjectName("PageRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 12, 24, 20)
        root.setSpacing(10)

        self.back_button = IconButton("chevron-left", "返回题库")
        self.back_button.clicked.connect(self.back_requested.emit)
        describe_field(self.back_button, "返回题库", "返回上一页，快捷键 Alt+Left")
        self.back_button.setToolTip("返回题库 (Alt+Left)")
        self.edit_button = primary_button("编辑题目")
        self.edit_button.clicked.connect(self._request_edit)
        self.chat_button = QPushButton("AI 讨论")
        self.chat_button.clicked.connect(self._request_chat)
        self.chat_button.setEnabled(chat is not None)
        self.header = PageHeader("题目详情")
        self.title_label = self.header.title
        self.meta_label = self.header.description
        self.meta_label.hide()
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.add_leading(self.back_button)
        root.addWidget(self.header)

        self.action_toolbar = QFrame()
        self.action_toolbar.setObjectName("ContextBar")
        self.action_toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # The reader scrolls below this toolbar, so these controls stay available
        # while navigating a long problem.
        self.toolbar_actions = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.action_toolbar)
        self.toolbar_actions.setContentsMargins(10, 8, 10, 8)
        self.toolbar_actions.setSpacing(16)

        self.switch_group = self._toolbar_group()
        self.previous_button = QPushButton("上一题")
        bind_icon(self.previous_button, "chevron-left")
        self.previous_button.clicked.connect(self.previous_requested.emit)
        self.next_button = QPushButton("下一题")
        bind_icon(self.next_button, "chevron-right")
        self.next_button.clicked.connect(self.next_requested.emit)
        self.switch_group.layout().addWidget(self.previous_button)
        self.switch_group.layout().addWidget(self.next_button)

        self.learning_group = self._toolbar_group()
        self.learning_group.layout().addWidget(self.chat_button)
        self.review_button = QPushButton("加入复习计划")
        self.review_button.clicked.connect(self._request_review)
        self.favorite_button = QPushButton("收藏")
        self.favorite_button.clicked.connect(self._request_favorite)
        self.learning_group.layout().addWidget(self.review_button)
        self.learning_group.layout().addWidget(self.favorite_button)

        self.management_group = self._toolbar_group()
        self.more_button = QPushButton("更多")
        bind_icon(self.more_button, "more-horizontal")
        self.more_menu = QMenu(self.more_button)
        self.archive_action = self.more_menu.addAction("归档")
        self.archive_action.triggered.connect(self._request_archive)
        self.trash_action = self.more_menu.addAction("移入回收站")
        self.trash_action.setProperty("danger", True)
        self.trash_action.triggered.connect(self._request_trash)
        self.restore_action = self.more_menu.addAction("恢复到正式题库")
        self.restore_action.triggered.connect(self._request_restore)
        self.more_button.clicked.connect(self._show_more_menu)
        self.management_group.layout().addWidget(self.edit_button)
        self.management_group.layout().addWidget(self.more_button)

        self._toolbar_dividers = (self._toolbar_divider(), self._toolbar_divider())
        self._toolbar_priority_row = QWidget()
        self._toolbar_priority_layout = QHBoxLayout(self._toolbar_priority_row)
        self._toolbar_priority_layout.setContentsMargins(0, 0, 0, 0)
        self._toolbar_priority_layout.setSpacing(16)
        self._toolbar_compact: bool | None = None
        self._update_toolbar_layout(force=True)
        root.addWidget(self.action_toolbar)

        self.reader = MathContentView()
        # The reader owns the remaining page height.  A content-height-fixed
        # reader changes its parent layout when its first async render arrives,
        # which makes the header and toolbar visibly jump on first open.
        self.reader.set_fit_content_height(expand_widget=False)
        self.reader.set_accessible_content(
            "题目正文与解析",
            "只读题目内容；可使用方向键或翻页键浏览长内容",
        )
        self.reader.setMinimumWidth(0)
        if hasattr(self.reader, "render_completed"):
            self.reader.render_completed.connect(self._restore_reader_scroll)
            self.reader.render_completed.connect(self._on_reader_rendered)
        self.reference_canvas = _ReferenceCanvas()
        self.reference_canvas.changed.connect(self._update_reference_summary)
        self.reference_canvas.selection_finished.connect(self._reference_selection_finished)
        self.reference_canvas.exit_requested.connect(self._finish_reference_mode)
        self.reader_stack = QStackedWidget()
        self.reader_stack.addWidget(self.reader)
        self.reference_scroll = QScrollArea()
        self.reference_scroll.setWidgetResizable(False)
        self.reference_scroll.setWidget(self.reference_canvas)
        self.reader_stack.addWidget(self.reference_scroll)
        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace.setChildrenCollapsible(False)
        self.workspace.setHandleWidth(10)
        self.workspace.addWidget(self.reader_stack)
        root.addWidget(self.workspace, stretch=1)

        self.chat_card = CardFrame()
        self.chat_card.setMinimumWidth(360)
        self.chat_card.add_title("AI 讨论")

        # 紧凑标题栏：左侧标题，右侧新建/更多图标
        chat_header = QHBoxLayout()
        chat_header.setSpacing(8)
        chat_header.addWidget(QLabel("AI 讨论"))
        chat_header.addStretch(1)
        self._new_chat_button = QPushButton()
        bind_icon(self._new_chat_button, "plus", size=18)
        self._new_chat_button.setObjectName("IconButton")
        self._new_chat_button.setFixedSize(34, 34)
        self._new_chat_button.setToolTip("新建对话")
        self._new_chat_button.setAccessibleName("新建对话")
        self._new_chat_button.clicked.connect(self._new_conversation)
        self._more_button = QPushButton()
        bind_icon(self._more_button, "more-horizontal", size=18)
        self._more_button.setObjectName("IconButton")
        self._more_button.setFixedSize(34, 34)
        self._more_button.setToolTip("更多操作")
        self._more_button.setAccessibleName("更多操作")
        self._more_button.clicked.connect(self._show_chat_more_menu)
        chat_header.addWidget(self._new_chat_button)
        chat_header.addWidget(self._more_button)
        self.chat_card.body.addLayout(chat_header)

        # 会话选择
        self.conversation_combo = QComboBox()
        describe_field(self.conversation_combo, "AI 讨论会话", "选择已保存的题目讨论")
        self.conversation_combo.currentIndexChanged.connect(self._conversation_changed)
        self.chat_card.body.addWidget(self.conversation_combo)

        # 消息流（只纵向滚动、隐藏滚动条）
        self._chat_flow = _ChatFlow()
        self._chat_flow.follow_changed.connect(self._on_chat_follow_changed)
        self.chat_card.body.addWidget(self._chat_flow, stretch=1)
        self._back_to_latest_button = ghost_button("回到最新消息")
        self._back_to_latest_button.clicked.connect(self._chat_flow.scroll_to_bottom)
        self._back_to_latest_button.hide()
        back_row = QHBoxLayout()
        back_row.addStretch(1)
        back_row.addWidget(self._back_to_latest_button)
        self.chat_card.body.addLayout(back_row)

        # 题目引用上下文卡（紧凑可折叠）
        self._reference_card = QFrame()
        self._reference_card.setObjectName("ChatReferenceCard")
        ref_card_layout = QVBoxLayout(self._reference_card)
        ref_card_layout.setContentsMargins(10, 8, 10, 8)
        ref_card_layout.setSpacing(8)
        ref_header = QHBoxLayout()
        ref_header.setSpacing(8)
        self.reference_summary = QLabel("未引用区域；将按整题提问")
        self.reference_summary.setObjectName("MutedLabel")
        self.reference_summary.setWordWrap(True)
        self._reference_toggle = QPushButton("展开")
        self._reference_toggle.setObjectName("HistoryLinkButton")
        self._reference_toggle.clicked.connect(self._toggle_reference_card)
        ref_header.addWidget(self.reference_summary, 1)
        ref_header.addWidget(self._reference_toggle)
        ref_card_layout.addLayout(ref_header)
        self._reference_body = QWidget()
        ref_body_layout = QVBoxLayout(self._reference_body)
        ref_body_layout.setContentsMargins(0, 0, 0, 0)
        ref_body_layout.setSpacing(6)
        reference_row = QHBoxLayout()
        self.add_reference_button = QPushButton("框选题图")
        self.add_reference_button.clicked.connect(self._enable_reference_mode)
        self.clear_references_button = ghost_button("清除全部")
        self.clear_references_button.clicked.connect(self._clear_references)
        self.delete_reference_button = ghost_button("删除选中")
        self.delete_reference_button.clicked.connect(self.reference_canvas.delete_selected)
        self.finish_reference_button = ghost_button("退出框选")
        self.finish_reference_button.clicked.connect(self._finish_reference_mode)
        self.finish_reference_button.setVisible(False)
        self.reference_source_combo = QComboBox()
        describe_field(self.reference_source_combo, "框选来源页面")
        self.reference_source_combo.currentIndexChanged.connect(self._set_reference_source)
        reference_row.addWidget(self.add_reference_button)
        reference_row.addWidget(self.clear_references_button)
        reference_row.addWidget(self.delete_reference_button)
        reference_row.addWidget(self.finish_reference_button)
        reference_row.addWidget(self.reference_source_combo)
        ref_body_layout.addLayout(reference_row)
        self.reference_previews = QListWidget()
        self.reference_previews.setObjectName("ReferencePreviewList")
        self.reference_previews.setAccessibleName("本次提问的引用区域")
        self.reference_previews.setViewMode(QListView.ViewMode.IconMode)
        self.reference_previews.setFlow(QListView.Flow.LeftToRight)
        self.reference_previews.setWrapping(False)
        self.reference_previews.setIconSize(QSize(56, 42))
        self.reference_previews.setFixedHeight(68)
        self.reference_previews.setVisible(False)
        self.reference_previews.itemActivated.connect(self._activate_reference_preview)
        ref_body_layout.addWidget(self.reference_previews)
        self._reference_body.setVisible(False)
        ref_card_layout.addWidget(self._reference_body)
        self.chat_card.body.addWidget(self._reference_card)

        # 底部输入区
        prompt_row = QHBoxLayout()
        prompt_row.setSpacing(8)
        self._attach_button = QPushButton()
        bind_icon(self._attach_button, "plus", size=18)
        self._attach_button.setObjectName("IconButton")
        self._attach_button.setFixedSize(36, 36)
        self._attach_button.setToolTip("附加功能")
        self._attach_button.setAccessibleName("附加功能")
        self._attach_button.clicked.connect(self._show_attach_menu)
        self.chat_input = _ChatInputEdit()
        self.chat_input.submit_requested.connect(self._send_chat)
        self.send_chat_button = primary_button("发送")
        self.send_chat_button.clicked.connect(self._toggle_send_or_stop)
        prompt_row.addWidget(self._attach_button, 0, Qt.AlignmentFlag.AlignBottom)
        prompt_row.addWidget(self.chat_input, 1)
        prompt_row.addWidget(self.send_chat_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.chat_card.body.addLayout(prompt_row)
        self.chat_card.setVisible(False)
        self.workspace.addWidget(self.chat_card)
        self.workspace.setStretchFactor(0, 2)
        self.workspace.setStretchFactor(1, 1)
        self.workspace.setSizes([800, 400])
        self.back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self.back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.back_shortcut.activated.connect(self.back_requested.emit)
        set_tab_order_chain(
            self.back_button,
            self.chat_button,
            self.edit_button,
            self.previous_button,
            self.next_button,
            self.review_button,
            self.favorite_button,
            self.more_button,
            self.reader,
            self.conversation_combo,
            self.chat_input,
            self.send_chat_button,
        )

    def set_back_text(self, text: str) -> None:
        self.back_button.setToolTip(text)
        self.back_button.setAccessibleName(text)

    def set_problem(
        self,
        problem: Problem,
        *,
        image_path: Path | None = None,
        content_blocks: list[dict[str, Any]] | None = None,
        subject_name: str | None = None,
        chapter_name: str | None = None,
    ) -> None:
        if self.problem_id:
            scroll_position = getattr(self.reader, "scroll_position", None)
            if callable(scroll_position):
                self._reader_scroll_by_problem[self.problem_id] = scroll_position()
            if conversation_id := self._conversation_id():
                self._conversation_by_problem[self.problem_id] = conversation_id
        self.problem_id = problem.id
        self._pending_reader_scroll = self._reader_scroll_by_problem.get(problem.id, 0)
        self.title_label.setText(problem.title or "无标题题目")
        is_trashed = problem.status == "trashed"
        self.review_button.setVisible(not is_trashed and problem.status != "archived")
        self.favorite_button.setVisible(not is_trashed)
        self.favorite_button.setText("取消收藏" if problem.is_favorite else "收藏")
        self.favorite_button.setProperty("targetFavorite", not problem.is_favorite)
        self.archive_action.setVisible(problem.status in {"active", "inbox"})
        self.trash_action.setVisible(not is_trashed)
        self.restore_action.setVisible(is_trashed)
        self.more_button.setVisible(not is_trashed or self.restore_action.isVisible())
        fields: dict[str, Any] = {
            column: getattr(problem, column)
            for column in (
                "title",
                "question_markdown",
                "question_latex",
                "user_answer",
                "correct_answer",
                "solution_markdown",
                "error_analysis",
                "notes",
                "problem_type",
                "priority",
                "source_book",
            )
        }
        fields["subject_name"] = subject_name
        fields["chapter_name"] = chapter_name
        fields["content_blocks"] = content_blocks or []
        self.reader.set_problem(
            fields,
            tag_names=[tag.name for tag in (problem.tags or [])],
            include_answers=True,
            show_header=False,
            classic=True,
        )
        self.chat_card.setVisible(False)
        self.reader_stack.setCurrentWidget(self.reader)
        self.reader_stack.setVisible(True)
        self.reader.setVisible(True)
        self.reference_canvas.clear()
        self.finish_reference_button.setVisible(False)
        self.chat_button.setText("AI 讨论")
        self._configure_reference_source()
        self._refresh_conversations()

    def _request_chat(self) -> None:
        if self.problem_id:
            self.chat_requested.emit(self.problem_id)

    def _toggle_chat(self) -> None:
        if self.width() < 860 and self.chat_card.isVisible():
            self.reader_stack.setVisible(True)
            self.reader.setVisible(True)
            self.chat_card.setVisible(False)
            self.chat_button.setText("AI 讨论")
            self.reader.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        opening = not self.chat_card.isVisible()
        if opening:
            focus = QApplication.focusWidget()
            self._focus_before_chat = focus if focus and self.isAncestorOf(focus) else self.reader
        self.chat_card.setVisible(not self.chat_card.isVisible())
        if self.chat_card.isVisible():
            if self.width() < 860:
                self.reader_stack.setVisible(False)
                self.reader.setVisible(False)
                self.chat_button.setText("查看题目")
            self._set_chat_split_sizes()
            self._refresh_conversations()
            self.chat_input.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            target = self._focus_before_chat or self.reader
            if target.isVisible() and target.isEnabled():
                target.setFocus(Qt.FocusReason.OtherFocusReason)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._update_toolbar_layout()
        narrow = self.width() < 860
        self.workspace.setOrientation(
            Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        )
        if narrow and self.chat_card.isVisible():
            self.reader_stack.setVisible(False)
            self.reader.setVisible(False)
            self.chat_button.setText("查看题目")
        elif not narrow:
            self.reader_stack.setVisible(True)
            if self.reader_stack.currentWidget() is self.reader:
                self.reader.setVisible(True)
            elif self.reader_stack.currentWidget() is self.reference_scroll:
                self._fit_reference_canvas()
            self.chat_button.setText("AI 讨论")
            if self.chat_card.isVisible():
                self._set_chat_split_sizes()

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._update_toolbar_layout()

    def _configure_reference_source(self) -> None:
        self._reference_sources = (
            self._ensure_render_sources() if self.chat and self.problem_id else []
        )
        self.reference_source_combo.blockSignals(True)
        self.reference_source_combo.clear()
        for source in self._reference_sources:
            self.reference_source_combo.addItem(f"PDF 第 {int(source['page_index']) + 1} 页", source)
        self.reference_source_combo.blockSignals(False)
        self._set_reference_source()
        enabled = bool(self._reference_sources)
        self.add_reference_button.setEnabled(enabled)
        self.add_reference_button.setToolTip(
            "在左侧 PDF 上拖拽框选" if enabled else "正在生成题目 PDF…"
        )
        self.clear_references_button.setEnabled(enabled)
        self.reference_source_combo.setEnabled(enabled and len(self._reference_sources) > 1)
        self._update_reference_summary()

    def _ensure_render_sources(self) -> list[dict[str, Any]]:
        render_pages = getattr(self.reader, "render_pages", None)
        if not callable(render_pages):
            return []
        pages = render_pages()
        if not pages:
            return []
        encoded: list[bytes] = []
        for image in pages:
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buffer, "PNG")
            encoded.append(bytes(buffer.data()))
            buffer.close()
        return self.chat.ensure_render_sources(self.problem_id, encoded)

    def _on_reader_rendered(self, *_args) -> None:
        if self.problem_id:
            self._configure_reference_source()

    def _fit_reference_canvas(self) -> None:
        source = self.reference_source_combo.currentData()
        if not isinstance(source, dict):
            return
        width = max(120, self.reference_scroll.viewport().width() - 8)
        self.reference_canvas.set_source(
            str(source["asset_id"]),
            int(source["page_index"]),
            source["path"],
            fit_width=width,
        )

    def _set_reference_source(self, *_args) -> None:
        source = self.reference_source_combo.currentData()
        if not isinstance(source, dict):
            source = None
        self.reference_canvas.set_source(
            str(source["asset_id"]) if source else "",
            int(source["page_index"]) if source else 0,
            source["path"] if source else None,
            fit_width=max(120, self.reference_scroll.viewport().width() - 8),
        )
        self._update_reference_summary()

    def _enable_reference_mode(self) -> None:
        if self.reference_source_combo.currentData() is None:
            self.status_message.emit("题目 PDF 尚未生成，请稍后再试")
            return
        self.reader_stack.setCurrentWidget(self.reference_scroll)
        self.reference_scroll.show()
        self._fit_reference_canvas()
        if self.width() < 860:
            self.reader_stack.setVisible(True)
            self.chat_card.setVisible(False)
            self.chat_button.setText("返回讨论")
        self.reference_canvas.begin_selection()
        self.finish_reference_button.setVisible(True)
        self.add_reference_button.setText("请在 PDF 上拖拽框选")

    def _reference_selection_finished(self) -> None:
        self.add_reference_button.setText("框选题图")

    def _finish_reference_mode(self) -> None:
        self.reference_canvas.cancel_selection()
        self.reader_stack.setCurrentWidget(self.reader)
        self.reader.setVisible(True)
        self.finish_reference_button.setVisible(False)
        self.add_reference_button.setText("框选题图")

    def _activate_reference_preview(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        references = self.reference_canvas.references()
        if not isinstance(index, int) or not 0 <= index < len(references):
            return
        reference = references[index]
        for source_index, source in enumerate(self._reference_sources):
            if source["asset_id"] == reference.asset_id:
                self.reference_source_combo.setCurrentIndex(source_index)
                break
        self.reference_canvas.select_reference(index)
        self.reader_stack.setCurrentWidget(self.reference_scroll)
        self.reference_scroll.show()
        self._fit_reference_canvas()
        self.finish_reference_button.setVisible(True)

    def _clear_references(self) -> None:
        self.reference_canvas.clear()

    def _set_chat_split_sizes(self) -> None:
        if self.workspace.orientation() != Qt.Orientation.Horizontal:
            return
        available = max(1, self.workspace.width() - self.workspace.handleWidth())
        self.workspace.setSizes([available * 2 // 3, available // 3])
        QTimer.singleShot(0, self.reader.fit_to_width)

    def _update_reference_summary(self) -> None:
        references = self.reference_canvas.references()
        count = len(references)
        if count:
            summary = f"本次引用 {count} 个区域"
        elif self._reference_sources:
            summary = "未引用区域；将按整题提问"
        else:
            summary = "PDF 尚未生成；将按整题文字提问"
        self.reference_summary.setText(summary)
        self.reference_previews.clear()
        for index, reference in enumerate(references):
            item = QListWidgetItem(str(index + 1))
            item.setData(Qt.ItemDataRole.UserRole, index)
            preview = self._reference_preview(reference)
            if not preview.isNull():
                item.setIcon(QIcon(preview))
            item.setToolTip(f"引用区域 {index + 1} · 第 {reference.page_index + 1} 页 PDF")
            self.reference_previews.addItem(item)
        self.reference_previews.setVisible(bool(references))
        self.delete_reference_button.setEnabled(bool(references))

    def _reference_preview(self, reference: ProblemReference) -> QPixmap:
        source = next(
            (value for value in self._reference_sources if value["asset_id"] == reference.asset_id),
            None,
        )
        pixmap = QPixmap(str(source["path"])) if source else QPixmap()
        if pixmap.isNull():
            return QPixmap()
        rect = pixmap.rect()
        crop = QRect(
            round(rect.width() * reference.x),
            round(rect.height() * reference.y),
            max(1, round(rect.width() * reference.width)),
            max(1, round(rect.height() * reference.height)),
        ).intersected(rect)
        return pixmap.copy(crop).scaled(
            QSize(56, 42),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _refresh_conversations(self) -> None:
        preferred = (
            self._conversation_by_problem.get(self.problem_id or "") or self._conversation_id()
        )
        self.conversation_combo.blockSignals(True)
        self.conversation_combo.clear()
        if self.chat is not None and self.problem_id:
            for conversation in self.chat.list_conversations(self.problem_id):
                self.conversation_combo.addItem(conversation.title, conversation.id)
        if preferred:
            index = self.conversation_combo.findData(preferred)
            if index >= 0:
                self.conversation_combo.setCurrentIndex(index)
        self.conversation_combo.blockSignals(False)
        self._load_conversation()

    def _conversation_changed(self, *_args) -> None:
        if self.problem_id and (conversation_id := self._conversation_id()):
            self._conversation_by_problem[self.problem_id] = conversation_id
        self._load_conversation()

    def _restore_reader_scroll(self) -> None:
        restore = getattr(self.reader, "restore_scroll_position", None)
        if callable(restore):
            restore(self._pending_reader_scroll)

    def _conversation_id(self) -> str | None:
        value = self.conversation_combo.currentData()
        return value if isinstance(value, str) else None

    def _load_conversation(self, *_args) -> None:
        conversation_id = self._conversation_id()
        if self.chat is None or not conversation_id:
            self._set_chat_history("选择或新建一个题目讨论。")
            return
        conversation = self.chat.get_conversation(conversation_id)
        if conversation is None:
            self._set_chat_history("该讨论不存在。")
            return
        self._chat_flow.clear()
        for message in conversation.messages:
            role = "user" if message.role == "user" else "assistant"
            text = message.content_markdown
            if message.status == "failed" and message.error_message:
                text = f"{text}\n\n（失败：{message.error_message}）"
            bubble = self._chat_flow.add_message(role, text)
            if message.role == "user":
                try:
                    references = json.loads(message.reference_snapshot_json or "[]")
                except (ValueError, TypeError):
                    references = []
                if references and bubble.label is not None:
                    labels = "、".join(
                        f"{index}（PDF 第 {int(value.get('page_index', 0)) + 1} 页）"
                        for index, value in enumerate(references, start=1)
                        if isinstance(value, dict)
                    )
                    bubble.label.setToolTip(f"引用区域：{labels}")
        self._chat_flow.scroll_to_bottom()

    def _set_chat_history(self, content: str) -> None:
        self._chat_flow.clear()
        if content:
            self._chat_flow.add_message("assistant", content)

    def _new_conversation(self) -> None:
        if self.chat is None or not self.problem_id:
            return
        # 当前已是一个没有任何消息的新对话时，
        # 点击加号不再重复新建，避免空对话堆积。
        current_id = self._conversation_id()
        if current_id:
            current = self.chat.get_conversation(current_id)
            if current is not None and not current.messages:
                return
        conversation = self.chat.create_conversation(self.problem_id)
        self._refresh_conversations()
        self.conversation_combo.setCurrentIndex(self.conversation_combo.findData(conversation.id))

    def _rename_conversation(self) -> None:
        conversation_id = self._conversation_id()
        if self.chat is None or not conversation_id:
            return
        title, accepted = QInputDialog.getText(self, "命名对话", "名称：")
        if accepted:
            self.chat.rename_conversation(conversation_id, title)
            self._refresh_conversations()

    def _send_chat(self) -> None:
        if self.chat is None or self._chat_job_id is not None:
            return
        if self.ai_coordinator is None:
            self._set_chat_history("聊天任务队列不可用，请重启应用后重试。")
            return
        conversation_id = self._conversation_id()
        if not conversation_id:
            self._new_conversation()
            conversation_id = self._conversation_id()
        if not conversation_id:
            return
        content = self.chat_input.toPlainText().strip()
        if not content:
            self._set_chat_history("请输入要讨论的问题。")
            return
        self.chat_input.clear()
        self._chat_flow.add_message("user", content)
        self._streaming_text = ""
        self._streaming_bubble = self._chat_flow.add_message("assistant", "正在生成回答…")
        self._set_chat_busy(True)
        references = [
            reference.as_dict() for reference in self.reference_canvas.references()
        ]
        try:
            job = self.ai_coordinator.ai.create_background_job(
                domain="problem_chat",
                context_id=conversation_id,
                job_type="chat",
                config={"content": content, "references": references},
            )
        except Exception as exc:  # noqa: BLE001
            self._set_chat_busy(False)
            self._on_chat_failed(str(exc))
            return
        self._chat_job_id = job.id
        self.ai_coordinator.enqueue(job.id)

    def _set_chat_busy(self, busy: bool) -> None:
        self.chat_input.setEnabled(not busy)
        self.send_chat_button.setEnabled(True)
        self.send_chat_button.setText("停止生成" if busy else "发送")
        self.conversation_combo.setEnabled(not busy)

    def _on_chat_failed(self, error: str) -> None:
        self._stream_timer.stop()
        if self._streaming_bubble is not None:
            text = self._streaming_text or "正在生成回答…"
            self._streaming_bubble.set_markdown(f"{text}\n\n（发送失败：{error}）")
            self._streaming_bubble = None
        else:
            self._set_chat_history(f"发送失败：{error}")

    def _flush_stream_bubble(self) -> None:
        if self._streaming_bubble is None:
            return
        self._stream_timer.stop()
        self._streaming_bubble.set_markdown(self._streaming_text or "正在生成回答…")
        self._chat_flow.scroll_to_bottom()

    def _toggle_send_or_stop(self) -> None:
        if self._chat_job_id is None:
            self._send_chat()
            return
        job_id = self._chat_job_id
        if self.ai_coordinator is not None:
            self.ai_coordinator.cancel(job_id)
        self._chat_job_id = None
        self._stream_timer.stop()
        if self._streaming_bubble is not None:
            self._flush_stream_bubble()
            self._streaming_bubble = None
        self._set_chat_busy(False)

    def _run_problem_chat_job(
        self, job_id: str, emit_progress, should_cancel
    ) -> dict[str, Any]:
        job = self.ai_coordinator.ai.get_job(job_id)
        if job is None:
            raise DomainError("聊天任务不存在")
        try:
            config = json.loads(job.config_json)
        except json.JSONDecodeError:
            config = {}
        content = str(config.get("content") or "")
        raw_references = config.get("references") or []
        references = [
            ProblemReference.from_value(value) for value in raw_references
        ]

        def receive(delta: str) -> None:
            if should_cancel() or not delta:
                return
            emit_progress(
                {
                    "stage": "streaming",
                    "label": "正在接收 AI 回复",
                    "text_delta": delta,
                }
            )

        message = self.chat.send_message(
            job.context_id,
            content,
            references,
            on_text_delta=receive,
        )
        return {
            "conversation_id": job.context_id,
            "message_id": message.id,
        }

    def _on_problem_chat_progress(self, job_id: str, event: object) -> None:
        if job_id != self._chat_job_id:
            return
        if isinstance(event, dict) and event.get("stage") == "streaming":
            delta = event.get("text_delta") or ""
            if delta:
                self._streaming_text += delta
                if (
                    self._streaming_bubble is not None
                    and not self._stream_timer.isActive()
                ):
                    self._stream_timer.start()

    def _on_problem_chat_job_finished(self, job_id: str) -> None:
        if job_id != self._chat_job_id:
            return
        self._chat_job_id = None
        self._stream_timer.stop()
        self._flush_stream_bubble()
        self._streaming_bubble = None
        self._set_chat_busy(False)
        self.reference_canvas.clear()
        self._finish_reference_mode()
        self._load_conversation()

    def _on_problem_chat_job_failed(self, job_id: str, message: str) -> None:
        if job_id != self._chat_job_id:
            return
        self._chat_job_id = None
        self._stream_timer.stop()
        if self._streaming_bubble is not None:
            text = self._streaming_text or "正在生成回答…"
            self._streaming_bubble.set_markdown(f"{text}\n\n（发送失败：{message}）")
            self._streaming_bubble = None
        else:
            self._set_chat_history(f"发送失败：{message}")
        self._set_chat_busy(False)

    def _show_chat_more_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("命名对话", self._rename_conversation)
        menu.addAction("保存对话", self._save_conversation)
        menu.addAction("导出对话", self._export_conversation)
        menu.addSeparator()
        delete_action = menu.addAction("删除对话", self._delete_conversation)
        delete_action.setProperty("danger", True)
        show_dropdown_menu(menu, self._more_button)

    def _show_attach_menu(self) -> None:
        menu = QMenu(self)
        box_action = menu.addAction("框选题图", self._enable_reference_mode)
        box_action.setEnabled(bool(self._reference_sources))
        box_action.setToolTip(
            "在左侧 PDF 上拖拽框选" if self._reference_sources else "题目 PDF 尚未生成"
        )
        clear_action = menu.addAction("清除全部引用", self._clear_references)
        clear_action.setEnabled(bool(self.reference_canvas.references()))
        delete_action = menu.addAction("删除选中引用", self.reference_canvas.delete_selected)
        delete_action.setEnabled(self.reference_canvas._selected_index >= 0)
        finish_action = menu.addAction("退出框选", self._finish_reference_mode)
        finish_action.setEnabled(self.reader_stack.currentWidget() is self.reference_scroll)
        show_dropdown_menu(menu, self._attach_button)

    def _toggle_reference_card(self) -> None:
        visible = not self._reference_body.isVisible()
        self._reference_body.setVisible(visible)
        self._reference_toggle.setText("收起" if visible else "展开")

    def _on_chat_follow_changed(self, follow: bool) -> None:
        self._back_to_latest_button.setVisible(not follow)
    def _save_conversation(self) -> None:
        if self.chat and (conversation_id := self._conversation_id()):
            self.chat.save_conversation(conversation_id)

    def _delete_conversation(self) -> None:
        if self.chat and (conversation_id := self._conversation_id()):
            self.chat.delete_conversation(conversation_id)
            self._refresh_conversations()

    def _export_conversation(self) -> None:
        conversation_id = self._conversation_id()
        if self.chat is None or not conversation_id:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出对话", "problem-chat.md", "Markdown (*.md)"
        )
        if path:
            self.chat.export_conversation_markdown(conversation_id, Path(path))

    def _request_edit(self) -> None:
        if self.problem_id:
            self.edit_requested.emit(self.problem_id)

    def _request_review(self) -> None:
        if self.problem_id:
            self.schedule_review_requested.emit(self.problem_id)

    def _request_favorite(self) -> None:
        if self.problem_id:
            self.favorite_requested.emit(
                self.problem_id,
                bool(self.favorite_button.property("targetFavorite")),
            )

    def _request_archive(self) -> None:
        if self.problem_id:
            self.archive_requested.emit(self.problem_id)

    def _request_trash(self) -> None:
        if self.problem_id:
            self.trash_requested.emit(self.problem_id)

    def _request_restore(self) -> None:
        if self.problem_id:
            self.restore_requested.emit(self.problem_id)

    def _show_more_menu(self) -> None:
        show_dropdown_menu(self.more_menu, self.more_button)
