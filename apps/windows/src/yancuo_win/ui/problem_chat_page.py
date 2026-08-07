"""AI 讨论独立子界面：顶部题目上下文卡 + 对话流 + 框选子界面。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.problem_chat_service import ProblemChatService, ProblemReference
from yancuo_win.data.models import Problem
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.ai_coordinator import AIJobCoordinator
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.problem_detail import (
    _ChatBubble,
    _ChatFlow,
    _ChatInputEdit,
    _ReferenceCanvas,
)
from yancuo_win.ui.widgets import (
    IconButton,
    PageHeader,
    describe_field,
    ghost_button,
    primary_button,
    show_dropdown_menu,
)


class BoxSelectPage(QWidget):
    """框选子界面：全屏展示 PDF/图片，用户拖拽框选多个区域。"""

    cancelled = Signal()
    confirmed = Signal(list)  # list[ProblemReference]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        self.cancel_button = ghost_button("取消")
        self.cancel_button.clicked.connect(self.cancelled.emit)
        self.reread_button = ghost_button("重新框选")
        self.reread_button.clicked.connect(self._reset_selection)
        self.confirm_button = primary_button("确定")
        self.confirm_button.clicked.connect(self._confirm)
        header.addWidget(self.cancel_button)
        header.addStretch(1)
        header.addWidget(QLabel("在图片上拖动框选一个或多个区域，可以重新框选"))
        header.addStretch(1)
        header.addWidget(self.reread_button)
        header.addWidget(self.confirm_button)
        root.addLayout(header)

        self.canvas = _ReferenceCanvas()
        self.canvas.changed.connect(self._on_changed)
        self.canvas.selection_finished.connect(self._on_selection_finished)
        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.canvas)
        self._scroll = scroll
        root.addWidget(scroll, stretch=1)

    def set_source(self, asset_id: str, page_index: int, path: Path | None) -> None:
        self.canvas.clear()
        self.canvas.set_source(asset_id, page_index, path, fit_width=1200)
        self.confirm_button.setEnabled(False)

    def _reset_selection(self) -> None:
        self.canvas.clear()
        self.confirm_button.setEnabled(False)
        self.canvas.begin_selection()

    def _on_changed(self) -> None:
        self.confirm_button.setEnabled(bool(self.canvas.references()))

    def _on_selection_finished(self) -> None:
        pass

    def _confirm(self) -> None:
        references = self.canvas.references()
        if not references:
            self.confirm_button.setEnabled(False)
            return
        self.confirmed.emit(references)




class ProblemChatPage(QWidget):
    """独立的 AI 讨论页：顶部题目卡 + 对话流 + 附加区 + 输入区。"""

    back_requested = Signal()  # 返回题目详情
    show_answer_requested = Signal()  # 展示答案与解析
    note_requested = Signal()  # 添加笔记
    status_message = Signal(str)

    def __init__(
        self,
        chat: ProblemChatService | None = None,
        coordinator: AIJobCoordinator | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.chat = chat
        self.ai_coordinator = coordinator
        self.problem_id: str | None = None
        self._problem: Problem | None = None
        self._chat_job_id: str | None = None
        self._streaming_bubble: _ChatBubble | None = None
        self._streaming_text = ""
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(120)
        self._stream_timer.timeout.connect(self._flush_stream_bubble)
        self._conversation_by_problem: dict[str, str] = {}
        self._reference_sources: list[dict[str, Any]] = []
        self._attached_references: list[ProblemReference] = []
        self._attached_notes: list[str] = []  # 附加解析等文本
        self.setObjectName("PageRoot")

        if coordinator is not None:
            coordinator.register_handler("problem_chat", self._run_problem_chat_job)
            coordinator.job_finished.connect(self._on_problem_chat_job_finished)
            coordinator.job_failed.connect(self._on_problem_chat_job_failed)
            coordinator.job_progress.connect(self._on_problem_chat_progress)

        self._stack = QStackedWidget(self)
        self._chat_view = QWidget()
        self._box_view = BoxSelectPage(self)
        self._box_view.cancelled.connect(self._close_box_select)
        self._box_view.confirmed.connect(self._apply_box_select)
        self._stack.addWidget(self._chat_view)
        self._stack.addWidget(self._box_view)

        self._build_chat_view()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._stack)

        # 题目渲染源（作为框选来源，不显示）
        self._source_reader = MathContentView()
        self._source_reader.set_compact(True)

    def _build_chat_view(self) -> None:
        root = QVBoxLayout(self._chat_view)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ---- 顶部：返回 + 标题 + 会话选择/新建/更多 ----
        header_row = QHBoxLayout()
        self.back_button = IconButton("chevron-left", "返回题目")
        self.back_button.clicked.connect(self.back_requested.emit)
        header_row.addWidget(self.back_button)
        self.header = PageHeader("AI 讨论", "针对当前题目的 AI 讨论，支持框选区域提问。")
        header_row.addWidget(self.header, 1)
        self.conversation_combo = QComboBox()
        describe_field(self.conversation_combo, "AI 讨论会话", "选择已保存的题目讨论")
        self.conversation_combo.setMinimumWidth(180)
        self.conversation_combo.currentIndexChanged.connect(self._conversation_changed)
        header_row.addWidget(self.conversation_combo)
        self._new_chat_button = QPushButton()
        bind_icon = __import__("yancuo_win.ui.icons", fromlist=["bind_icon"]).bind_icon
        bind_icon(self._new_chat_button, "plus", size=18)
        self._new_chat_button.setObjectName("IconButton")
        self._new_chat_button.setFixedSize(34, 34)
        self._new_chat_button.setToolTip("新建对话")
        self._new_chat_button.setAccessibleName("新建对话")
        self._new_chat_button.clicked.connect(self._new_conversation)
        header_row.addWidget(self._new_chat_button)
        self._more_button = QPushButton()
        bind_icon(self._more_button, "more-horizontal", size=18)
        self._more_button.setObjectName("IconButton")
        self._more_button.setFixedSize(34, 34)
        self._more_button.setToolTip("更多操作")
        self._more_button.setAccessibleName("更多操作")
        self._more_button.clicked.connect(self._show_chat_more_menu)
        header_row.addWidget(self._more_button)
        root.addLayout(header_row)

        # ---- 题目上下文卡（薄卡，72~96px）----
        self.context_card = QFrame()
        self.context_card.setObjectName("ChatReferenceCard")
        self.context_card.setFixedHeight(88)
        card = QHBoxLayout(self.context_card)
        card.setContentsMargins(12, 8, 12, 8)
        card.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(4)
        self.context_title = QLabel("未关联题目")
        self.context_title.setObjectName("SectionTitle")
        self.context_title.setWordWrap(False)
        left.addWidget(self.context_title)
        self.context_preview = QLabel("")
        self.context_preview.setObjectName("MutedLabel")
        self.context_preview.setWordWrap(False)
        left.addWidget(self.context_preview)
        card.addLayout(left, 1)
        self.context_status = QLabel("已关联当前题目")
        self.context_status.setObjectName("MutedLabel")
        card.addWidget(self.context_status)
        self.expand_problem_button = ghost_button("展开题目")
        self.expand_problem_button.clicked.connect(self.back_requested.emit)
        card.addWidget(self.expand_problem_button)
        self.show_answer_button = ghost_button("答案与解析")
        self.show_answer_button.clicked.connect(self.show_answer_requested.emit)
        card.addWidget(self.show_answer_button)
        root.addWidget(self.context_card)

        # ---- 对话流 ----
        self._chat_flow = _ChatFlow()
        self._chat_flow.follow_changed.connect(self._on_chat_follow_changed)
        root.addWidget(self._chat_flow, stretch=1)
        back_row = QHBoxLayout()
        back_row.addStretch(1)
        self._back_to_latest_button = ghost_button("回到最新消息")
        self._back_to_latest_button.clicked.connect(self._chat_flow.scroll_to_bottom)
        self._back_to_latest_button.hide()
        back_row.addWidget(self._back_to_latest_button)
        root.addLayout(back_row)

        # ---- 附加区（框选缩略图卡）----
        self.attach_card = QFrame()
        self.attach_card.setObjectName("ChatReferenceCard")
        attach_layout = QVBoxLayout(self.attach_card)
        attach_layout.setContentsMargins(10, 8, 10, 8)
        attach_layout.setSpacing(6)
        attach_header = QHBoxLayout()
        self.attach_status = QLabel("已关联当前题目")
        self.attach_status.setObjectName("MutedLabel")
        attach_header.addWidget(self.attach_status, 1)
        self.clear_attach_button = ghost_button("清除附加")
        self.clear_attach_button.clicked.connect(self._clear_attachments)
        attach_header.addWidget(self.clear_attach_button)
        attach_layout.addLayout(attach_header)
        self.attach_previews = QListWidget()
        self.attach_previews.setObjectName("ReferencePreviewList")
        self.attach_previews.setViewMode(QListView.ViewMode.IconMode)
        self.attach_previews.setFlow(QListView.Flow.LeftToRight)
        self.attach_previews.setWrapping(False)
        self.attach_previews.setIconSize(QSize(64, 48))
        self.attach_previews.setFixedHeight(64)
        self.attach_previews.setVisible(False)
        self.attach_previews.itemActivated.connect(self._activate_attach_preview)
        attach_layout.addWidget(self.attach_previews)
        self.attach_card.setVisible(False)
        root.addWidget(self.attach_card)

        # ---- 输入区 ----
        prompt_row = QHBoxLayout()
        prompt_row.setSpacing(8)
        self._attach_button = QPushButton()
        bind_icon(self._attach_button, "plus", size=18)
        self._attach_button.setObjectName("IconButton")
        self._attach_button.setFixedSize(36, 36)
        self._attach_button.setToolTip("添加内容")
        self._attach_button.setAccessibleName("添加内容")
        self._attach_button.clicked.connect(self._show_attach_menu)
        prompt_row.addWidget(self._attach_button)
        self.chat_input = _ChatInputEdit()
        self.chat_input.submit_requested.connect(self._send_chat)
        prompt_row.addWidget(self.chat_input, 1)
        self.send_chat_button = primary_button("发送")
        self.send_chat_button.clicked.connect(self._toggle_send_or_stop)
        prompt_row.addWidget(self.send_chat_button)
        root.addLayout(prompt_row)

        self._back_shortcut = QShortcut(QKeySequence("Alt+Left"), self)
        self._back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._back_shortcut.activated.connect(self.back_requested.emit)


    # ---- 题目关联 ----
    def set_problem(
        self,
        problem: Problem,
        *,
        content_blocks: list[dict[str, Any]] | None = None,
        subject_name: str | None = None,
        chapter_name: str | None = None,
    ) -> None:
        self.problem_id = problem.id
        self._problem = problem
        self.context_title.setText(problem.title or "(无标题题目)")
        preview = (problem.question_markdown or "").strip().replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:60] + "…"
        self.context_preview.setText(preview)
        tags: list[str] = []
        if problem.problem_type:
            tags.append(problem.problem_type)
        if subject_name:
            tags.append(subject_name)
        if chapter_name:
            tags.append(chapter_name)
        if tags:
            self.context_status.setText("已关联当前题目 · " + " · ".join(tags))
        else:
            self.context_status.setText("已关联当前题目")
        self._clear_attachments()
        self._configure_reference_source()
        self._refresh_conversations()

    # ---- 会话管理 ----
    def _conversation_id(self) -> str | None:
        value = self.conversation_combo.currentData()
        return value if isinstance(value, str) else None

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
        current_id = self._conversation_id()
        if current_id:
            current = self.chat.get_conversation(current_id)
            if current is not None and not current.messages:
                return
        conversation = self.chat.create_conversation(self.problem_id)
        self._refresh_conversations()
        self.conversation_combo.setCurrentIndex(
            self.conversation_combo.findData(conversation.id)
        )

    def _rename_conversation(self) -> None:
        conversation_id = self._conversation_id()
        if self.chat is None or not conversation_id:
            return
        title, accepted = QInputDialog.getText(self, "命名对话", "名称：")
        if accepted:
            self.chat.rename_conversation(conversation_id, title)
            self._refresh_conversations()

    def _save_conversation(self) -> None:
        conversation_id = self._conversation_id()
        if self.chat is None or not conversation_id:
            return
        try:
            self.chat.save_conversation(conversation_id)
            self.status_message.emit("对话已保存")
        except DomainError as exc:
            self.status_message.emit(str(exc))

    def _export_conversation(self) -> None:
        conversation_id = self._conversation_id()
        if self.chat is None or not conversation_id:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出对话", "讨论.md", "Markdown (*.md)"
        )
        if not path:
            return
        try:
            self.chat.export_conversation_markdown(conversation_id, Path(path))
            self.status_message.emit("对话已导出")
        except DomainError as exc:
            self.status_message.emit(str(exc))

    def _delete_conversation(self) -> None:
        conversation_id = self._conversation_id()
        if self.chat is None or not conversation_id:
            return
        if (
            QMessageBox.question(self, "确认", "删除该对话？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.chat.delete_conversation(conversation_id)
        self._conversation_by_problem.pop(self.problem_id or "", None)
        self._refresh_conversations()

    def _show_chat_more_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("命名对话", self._rename_conversation)
        menu.addAction("保存对话", self._save_conversation)
        menu.addAction("导出对话", self._export_conversation)
        menu.addSeparator()
        delete_action = menu.addAction("删除对话", self._delete_conversation)
        delete_action.setProperty("danger", True)
        show_dropdown_menu(menu, self._more_button)

    def _on_chat_follow_changed(self, follow: bool) -> None:
        self._back_to_latest_button.setVisible(not follow)


    # ---- 发送与任务 ----
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
        notes = "".join(self._attached_notes)
        if notes:
            content = f"{content}\n\n（附加解析参考）\n{notes}".strip()
        if not content:
            self._set_chat_history("请输入要讨论的问题。")
            return
        self.chat_input.clear()
        self._chat_flow.add_message("user", content)
        self._streaming_text = ""
        self._streaming_bubble = self._chat_flow.add_message("assistant", "正在生成回答…")
        self._set_chat_busy(True)
        references = [
            reference.as_dict() for reference in self._attached_references
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
        self._clear_attachments()
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


    # ---- 框选源（PDF/图片）----
    def _ensure_render_sources(self) -> list[dict[str, Any]]:
        render_pages = getattr(self._source_reader, "render_pages", None)
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

    def _configure_reference_source(self) -> None:
        self._reference_sources = (
            self._ensure_render_sources() if self.chat and self.problem_id else []
        )
        self._update_attach_status()

    def _render_source_path(self) -> tuple[str, int, Path | None] | None:
        if not self._reference_sources:
            return None
        source = self._reference_sources[0]
        return (
            str(source["asset_id"]),
            int(source["page_index"]),
            source["path"],
        )

    # ---- 加号菜单 ----
    def _show_attach_menu(self) -> None:
        menu = QMenu(self)
        box_action = menu.addAction("框选题目区域", self._open_box_select)
        box_action.setEnabled(bool(self._reference_sources))
        box_action.setToolTip(
            "在题目 PDF/图片上拖动框选"
            if self._reference_sources
            else "题目 PDF 尚未生成"
        )
        upload_action = menu.addAction("上传图片", self._upload_image)
        upload_action.setToolTip("上传一张图片作为附加内容")
        answer_action = menu.addAction("引用答案解析", self._attach_answer_notes)
        answer_action.setEnabled(self._problem is not None)
        menu.addAction("添加笔记", self.note_requested.emit)
        show_dropdown_menu(menu, self._attach_button)

    def _upload_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "上传图片", "", "图片 (*.png *.jpg *.jpeg *.webp)"
        )
        if not path:
            return
        self._attached_upload_images = getattr(self, "_attached_upload_images", [])
        self._attached_upload_images.append(Path(path))
        self._update_attach_status()

    def _attach_answer_notes(self) -> None:
        problem = self._problem
        if problem is None:
            return
        notes: list[str] = []
        if problem.correct_answer:
            notes.append(f"正确答案：{problem.correct_answer}")
        if problem.solution_markdown:
            notes.append(f"解析：{problem.solution_markdown}")
        if not notes:
            self.status_message.emit("该题目没有已存的答案或解析")
            return
        self._attached_notes = notes
        self._update_attach_status()

    # ---- 框选子界面 ----
    def _open_box_select(self) -> None:
        if not self._reference_sources:
            self.status_message.emit("题目 PDF 尚未生成，请稍后再试")
            return
        source = self._render_source_path()
        if source is None:
            return
        asset_id, page_index, path = source
        self._box_view.set_source(asset_id, page_index, path)
        self._stack.setCurrentWidget(self._box_view)
        self._box_view.canvas.begin_selection()

    def _close_box_select(self) -> None:
        self._box_view.canvas.cancel_selection()
        self._stack.setCurrentWidget(self._chat_view)

    def _apply_box_select(self, references: list[ProblemReference]) -> None:
        self._attached_references = list(references)
        self._stack.setCurrentWidget(self._chat_view)
        self._update_attach_status()

    # ---- 附加内容管理 ----
    def _clear_attachments(self) -> None:
        self._attached_references = []
        self._attached_notes = []
        self._attached_upload_images = []
        self._update_attach_status()

    def _update_attach_status(self) -> None:
        parts = ["已关联当前题目"]
        reference_count = len(self._attached_references)
        upload_count = len(getattr(self, "_attached_upload_images", []))
        note_count = len(self._attached_notes)
        if reference_count:
            parts.append(f"已附加 {reference_count} 个框选区域")
        if upload_count:
            parts.append(f"已附加 {upload_count} 张图片")
        if note_count:
            parts.append("已附加答案解析")
        self.attach_status.setText("    ".join(parts))

        self.attach_previews.clear()
        for index, reference in enumerate(self._attached_references):
            item = QListWidgetItem(f"框选 {index + 1}")
            item.setData(Qt.ItemDataRole.UserRole, ("reference", index))
            preview = self._reference_preview(reference)
            if not preview.isNull():
                item.setIcon(QIcon(preview))
            item.setToolTip(f"框选区域 {index + 1} · 第 {reference.page_index + 1} 页 PDF")
            self.attach_previews.addItem(item)
        for index, path in enumerate(getattr(self, "_attached_upload_images", [])):
            item = QListWidgetItem(f"图片 {index + 1}")
            item.setData(Qt.ItemDataRole.UserRole, ("image", index))
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap.scaled(
                    QSize(64, 48),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )))
            self.attach_previews.addItem(item)
        visible = self.attach_previews.count() > 0
        self.attach_previews.setVisible(visible)
        self.attach_card.setVisible(visible or note_count > 0)
        self.clear_attach_button.setEnabled(
            reference_count > 0 or upload_count > 0 or note_count > 0
        )

    def _reference_preview(self, reference: ProblemReference) -> QPixmap:
        source = next(
            (
                value
                for value in self._reference_sources
                if value["asset_id"] == reference.asset_id
            ),
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
            QSize(64, 48),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def _activate_attach_preview(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return
        kind, index = data
        if kind == "reference":
            if 0 <= index < len(self._attached_references):
                reference = self._attached_references[index]
                for source in self._reference_sources:
                    if source["asset_id"] == reference.asset_id:
                        self._box_view.set_source(
                            str(source["asset_id"]),
                            int(source["page_index"]),
                            source["path"],
                        )
                        self._box_view.canvas.clear()
                        self._box_view.canvas.add_normalized_region(
                            reference.x, reference.y, reference.width, reference.height
                        )
                        break
                self._stack.setCurrentWidget(self._box_view)
        elif kind == "image":
            images = getattr(self, "_attached_upload_images", [])
            if 0 <= index < len(images):
                self.status_message.emit(f"附加图片：{images[index]}")

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        if self.problem_id and self.chat is not None:
            self._refresh_conversations()

    def shutdown(self) -> None:
        self._stream_timer.stop()

