"""Dedicated problem reading page."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.problem_chat_service import ProblemChatService
from yancuo_win.data.models import Problem
from yancuo_win.tasks.worker import ProblemChatWorker
from yancuo_win.ui.image_viewer import ImageViewerDialog
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import (
    CardFrame,
    PageHeader,
    describe_field,
    ghost_button,
    primary_button,
    set_tab_order_chain,
)


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

    def __init__(self, chat: ProblemChatService | None = None, parent=None) -> None:
        super().__init__(parent)
        self.chat = chat
        self.problem_id: str | None = None
        self._image_path: Path | None = None
        self._chat_worker: ProblemChatWorker | None = None
        self.setObjectName("PageRoot")

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        self.back_button = ghost_button("返回题库")
        self.back_button.clicked.connect(self.back_requested.emit)
        describe_field(self.back_button, "返回题库", "返回上一页，快捷键 Alt+Left")
        self.back_button.setToolTip("返回题库 (Alt+Left)")
        self.edit_button = primary_button("编辑题目")
        self.edit_button.clicked.connect(self._request_edit)
        self.view_image_button = ghost_button("查看原图")
        self.view_image_button.clicked.connect(self._view_original_image)
        self.chat_button = ghost_button("AI 讨论")
        self.chat_button.clicked.connect(self._toggle_chat)
        self.chat_button.setEnabled(chat is not None)
        header = PageHeader("题目详情")
        self.title_label = header.title
        self.meta_label = header.description
        self.meta_label.setVisible(True)
        header.add_leading(self.back_button)
        header.add_action(self.view_image_button)
        header.add_action(self.chat_button)
        header.add_action(self.edit_button)
        root.addWidget(header)

        actions = QHBoxLayout()
        self.previous_button = ghost_button("上一题")
        self.previous_button.clicked.connect(self.previous_requested.emit)
        self.next_button = ghost_button("下一题")
        self.next_button.clicked.connect(self.next_requested.emit)
        actions.addWidget(self.previous_button)
        actions.addWidget(self.next_button)
        actions.addSpacing(12)
        self.review_button = QPushButton("加入复习计划")
        self.review_button.clicked.connect(self._request_review)
        self.favorite_button = QPushButton("收藏")
        self.favorite_button.clicked.connect(self._request_favorite)
        self.archive_button = QPushButton("归档")
        self.archive_button.clicked.connect(self._request_archive)
        self.trash_button = QPushButton("移入回收站")
        self.trash_button.setObjectName("DangerButton")
        self.trash_button.clicked.connect(self._request_trash)
        self.restore_button = primary_button("恢复到正式题库")
        self.restore_button.clicked.connect(self._request_restore)
        for button in (
            self.review_button,
            self.favorite_button,
            self.archive_button,
            self.trash_button,
            self.restore_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)

        self.reader = MathContentView()
        self.reader.set_adaptive_content_height(560)
        self.reader.set_accessible_content(
            "题目正文与解析",
            "只读题目内容；可使用方向键或翻页键浏览长内容",
        )
        root.addWidget(self.reader, stretch=1)

        self.chat_card = CardFrame()
        self.chat_card.add_title("AI 讨论")
        chat_toolbar = QHBoxLayout()
        self.conversation_combo = QComboBox()
        describe_field(self.conversation_combo, "AI 讨论会话", "选择已保存的题目讨论")
        self.conversation_combo.currentIndexChanged.connect(self._load_conversation)
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
        self.include_original_checkbox = QCheckBox("授权附带原图")
        self.include_original_checkbox.setToolTip("仅在新建对话时发送一次原图授权")
        for widget in (self.conversation_combo, new_chat, rename_chat, save_chat, export_chat, delete_chat, self.include_original_checkbox):
            chat_toolbar.addWidget(widget)
        self.chat_card.body.addLayout(chat_toolbar)
        self.chat_history = MathContentView()
        self.chat_history.setMinimumHeight(180)
        self.chat_history.set_accessible_content(
            "AI 讨论记录",
            "只读对话历史；可使用方向键浏览",
        )
        self.chat_card.body.addWidget(self.chat_history)
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
        root.addWidget(self.chat_card)
        self.back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self.back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.back_shortcut.activated.connect(self.back_requested.emit)
        set_tab_order_chain(
            self.back_button,
            self.view_image_button,
            self.chat_button,
            self.edit_button,
            self.previous_button,
            self.next_button,
            self.review_button,
            self.favorite_button,
            self.archive_button,
            self.trash_button,
            self.restore_button,
            self.reader,
            self.conversation_combo,
            self.include_original_checkbox,
            self.chat_input,
            self.send_chat_button,
        )

    def set_back_text(self, text: str) -> None:
        self.back_button.setText(text)
        self.back_button.setAccessibleName(text)

    def set_problem(
        self,
        problem: Problem,
        *,
        image_path: Path | None = None,
        subject_name: str | None = None,
        chapter_name: str | None = None,
    ) -> None:
        self.problem_id = problem.id
        self.title_label.setText(problem.title or "无标题题目")
        status = {
            "inbox": "收件箱",
            "active": "正式题库",
            "archived": "归档",
            "trashed": "回收站",
        }.get(problem.status, problem.status)
        self.meta_label.setText(
            f"{status} · 优先级 P{problem.priority} · 已复习 {problem.review_count} 次"
        )
        is_trashed = problem.status == "trashed"
        self.review_button.setVisible(not is_trashed and problem.status != "archived")
        self.favorite_button.setVisible(not is_trashed)
        self.favorite_button.setText("取消收藏" if problem.is_favorite else "收藏")
        self.favorite_button.setProperty("targetFavorite", not problem.is_favorite)
        self.archive_button.setVisible(problem.status in {"active", "inbox"})
        self.trash_button.setVisible(not is_trashed)
        self.restore_button.setVisible(is_trashed)
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
        self.reader.set_problem(
            fields,
            tag_names=[tag.name for tag in (problem.tags or [])],
            include_answers=True,
            show_header=False,
        )
        # Keep original media out of the reading layout and avoid decoding it
        # until the user explicitly opens the viewer.
        self._image_path = image_path if image_path and image_path.is_file() else None
        self.view_image_button.setEnabled(self._image_path is not None)
        self.view_image_button.setToolTip(
            "打开可缩放原图" if self._image_path else "原始图片不存在或不可读取"
        )
        self.chat_card.setVisible(False)
        self._refresh_conversations()

    def _view_original_image(self) -> None:
        if self._image_path is None:
            return
        source = QPixmap(str(self._image_path))
        if source.isNull():
            self.view_image_button.setEnabled(False)
            self.view_image_button.setToolTip("原始图片格式无法读取")
            return
        ImageViewerDialog(source, self).exec()

    def _toggle_chat(self) -> None:
        self.chat_card.setVisible(not self.chat_card.isVisible())
        if self.chat_card.isVisible():
            self._refresh_conversations()

    def _refresh_conversations(self) -> None:
        self.conversation_combo.blockSignals(True)
        self.conversation_combo.clear()
        if self.chat is not None and self.problem_id:
            for conversation in self.chat.list_conversations(self.problem_id):
                self.conversation_combo.addItem(conversation.title, conversation.id)
        self.conversation_combo.blockSignals(False)
        self._load_conversation()

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
            lines.append(f"\n{role}\n{message.content_markdown}{suffix}")
        self._set_chat_history("\n".join(lines))

    def _set_chat_history(self, content: str) -> None:
        self.chat_history.set_message("AI 讨论", content)

    def _new_conversation(self) -> None:
        if self.chat is None or not self.problem_id:
            return
        conversation = self.chat.create_conversation(
            self.problem_id,
            include_original_image=self.include_original_checkbox.isChecked(),
        )
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
        worker = ProblemChatWorker(self.chat, conversation_id, content, self)
        worker.finished_ok.connect(self._on_chat_completed)
        worker.failed.connect(self._on_chat_failed)
        worker.finished.connect(self._on_chat_worker_finished)
        self._chat_worker = worker
        worker.start()

    def _set_chat_busy(self, busy: bool) -> None:
        self.chat_input.setEnabled(not busy)
        self.send_chat_button.setEnabled(not busy)
        self.conversation_combo.setEnabled(not busy)
        self.include_original_checkbox.setEnabled(not busy)

    def _on_chat_completed(self, message: object) -> None:
        status = getattr(message, "status", "failed")
        if status == "complete":
            self.chat_input.clear()
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
        path, _ = QFileDialog.getSaveFileName(self, "导出对话", "problem-chat.md", "Markdown (*.md)")
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
