"""In-shell note library, reader and block editor."""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.note_ai_service import (
    NoteAiService,
    NoteBlockDraft,
    NoteExtractionDraft,
)
from yancuo_win.application.note_service import NoteService
from yancuo_win.data.models import NoteBlock, NoteDocument
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.note_worker import NoteExtractionWorker
from yancuo_win.ui.widgets import CardFrame, danger_button, ghost_button, primary_button

_STATUS_LABELS = {
    "inbox": "待整理",
    "active": "正式",
    "archived": "归档",
    "trashed": "回收站",
}
_BLOCK_LABELS = {
    "heading": "标题",
    "text": "文本",
    "formula": "公式",
    "callout": "提示",
    "image": "图片",
}


class NoteExtractionDialog(QDialog):
    """Review the AI draft before it becomes a note document."""

    def __init__(self, draft: NoteExtractionDraft, parent=None) -> None:
        super().__init__(parent)
        self.draft = draft
        self.blocks = list(draft.blocks)
        self.setWindowTitle("AI 笔记 · 确认结果")
        self.resize(760, 680)

        root = QVBoxLayout(self)
        title = QLabel("AI 已完成笔记整理，请确认后入库")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setMinimumHeight(160)
        preview.setMaximumHeight(240)
        pixmap = QPixmap(str(draft.source_path))
        if pixmap.isNull():
            preview.setText("原图无法预览")
        else:
            preview.setPixmap(
                pixmap.scaled(
                    700,
                    220,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        root.addWidget(preview)
        hint = QLabel(
            f"模型：{draft.model or '未返回'} · 建议科目：{draft.subject_suggestion or '未指定'} · "
            f"建议章节：{draft.chapter_suggestion or '未指定'}"
        )
        hint.setObjectName("PageHint")
        root.addWidget(hint)

        form = CardFrame()
        form.add_title("笔记信息")
        self.title_edit = QLineEdit(draft.title)
        self.summary_edit = QTextEdit()
        self.summary_edit.setPlainText(draft.summary)
        self.summary_edit.setFixedHeight(70)
        form.body.addWidget(self.title_edit)
        form.body.addWidget(self.summary_edit)
        root.addWidget(form)

        blocks = CardFrame()
        blocks.add_title("AI 提取的内容块")
        self.block_list = QListWidget()
        self.block_editor = QTextEdit()
        self.block_list.currentRowChanged.connect(self._select_block)
        blocks.body.addWidget(self.block_list, stretch=1)
        blocks.body.addWidget(self.block_editor, stretch=1)
        root.addWidget(blocks, stretch=1)
        self._refresh_blocks()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("确认入库")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _refresh_blocks(self) -> None:
        self.block_list.clear()
        for index, block in enumerate(self.blocks, start=1):
            value = block.content_latex if block.block_type == "formula" else block.content_markdown
            self.block_list.addItem(
                f"{index}. {_BLOCK_LABELS[block.block_type]} · {value[:50] or '（空）'}"
            )
        if self.blocks:
            self.block_list.setCurrentRow(0)

    def _select_block(self, row: int) -> None:
        if not 0 <= row < len(self.blocks):
            self.block_editor.clear()
            return
        block = self.blocks[row]
        self.block_editor.setPlainText(
            block.content_latex if block.block_type == "formula" else block.content_markdown
        )

    def _save_current_block(self) -> None:
        row = self.block_list.currentRow()
        if not 0 <= row < len(self.blocks):
            return
        block = self.blocks[row]
        value = self.block_editor.toPlainText()
        self.blocks[row] = NoteBlockDraft(
            block_type=block.block_type,
            content_markdown="" if block.block_type == "formula" else value,
            content_latex=value if block.block_type == "formula" else block.content_latex,
            uncertain_fields=block.uncertain_fields,
        )

    def accept(self) -> None:
        self._save_current_block()
        super().accept()

    def values(self) -> tuple[str, str, list[NoteBlockDraft]]:
        return self.title_edit.text(), self.summary_edit.toPlainText(), self.blocks


class NotePage(QWidget):
    """A local-first editor; image assets and AI intake are added in later slices."""

    status_message = Signal(str)
    notes_changed = Signal()

    def __init__(self, notes: NoteService, parent=None) -> None:
        super().__init__(parent)
        self.notes = notes
        self._notes: list[NoteDocument] = []
        self._note: NoteDocument | None = None
        self._block: NoteBlock | None = None
        self._loading = False
        self.note_ai = NoteAiService(notes.runtime)
        self.note_worker: NoteExtractionWorker | None = None
        self._build()
        self.reload()

    def _build(self) -> None:
        self.setObjectName("PageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("笔记")
        title.setObjectName("PageTitle")
        titles.addWidget(title)
        hint = QLabel("用可编辑的内容块整理公式、概念和学习记录。")
        hint.setObjectName("PageHint")
        titles.addWidget(hint)
        header.addLayout(titles)
        header.addStretch(1)
        self.new_note_button = primary_button("新建笔记")
        self.new_note_button.clicked.connect(self._create_note)
        header.addWidget(self.new_note_button)
        ai_note_button = primary_button("AI 图片录入")
        ai_note_button.clicked.connect(self._start_ai_extraction)
        header.addWidget(ai_note_button)
        root.addLayout(header)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)

        left = CardFrame()
        left.setMinimumWidth(230)
        left.add_title("笔记库")
        self.status_filter = QComboBox()
        for label, status in (
            ("正式笔记", "active"),
            ("待整理", "inbox"),
            ("归档", "archived"),
            ("回收站", "trashed"),
            ("全部笔记", None),
        ):
            self.status_filter.addItem(label, status)
        self.status_filter.currentIndexChanged.connect(self.reload)
        left.body.addWidget(self.status_filter)
        self.note_list = QListWidget()
        self.note_list.setObjectName("NoteList")
        self.note_list.currentItemChanged.connect(self._select_note)
        left.body.addWidget(self.note_list, stretch=1)
        split.addWidget(left)

        self.empty_card = CardFrame()
        self.empty_card.add_title("选择一篇笔记")
        self.empty_card.add_hint("新建笔记后，可以按块写入标题、正文、公式或提示。")
        empty_new = primary_button("新建第一篇笔记")
        empty_new.clicked.connect(self._create_note)
        self.empty_card.body.addWidget(empty_new)

        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(self.empty_card)
        self.detail_stack.addWidget(self._build_detail())
        split.addWidget(self.detail_stack)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([300, 900])
        root.addWidget(split, stretch=1)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        if self.note_worker and self.note_worker.isRunning():
            self.note_worker.wait(3000)
        super().closeEvent(event)

    def _build_detail(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.note_status = QLabel()
        self.note_status.setObjectName("MutedLabel")
        header.addWidget(self.note_status)
        header.addStretch(1)
        self.read_button = ghost_button("阅读预览")
        self.read_button.clicked.connect(lambda: self._set_mode("read"))
        self.edit_button = primary_button("编辑内容")
        self.edit_button.clicked.connect(lambda: self._set_mode("edit"))
        header.addWidget(self.read_button)
        header.addWidget(self.edit_button)
        layout.addLayout(header)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_editor())
        self.mode_stack.addWidget(self._build_reader())
        layout.addWidget(self.mode_stack, stretch=1)
        return page

    def _build_editor(self) -> QWidget:
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        info = CardFrame()
        info.add_title("笔记信息")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("笔记标题")
        self.summary_edit = QTextEdit()
        self.summary_edit.setPlaceholderText("摘要（可选）")
        self.summary_edit.setFixedHeight(62)
        info.body.addWidget(self.title_edit)
        info.body.addWidget(self.summary_edit)
        self.save_note_button = primary_button("保存笔记信息")
        self.save_note_button.clicked.connect(self._save_note)
        archive = QPushButton("归档")
        archive.clicked.connect(lambda: self._set_note_status("archived"))
        self.restore_button = QPushButton("恢复为正式")
        self.restore_button.clicked.connect(lambda: self._set_note_status("active"))
        self.trash_button = danger_button("移入回收站")
        self.trash_button.clicked.connect(lambda: self._set_note_status("trashed"))
        info.body.addLayout(
            self._row(self.save_note_button, archive, self.restore_button, self.trash_button)
        )
        layout.addWidget(info)

        body = QSplitter(Qt.Orientation.Horizontal)
        block_card = CardFrame()
        block_card.add_title("内容块")
        block_actions = QHBoxLayout()
        for block_type in ("heading", "text", "formula", "callout"):
            button = QPushButton(f"+ {_BLOCK_LABELS[block_type]}")
            button.clicked.connect(
                lambda _checked=False, value=block_type: self._add_block(value)
            )
            block_actions.addWidget(button)
        block_card.body.addLayout(block_actions)
        self.block_list = QListWidget()
        self.block_list.setObjectName("NoteBlockList")
        self.block_list.currentItemChanged.connect(self._select_block)
        block_card.body.addWidget(self.block_list, stretch=1)
        up = QPushButton("上移")
        up.clicked.connect(lambda: self._move_block(-1))
        down = QPushButton("下移")
        down.clicked.connect(lambda: self._move_block(1))
        block_card.body.addLayout(self._row(up, down))
        body.addWidget(block_card)

        self.block_editor = CardFrame()
        self.block_editor.add_title("编辑内容块")
        self.block_type_label = self.block_editor.add_hint("请选择一个内容块")
        self.block_content = QTextEdit()
        self.block_content.setPlaceholderText("选择内容块后开始编辑")
        self.block_editor.body.addWidget(self.block_content, stretch=1)
        self.save_block_button = primary_button("保存当前块")
        self.save_block_button.clicked.connect(self._save_block)
        self.delete_block_button = danger_button("删除当前块")
        self.delete_block_button.clicked.connect(self._delete_block)
        self.block_editor.body.addLayout(
            self._row(self.save_block_button, self.delete_block_button)
        )
        body.addWidget(self.block_editor)
        body.setStretchFactor(0, 1)
        body.setStretchFactor(1, 2)
        layout.addWidget(body, stretch=1)
        return editor

    def _build_reader(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.reader = QTextBrowser()
        self.reader.setObjectName("NoteReader")
        self.reader.setOpenExternalLinks(False)
        layout.addWidget(self.reader)
        return page

    @staticmethod
    def _row(*widgets: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch(1)
        return row

    def reload(self, *_args, select_note_id: str | None = None) -> None:
        if self._loading:
            return
        current_id = select_note_id or (self._note.id if self._note else None)
        try:
            self._notes = self.notes.list_notes(
                status=self.status_filter.currentData()
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._loading = True
        self.note_list.clear()
        selected_row = -1
        for index, note in enumerate(self._notes):
            title = note.title or "未命名笔记"
            preview = note.summary.strip() or self._block_preview(note)
            item = QListWidgetItem(f"{title}\n{preview or '尚未添加内容'}")
            item.setData(Qt.ItemDataRole.UserRole, note.id)
            self.note_list.addItem(item)
            if note.id == current_id:
                selected_row = index
        self._loading = False
        if selected_row >= 0:
            self.note_list.setCurrentRow(selected_row)
        elif self.note_list.count():
            self.note_list.setCurrentRow(0)
        else:
            self._note = None
            self._block = None
            self.detail_stack.setCurrentIndex(0)

    @staticmethod
    def _block_preview(note: NoteDocument) -> str:
        for block in note.blocks:
            value = block.content_latex if block.block_type == "formula" else block.content_markdown
            if value.strip():
                return value.replace("\n", " ")[:60]
        return ""

    def _select_note(self, current: QListWidgetItem | None, _previous=None) -> None:
        if self._loading or current is None:
            return
        note = self.notes.get_note(str(current.data(Qt.ItemDataRole.UserRole)))
        if note is None:
            self.reload()
            return
        self._note = note
        self._block = None
        self.detail_stack.setCurrentIndex(1)
        self._render_note()

    def _render_note(self) -> None:
        note = self._note
        if note is None:
            self.detail_stack.setCurrentIndex(0)
            return
        self._loading = True
        self.title_edit.setText(note.title)
        self.summary_edit.setPlainText(note.summary)
        self.note_status.setText(
            f"{_STATUS_LABELS[note.status]} · {len(note.blocks)} 个内容块 · 已保存到本地"
        )
        editable = note.status != "trashed"
        self.title_edit.setReadOnly(not editable)
        self.summary_edit.setReadOnly(not editable)
        self.save_note_button.setEnabled(editable)
        self.trash_button.setVisible(editable)
        self.restore_button.setVisible(note.status == "trashed")
        self.block_list.clear()
        for index, block in enumerate(note.blocks, start=1):
            value = block.content_latex if block.block_type == "formula" else block.content_markdown
            preview = value.replace("\n", " ")[:46] or "（空）"
            item = QListWidgetItem(f"{index}. {_BLOCK_LABELS[block.block_type]} · {preview}")
            item.setData(Qt.ItemDataRole.UserRole, block.id)
            self.block_list.addItem(item)
        self._loading = False
        if self.block_list.count():
            self.block_list.setCurrentRow(0)
        else:
            self._clear_block_editor()
        self._render_reader()

    def _select_block(self, current: QListWidgetItem | None, _previous=None) -> None:
        if self._loading or self._note is None:
            return
        block_id = str(current.data(Qt.ItemDataRole.UserRole)) if current else ""
        self._block = next((item for item in self._note.blocks if item.id == block_id), None)
        if self._block is None:
            self._clear_block_editor()
            return
        content = (
            self._block.content_latex
            if self._block.block_type == "formula"
            else self._block.content_markdown
        )
        self.block_type_label.setText(
            f"{_BLOCK_LABELS[self._block.block_type]}块"
            + (" · 输入 LaTeX 源码" if self._block.block_type == "formula" else "")
        )
        self.block_content.setPlainText(content)
        editable = self._note.status != "trashed"
        self.block_content.setReadOnly(not editable)
        self.save_block_button.setEnabled(editable)
        self.delete_block_button.setEnabled(editable)

    def _clear_block_editor(self) -> None:
        self._block = None
        self.block_type_label.setText("请选择一个内容块")
        self.block_content.clear()
        self.block_content.setReadOnly(True)
        self.save_block_button.setEnabled(False)
        self.delete_block_button.setEnabled(False)

    def _create_note(self) -> None:
        try:
            note = self.notes.create_note(title="未命名笔记", status="active")
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.reload(select_note_id=note.id)
        self.status_message.emit("已新建笔记，可以开始添加内容块")
        self.notes_changed.emit()

    def _start_ai_extraction(self) -> None:
        if self.note_worker and self.note_worker.isRunning():
            self.status_message.emit("AI 笔记录入正在处理中，请稍候")
            return
        path_text, _ = QFileDialog.getOpenFileName(
            self,
            "选择笔记图片",
            "",
            "图片 (*.jpg *.jpeg *.png *.webp)",
        )
        if not path_text:
            return
        instruction, accepted = QInputDialog.getMultiLineText(
            self,
            "告诉 AI 如何整理",
            "补充要求（可选）",
            "例如：把红笔标注整理成提示块；每个独立公式单独作为公式块。",
        )
        if not accepted:
            return
        self.status_message.emit("正在整理笔记图片…")
        self.note_worker = NoteExtractionWorker(
            self.note_ai,
            Path(path_text),
            instruction,
            self,
        )
        self.note_worker.finished_ok.connect(self._on_ai_extraction_ready)
        self.note_worker.failed.connect(self._on_ai_extraction_failed)
        self.note_worker.finished.connect(self._on_ai_worker_finished)
        self.note_worker.start()

    def _on_ai_extraction_ready(self, draft: NoteExtractionDraft) -> None:
        dialog = NoteExtractionDialog(draft, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status_message.emit("已取消 AI 笔记确认，原图未入库")
            return
        title, summary, blocks = dialog.values()
        confirmed = NoteExtractionDraft(
            source_path=draft.source_path,
            title=title,
            summary=summary,
            blocks=blocks,
            subject_suggestion=draft.subject_suggestion,
            chapter_suggestion=draft.chapter_suggestion,
            tags=draft.tags,
            uncertain_fields=draft.uncertain_fields,
            model=draft.model,
            cost_estimate=draft.cost_estimate,
        )
        try:
            note = self.note_ai.commit_draft(confirmed)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.reload(select_note_id=note.id)
        self.status_message.emit("AI 笔记已确认入库")
        self.notes_changed.emit()

    def _on_ai_extraction_failed(self, message: str) -> None:
        self.status_message.emit(f"AI 笔记录入失败：{message}")

    def _on_ai_worker_finished(self) -> None:
        self.note_worker = None

    def _save_note(self) -> None:
        if self._note is None:
            return
        try:
            note = self.notes.update_note(
                self._note.id,
                {"title": self.title_edit.text(), "summary": self.summary_edit.toPlainText()},
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._note = note
        self.reload(select_note_id=note.id)
        self.status_message.emit("笔记信息已保存")
        self.notes_changed.emit()

    def _set_note_status(self, status: str) -> None:
        if self._note is None:
            return
        try:
            note = self.notes.update_note(self._note.id, {"status": status})
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.status_filter.setCurrentIndex(
            self.status_filter.findData(note.status)
        )
        self.reload(select_note_id=note.id)
        self.status_message.emit(f"笔记已移动至{_STATUS_LABELS[note.status]}")
        self.notes_changed.emit()

    def _add_block(self, block_type: str) -> None:
        if self._note is None:
            return
        try:
            block = self.notes.add_block(self._note.id, block_type=block_type)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.reload(select_note_id=self._note.id)
        for index in range(self.block_list.count()):
            item = self.block_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == block.id:
                self.block_list.setCurrentItem(item)
                break
        self.status_message.emit(f"已添加{_BLOCK_LABELS[block_type]}块")
        self.notes_changed.emit()

    def _save_block(self) -> None:
        if self._block is None or self._note is None:
            return
        content = self.block_content.toPlainText()
        values = (
            {"content_latex": content, "content_markdown": ""}
            if self._block.block_type == "formula"
            else {"content_markdown": content}
        )
        try:
            self.notes.update_block(self._block.id, values)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        block_id = self._block.id
        self.reload(select_note_id=self._note.id)
        for index in range(self.block_list.count()):
            item = self.block_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == block_id:
                self.block_list.setCurrentItem(item)
                break
        self.status_message.emit("内容块已保存")
        self.notes_changed.emit()

    def _delete_block(self) -> None:
        if self._block is None or self._note is None:
            return
        if QMessageBox.question(self, "删除内容块", "确定删除当前内容块吗？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.notes.delete_block(self._block.id)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.reload(select_note_id=self._note.id)
        self.status_message.emit("内容块已删除")
        self.notes_changed.emit()

    def _move_block(self, offset: int) -> None:
        if self._note is None or self._block is None:
            return
        ids = [block.id for block in self._note.blocks]
        index = ids.index(self._block.id)
        target = index + offset
        if target < 0 or target >= len(ids):
            return
        ids[index], ids[target] = ids[target], ids[index]
        try:
            self.notes.reorder_blocks(self._note.id, ids)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        block_id = self._block.id
        self.reload(select_note_id=self._note.id)
        for row in range(self.block_list.count()):
            item = self.block_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == block_id:
                self.block_list.setCurrentItem(item)
                break
        self.notes_changed.emit()

    def _set_mode(self, mode: str) -> None:
        self.mode_stack.setCurrentIndex(1 if mode == "read" else 0)
        if mode == "read":
            self._render_reader()

    def _render_reader(self) -> None:
        note = self._note
        if note is None:
            self.reader.setHtml("")
            return
        blocks: list[str] = []
        for block in note.blocks:
            value = block.content_latex if block.block_type == "formula" else block.content_markdown
            if block.block_type == "heading":
                blocks.append(f"<h2>{escape(value or '（未命名标题）')}</h2>")
            elif block.block_type == "formula":
                blocks.append(f"<pre class='formula'>{escape(value or '（空公式）')}</pre>")
            elif block.block_type == "callout":
                blocks.append(f"<aside>{escape(value or '（空提示）')}</aside>")
            else:
                blocks.append(f"<p>{escape(value or '（空内容）').replace(chr(10), '<br>')}</p>")
        body = "".join(blocks) or "<p class='muted'>尚未添加内容块。</p>"
        self.reader.setHtml(
            "<html><head><style>"
            "body{font-family:'Microsoft YaHei UI';font-size:16px;line-height:1.8;padding:18px;}"
            "h1{font-size:28px;}h2{margin-top:26px;}p{white-space:normal;}"
            ".formula{padding:14px;background:#f4f6fa;border-radius:8px;white-space:pre-wrap;}"
            "aside{padding:12px 16px;background:#edf4ff;border-left:4px solid #3772ff;border-radius:6px;}"
            ".muted{color:#768399;}"
            "</style></head><body>"
            f"<h1>{escape(note.title or '未命名笔记')}</h1>"
            f"<p class='muted'>{escape(note.summary)}</p>{body}</body></html>"
        )
