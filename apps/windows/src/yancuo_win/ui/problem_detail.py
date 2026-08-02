"""Dedicated problem reading page."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QBoxLayout,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
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
from yancuo_win.tasks.worker import ProblemChatWorker
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

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
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

    def set_source(self, asset_id: str, page_index: int, path: Path | None) -> None:
        self._asset_id, self._page_index = asset_id, page_index
        self._pixmap = QPixmap(str(path)) if path else QPixmap()
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
            self.cancel_selection()
            self.selection_finished.emit()
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

    def __init__(self, chat: ProblemChatService | None = None, parent=None) -> None:
        super().__init__(parent)
        self.chat = chat
        self.problem_id: str | None = None
        self._chat_worker: ProblemChatWorker | None = None
        self._conversation_by_problem: dict[str, str] = {}
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
        self.chat_button.clicked.connect(self._toggle_chat)
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
        self.more_button.setMenu(self.more_menu)
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
        self.reference_canvas = _ReferenceCanvas()
        self.reference_canvas.changed.connect(self._update_reference_summary)
        self.reference_canvas.selection_finished.connect(self._reference_selection_finished)
        self.reader_stack = QStackedWidget()
        self.reader_stack.addWidget(self.reader)
        self.reader_stack.addWidget(self.reference_canvas)
        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace.setChildrenCollapsible(False)
        self.workspace.setHandleWidth(10)
        self.workspace.addWidget(self.reader_stack)
        root.addWidget(self.workspace, stretch=1)

        self.chat_card = CardFrame()
        self.chat_card.setMinimumWidth(360)
        self.chat_card.add_title("AI 讨论")
        chat_toolbar = QVBoxLayout()
        chat_toolbar.setSpacing(8)
        self.conversation_combo = QComboBox()
        describe_field(self.conversation_combo, "AI 讨论会话", "选择已保存的题目讨论")
        self.conversation_combo.currentIndexChanged.connect(self._conversation_changed)
        new_chat = QPushButton("新对话")
        new_chat.clicked.connect(self._new_conversation)
        rename_chat = QPushButton("命名")
        rename_chat.clicked.connect(self._rename_conversation)
        save_chat = QPushButton("保存")
        save_chat.clicked.connect(self._save_conversation)
        export_chat = QPushButton("导出")
        export_chat.clicked.connect(self._export_conversation)
        delete_chat = QPushButton("删除")
        delete_chat.clicked.connect(self._delete_conversation)
        conversation_row = QHBoxLayout()
        conversation_row.setSpacing(6)
        conversation_row.addWidget(self.conversation_combo, stretch=1)
        conversation_row.addWidget(new_chat)
        conversation_row.addWidget(rename_chat)
        conversation_row.addWidget(save_chat)
        management_row = QHBoxLayout()
        management_row.setSpacing(6)
        management_row.addWidget(export_chat)
        management_row.addWidget(delete_chat)
        management_row.addStretch(1)
        chat_toolbar.addLayout(conversation_row)
        chat_toolbar.addLayout(management_row)
        self.chat_card.body.addLayout(chat_toolbar)
        self.chat_history = MathContentView()
        self.chat_history.setMinimumWidth(0)
        self.chat_history.setMinimumHeight(180)
        self.chat_history.set_accessible_content(
            "AI 讨论记录",
            "只读对话历史；可使用方向键浏览",
        )
        if hasattr(self.chat_history, "render_completed"):
            self.chat_history.render_completed.connect(self.chat_history.scroll_to_bottom)
        self.chat_card.body.addWidget(self.chat_history)
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
        self.reference_summary = QLabel("未引用区域；将按整题提问")
        reference_row.addWidget(self.add_reference_button)
        reference_row.addWidget(self.clear_references_button)
        reference_row.addWidget(self.delete_reference_button)
        reference_row.addWidget(self.finish_reference_button)
        reference_row.addWidget(self.reference_source_combo)
        reference_row.addWidget(self.reference_summary, stretch=1)
        self.chat_card.body.addLayout(reference_row)
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
        self.chat_card.body.addWidget(self.reference_previews)
        prompt_row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("向当前题目提问")
        describe_field(self.chat_input, "AI 讨论问题", "输入后按回车发送")
        self.chat_input.returnPressed.connect(self._send_chat)
        self.send_chat_button = primary_button("发送")
        self.send_chat_button.clicked.connect(self._send_chat)
        prompt_row.addWidget(self.chat_input, stretch=1)
        prompt_row.addWidget(self.send_chat_button)
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
            self.chat_button.setText("AI 讨论")
            if self.chat_card.isVisible():
                self._set_chat_split_sizes()

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._update_toolbar_layout()

    def _configure_reference_source(self) -> None:
        self._reference_sources = (
            self.chat.list_reference_sources(self.problem_id)
            if self.chat and self.problem_id
            else []
        )
        self.reference_source_combo.blockSignals(True)
        self.reference_source_combo.clear()
        for source in self._reference_sources:
            self.reference_source_combo.addItem(f"题图 {int(source['page_index']) + 1}", source)
        self.reference_source_combo.blockSignals(False)
        self._set_reference_source()
        enabled = bool(self._reference_sources)
        self.add_reference_button.setEnabled(enabled)
        self.clear_references_button.setEnabled(enabled)
        self.reference_source_combo.setEnabled(enabled and len(self._reference_sources) > 1)
        self._update_reference_summary()

    def _set_reference_source(self, *_args) -> None:
        source = self.reference_source_combo.currentData()
        if not isinstance(source, dict):
            source = None
        self.reference_canvas.set_source(
            str(source["asset_id"]) if source else "",
            int(source["page_index"]) if source else 0,
            source["path"] if source else None,
        )
        self._update_reference_summary()

    def _enable_reference_mode(self) -> None:
        if self.reference_source_combo.currentData() is None:
            return
        self.reader_stack.setCurrentWidget(self.reference_canvas)
        self.reference_canvas.show()
        if self.width() < 860:
            self.reader_stack.setVisible(True)
            self.chat_card.setVisible(False)
            self.chat_button.setText("返回讨论")
        self.reference_canvas.begin_selection()
        self.finish_reference_button.setVisible(True)
        self.add_reference_button.setText("请在题图上拖拽框选")

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
        self.reader_stack.setCurrentWidget(self.reference_canvas)
        self.reference_canvas.show()
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
        self.reference_summary.setText(
            f"本次引用 {count} 个区域" if count else "未引用区域；将按整题提问"
        )
        self.reference_previews.clear()
        for index, reference in enumerate(references):
            item = QListWidgetItem(str(index + 1))
            item.setData(Qt.ItemDataRole.UserRole, index)
            preview = self._reference_preview(reference)
            if not preview.isNull():
                item.setIcon(QIcon(preview))
            item.setToolTip(f"引用区域 {index + 1} · 第 {reference.page_index + 1} 张题图")
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
            return
        lines = [f"基于题目修订版 {conversation.problem_revision}"]
        for message in conversation.messages:
            role = "我" if message.role == "user" else "AI"
            suffix = f"\n失败：{message.error_message}" if message.status == "failed" else ""
            try:
                references = json.loads(message.reference_snapshot_json or "[]")
            except (ValueError, TypeError):
                references = []
            reference_note = ""
            if references:
                labels = "、".join(
                    f"{index}（题图 {int(value.get('page_index', 0)) + 1}）"
                    for index, value in enumerate(references, start=1)
                    if isinstance(value, dict)
                )
                reference_note = f"\n引用区域：{labels}"
            lines.append(f"\n{role}\n{message.content_markdown}{reference_note}{suffix}")
        self._set_chat_history("\n".join(lines))

    def _set_chat_history(self, content: str) -> None:
        self.chat_history.set_message("AI 讨论", content)

    def _new_conversation(self) -> None:
        if self.chat is None or not self.problem_id:
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
        if self.chat is None or self._chat_worker is not None:
            return
        conversation_id = self._conversation_id()
        if not conversation_id:
            self._new_conversation()
            conversation_id = self._conversation_id()
        if not conversation_id:
            return
        content = self.chat_input.text().strip()
        if not content:
            self._set_chat_history("请输入要讨论的问题。")
            return
        self._set_chat_busy(True)
        self._set_chat_history("正在生成回答…（可继续浏览题目）")
        references = self.reference_canvas.references()
        worker = ProblemChatWorker(self.chat, conversation_id, content, references, self)
        worker.finished_ok.connect(self._on_chat_completed)
        worker.failed.connect(self._on_chat_failed)
        worker.finished.connect(self._on_chat_worker_finished)
        self._chat_worker = worker
        worker.start()

    def _set_chat_busy(self, busy: bool) -> None:
        self.chat_input.setEnabled(not busy)
        self.send_chat_button.setEnabled(not busy)
        self.conversation_combo.setEnabled(not busy)

    def _on_chat_completed(self, message: object) -> None:
        status = getattr(message, "status", "failed")
        if status == "complete":
            self.chat_input.clear()
            self.reference_canvas.clear()
            self._finish_reference_mode()
        self._load_conversation()

    def _on_chat_failed(self, error: str) -> None:
        self._set_chat_history(f"发送失败：{error}")

    def _on_chat_worker_finished(self) -> None:
        worker = self._chat_worker
        self._chat_worker = None
        self._set_chat_busy(False)
        if worker is not None:
            worker.deleteLater()

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
