"""In-shell note library, reader and block editor."""

from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.ai.base import normalize_region
from yancuo_win.application.ai_service import AIService
from yancuo_win.application.note_ai_service import (
    NoteAiService,
    NoteBlockDraft,
    NoteExtractionDraft,
)
from yancuo_win.application.note_intake_service import NoteIntakeService
from yancuo_win.application.services import AppServices
from yancuo_win.application.note_service import NoteListRow, NoteService
from yancuo_win.application.note_ai_search_service import NoteAiSearchService
from yancuo_win.application.unified_search_service import UnifiedSearchIndexService
from yancuo_win.data.models import NoteBlock, NoteDocument, NoteIntakeSession
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.ai_coordinator import AIJobCoordinator
from yancuo_win.tasks.note_search_worker import NoteAiSearchWorker
from yancuo_win.ui.icons import bind_icon
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import (
    CardFrame,
    IconButton,
    PageHeader,
    ReadingCanvas,
    SearchInput,
    SoftItemDelegate,
    danger_button,
    describe_field,
    deferred_view_updates,
    ghost_button,
    primary_button,
    set_tab_order_chain,
    show_dropdown_menu,
)

_STATUS_LABELS = {
    "inbox": "待整理",
    "active": "正式",
    "archived": "归档",
    "trashed": "回收站",
}
_BLOCK_LABELS = {
    "heading": "标题",
    "text": "文本",
    "concept": "概念",
    "formula": "公式",
    "callout": "提示",
    "image": "图片",
}
_NOTE_LIST_BATCH_SIZE = 500

_NOTE_STATUS_LABELS = {
    "active": "正式",
    "inbox": "草稿",
    "archived": "已归档",
    "trashed": "回收站",
}


class NoteDraftGroupTree(QTreeWidget):
    """Tree that translates a block drop into a persistent draft operation."""

    block_dropped = Signal(str, str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._from_task_queue = False
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def dropEvent(self, event) -> None:
        source = self.currentItem()
        if source is None or source.parent() is None:
            event.ignore()
            return
        block_id = source.data(0, Qt.ItemDataRole.UserRole)
        target_item = self.itemAt(event.position().toPoint())
        if not block_id or target_item is None:
            event.ignore()
            return
        target_root = target_item
        while target_root.parent() is not None:
            target_root = target_root.parent()
        target_group_id = target_root.data(0, Qt.ItemDataRole.UserRole)
        if not target_group_id:
            event.ignore()
            return
        if target_item.parent() is None:
            target_index = target_root.childCount()
        else:
            target_index = target_root.indexOfChild(target_item)
            if event.position().y() > self.visualItemRect(target_item).center().y():
                target_index += 1
        self.block_dropped.emit(str(block_id), str(target_group_id), target_index)
        event.acceptProposedAction()


class NoteBlockList(QListWidget):
    """An editor-only ordered block list that reports a completed internal drop."""

    blocks_reordered = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._drag_from_handle = False
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    def mousePressEvent(self, event) -> None:
        self._drag_from_handle = event.position().x() <= 28
        super().mousePressEvent(event)

    def startDrag(self, supported_actions) -> None:  # noqa: N802, ANN001
        if self._drag_from_handle:
            super().startDrag(supported_actions)

    def dropEvent(self, event) -> None:
        before = self._block_ids()
        super().dropEvent(event)
        after = self._block_ids()
        if event.isAccepted() and after != before:
            self.blocks_reordered.emit(after)

    def _block_ids(self) -> list[str]:
        return [
            str(self.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.count())
        ]


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
            source_region=block.source_region,
            uncertain_fields=block.uncertain_fields,
        )

    def accept(self) -> None:
        self._save_current_block()
        super().accept()

    def values(self) -> tuple[str, str, list[NoteBlockDraft]]:
        return self.title_edit.text(), self.summary_edit.toPlainText(), self.blocks


class NoteIntakeSetupDialog(QDialog):
    """Collect the source and classification choice before creating a session."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 笔记录入")
        self.resize(520, 280)
        root = QVBoxLayout(self)
        root.addWidget(QLabel("来源图片"))
        source_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        browse = ghost_button("选择图片")
        browse.clicked.connect(self._choose_source)
        source_row.addWidget(self.path_edit, stretch=1)
        source_row.addWidget(browse)
        root.addLayout(source_row)
        root.addWidget(QLabel("分类方式"))
        self.classification_mode = QComboBox()
        self.classification_mode.addItem("自定义分类", "custom")
        self.classification_mode.addItem("AI 识别并分组", "ai")
        root.addWidget(self.classification_mode)
        root.addWidget(QLabel("补充要求（可选）"))
        self.instruction_edit = QTextEdit()
        self.instruction_edit.setPlaceholderText("例如：把红笔标注整理成提示块。")
        self.instruction_edit.setFixedHeight(80)
        root.addWidget(self.instruction_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("开始识别")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _choose_source(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self, "选择笔记图片", "", "图片 (*.jpg *.jpeg *.png *.webp)"
        )
        if path_text:
            self.path_edit.setText(path_text)

    def values(self) -> tuple[Path, str, str] | None:
        path = Path(self.path_edit.text())
        if not path.is_file():
            return None
        return path, self.instruction_edit.toPlainText(), str(self.classification_mode.currentData())


class NoteDraftPreviewPage(QWidget):
    """Review an AI draft as a dedicated workspace page before committing it."""

    back_requested = Signal()
    confirmed = Signal(tuple)

    def __init__(
        self, intake: NoteIntakeSession, note_intake: NoteIntakeService, parent=None
    ) -> None:
        super().__init__(parent)
        self.note_intake = note_intake
        self.catalog = AppServices(note_intake.runtime)
        self.intake = intake
        self.confirmed_note_ids: tuple[str, ...] = ()
        self._refreshing_groups = False
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)
        header = PageHeader("确认 AI 笔记", "检查内容块与分类后再保存到笔记库。")
        back = IconButton("chevron-left", "返回 AI 录入")
        back.clicked.connect(self.back_requested.emit)
        header.add_leading(back)
        root.addWidget(header)
        mode = "AI 识别并分组" if intake.classification_mode == "ai" else "自定义分类"
        root.addWidget(QLabel(f"{mode} · 草稿已暂存，尚未入库"))
        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("概念块布局"))
        self.block_layout = QComboBox()
        self.block_layout.addItem("列表", "list")
        self.block_layout.addItem("紧凑网格", "grid")
        self.block_layout.currentIndexChanged.connect(self._change_block_layout)
        layout_row.addWidget(self.block_layout)
        layout_row.addStretch(1)
        root.addLayout(layout_row)

        self.block_views = QStackedWidget()
        self.groups = NoteDraftGroupTree()
        self.groups.setHeaderLabels(["分类组与内容块", "序号", "目标分类"])
        self.groups.itemDoubleClicked.connect(self._edit_selected_group)
        self.groups.block_dropped.connect(self._move_block)
        self.groups.itemChanged.connect(self._change_block_order)
        self.groups.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.groups.customContextMenuRequested.connect(self._show_block_context_menu)
        self.block_views.addWidget(self.groups)

        self.concept_grid = QListWidget()
        self.concept_grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.concept_grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.concept_grid.setWrapping(True)
        self.concept_grid.setWordWrap(True)
        self.concept_grid.setSpacing(8)
        self.concept_grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.concept_grid.customContextMenuRequested.connect(self._show_concept_grid_context_menu)
        self.block_views.addWidget(self.concept_grid)
        root.addWidget(self.block_views, stretch=1)

        actions = QHBoxLayout()
        edit = ghost_button("编辑分类")
        edit.clicked.connect(self._edit_selected_group)
        add = ghost_button("新增分类组")
        add.clicked.connect(self._add_group)
        delete = danger_button("删除空组")
        delete.clicked.connect(self._delete_group)
        merge = ghost_button("合并到分类组")
        merge.clicked.connect(self._merge_group)
        actions.addWidget(edit)
        actions.addWidget(add)
        actions.addWidget(delete)
        actions.addWidget(merge)
        actions.addStretch(1)
        root.addLayout(actions)

        hint = QLabel(
            "列表可拖拽内容块、直接编辑序号或右键移动分类。紧凑网格仅展示概念块：短内容自动紧凑排布，长内容保留更宽的阅读空间。"
        )
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = ghost_button("稍后继续")
        cancel.clicked.connect(self.back_requested.emit)
        confirm = primary_button("确认入库")
        confirm.clicked.connect(self._confirm_groups)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        root.addLayout(buttons)
        self._refresh_groups()

    def _refresh_groups(self) -> None:
        self._refreshing_groups = True
        self.groups.clear()
        for index, group in enumerate(self.intake.groups, start=1):
            root = QTreeWidgetItem(
                [
                    f"{index}. {group.title or '未命名分类组'} · {len(group.blocks)} 个内容块",
                    "",
                    self._category_label(group),
                ]
            )
            root.setData(0, Qt.ItemDataRole.UserRole, group.id)
            self.groups.addTopLevelItem(root)
            for order, block in enumerate(group.blocks, start=1):
                value = block.content_latex if block.block_type == "formula" else block.content_markdown
                child = QTreeWidgetItem(
                    root,
                    [
                        f"{_BLOCK_LABELS.get(block.block_type, block.block_type)} · {value[:70] or '（空）'}",
                        str(order),
                        "",
                    ],
                )
                child.setData(0, Qt.ItemDataRole.UserRole, block.id)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsEditable)
            root.setExpanded(True)
        self.groups.resizeColumnToContents(0)
        self.groups.resizeColumnToContents(1)
        self._refresh_concept_grid()
        self._refreshing_groups = False

    def _refresh_concept_grid(self) -> None:
        self.concept_grid.clear()
        for group in self.intake.groups:
            for order, block in enumerate(group.blocks, start=1):
                if block.block_type != "concept":
                    continue
                content = block.content_markdown.strip() or "（空概念）"
                item = QListWidgetItem(f"{group.title or '未命名分类组'} · {order}\n{content}")
                item.setData(Qt.ItemDataRole.UserRole, block.id)
                item.setData(Qt.ItemDataRole.UserRole + 1, group.id)
                # Short concepts scan well as small cards; long content keeps enough width to read.
                item.setSizeHint(QSize(190, 82) if len(content) <= 72 else QSize(360, 118))
                item.setToolTip(content)
                self.concept_grid.addItem(item)

    def _change_block_layout(self) -> None:
        self.block_views.setCurrentIndex(1 if self.block_layout.currentData() == "grid" else 0)

    @staticmethod
    def _category_label(group) -> str:
        if group.category_resolution == "existing":
            return "已有知识路径"
        if group.category_resolution == "create_new":
            return "新分类提案"
        return "待整理"

    def _selected_group(self):
        item = self.groups.currentItem()
        if item is None:
            return None
        while item.parent() is not None:
            item = item.parent()
        group_id = item.data(0, Qt.ItemDataRole.UserRole)
        return next((group for group in self.intake.groups if group.id == group_id), None)

    def _edit_selected_group(self, *_args) -> None:
        group = self._selected_group()
        if group is None:
            self._show_error("请先选择一个分类组")
            return
        title, ok = QInputDialog.getText(self, "分类组标题", "标题：", text=group.title)
        if not ok:
            return
        options = ["待整理", "选择已有路径", "新分类提案"]
        current = {"unresolved": 0, "existing": 1, "create_new": 2}[group.category_resolution]
        option, ok = QInputDialog.getItem(
            self, "分类方式", "分类：", options, current, False
        )
        if not ok:
            return
        try:
            if option == "选择已有路径":
                choices = self.catalog.list_category_choices()
                if not choices:
                    raise DomainError("尚无可选择的知识路径")
                labels = [choice.label for choice in choices]
                selected, accepted = QInputDialog.getItem(
                    self, "已有知识路径", "路径：", labels, 0, False
                )
                if not accepted:
                    return
                choice = choices[labels.index(selected)]
                self.intake = self.note_intake.update_group(
                    self.intake.id,
                    group.id,
                    title=title,
                    summary=group.summary,
                    category_resolution="existing",
                    subject_id=choice.subject_id,
                    chapter_id=choice.chapter_id,
                )
            elif option == "新分类提案":
                subject, accepted = QInputDialog.getText(
                    self, "新分类提案", "科目（可留空）：", text=group.proposed_subject
                )
                if not accepted:
                    return
                chapter, accepted = QInputDialog.getText(
                    self, "新分类提案", "章节（可留空）：", text=group.proposed_chapter
                )
                if not accepted:
                    return
                self.intake = self.note_intake.update_group(
                    self.intake.id,
                    group.id,
                    title=title,
                    summary=group.summary,
                    category_resolution="create_new",
                    proposed_subject=subject,
                    proposed_chapter=chapter,
                )
            else:
                self.intake = self.note_intake.update_group(
                    self.intake.id,
                    group.id,
                    title=title,
                    summary=group.summary,
                    category_resolution="unresolved",
                )
        except DomainError as exc:
            self._show_error(str(exc))
            return
        self._refresh_groups()

    def _add_group(self) -> None:
        title, ok = QInputDialog.getText(self, "新增分类组", "标题（可留空）：")
        if not ok:
            return
        try:
            self.intake = self.note_intake.add_group(self.intake.id, title=title)
        except DomainError as exc:
            self._show_error(str(exc))
            return
        self._refresh_groups()

    def _delete_group(self) -> None:
        group = self._selected_group()
        if group is None:
            self._show_error("请先选择一个分类组")
            return
        try:
            self.intake = self.note_intake.delete_group(self.intake.id, group.id)
        except DomainError as exc:
            self._show_error(str(exc))
            return
        self._refresh_groups()

    def _merge_group(self) -> None:
        source = self._selected_group()
        if source is None:
            self._show_error("请先选择要合并的分类组")
            return
        targets = [group for group in self.intake.groups if group.id != source.id]
        if not targets:
            self._show_error("至少需要两个分类组才能合并")
            return
        labels = [
            f"{index + 1}. {group.title or '未命名分类组'}"
            for index, group in enumerate(targets)
        ]
        selected, ok = QInputDialog.getItem(self, "合并分类组", "合并到：", labels, 0, False)
        if not ok:
            return
        target = targets[labels.index(selected)]
        try:
            self.intake = self.note_intake.merge_groups(
                self.intake.id, source_group_id=source.id, target_group_id=target.id
            )
        except DomainError as exc:
            self._show_error(str(exc))
            return
        self._refresh_groups()

    def _move_block(self, block_id: str, target_group_id: str, target_index: int) -> None:
        try:
            self.intake = self.note_intake.move_block(
                self.intake.id,
                block_id,
                target_group_id=target_group_id,
                target_index=target_index,
            )
        except DomainError as exc:
            self._show_error(str(exc))
        self._refresh_groups()

    def _change_block_order(self, item: QTreeWidgetItem, column: int) -> None:
        if self._refreshing_groups or column != 1 or item.parent() is None:
            return
        block_id = item.data(0, Qt.ItemDataRole.UserRole)
        group_id = item.parent().data(0, Qt.ItemDataRole.UserRole)
        try:
            target_index = int(item.text(1)) - 1
            if target_index < 0:
                raise ValueError
            self._move_block(str(block_id), str(group_id), target_index)
        except (ValueError, DomainError):
            self._show_error("序号必须是当前分类组内的有效正整数")
            self._refresh_groups()

    def _show_block_context_menu(self, position) -> None:
        item = self.groups.itemAt(position)
        if item is None or item.parent() is None:
            return
        self._show_move_menu(
            str(item.data(0, Qt.ItemDataRole.UserRole)), self.groups.viewport().mapToGlobal(position)
        )

    def _show_concept_grid_context_menu(self, position) -> None:
        item = self.concept_grid.itemAt(position)
        if item is None:
            return
        self._show_move_menu(
            str(item.data(Qt.ItemDataRole.UserRole)),
            self.concept_grid.viewport().mapToGlobal(position),
        )

    def _show_move_menu(self, block_id: str, global_position) -> None:
        menu = QMenu(self)
        move_menu = menu.addMenu("移动到分类")
        for group in self.intake.groups:
            action = move_menu.addAction(group.title or "未命名分类组")
            action.triggered.connect(
                lambda _checked=False, group_id=group.id: self._move_block_to_group(block_id, group_id)
            )
        menu.exec(global_position)

    def _move_block_to_group(self, block_id: str, group_id: str) -> None:
        self._move_block(block_id, group_id, len(next(
            group.blocks for group in self.intake.groups if group.id == group_id
        )))

    def _confirm_groups(self) -> None:
        try:
            notes = self.note_intake.confirm_groups(self.intake.id)
        except DomainError as exc:
            self._show_error(str(exc))
            return
        self.confirmed_note_ids = tuple(note.id for note in notes)
        self.confirmed.emit(self.confirmed_note_ids)

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "无法更新分类草稿", message)


class NotePage(QWidget):
    """A local-first editor; image assets and AI intake are added in later slices."""

    status_message = Signal(str)
    notes_changed = Signal()
    add_to_review_requested = Signal(str)
    library_shown = Signal()

    def __init__(
        self,
        notes: NoteService,
        coordinator: AIJobCoordinator | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.notes = notes
        self._notes: list[NoteListRow] = []
        self._note_visible_count = 0
        self._note: NoteDocument | None = None
        self._block: NoteBlock | None = None
        self._loading = False
        self._narrow_layout = False
        self._narrow_space_open = False
        self._collection_filter_id: str | None = None
        self._collection_note_ids: dict[str, set[str]] = {}
        self.note_ai = NoteAiService(notes.runtime)
        self.note_intake = NoteIntakeService(notes.runtime)
        self.ai_coordinator = coordinator or AIJobCoordinator(
            AIService(notes.runtime), self
        )
        self.note_job_id: str | None = None
        self.ai_coordinator.register_handler("note_intake", self._run_note_job)
        self.ai_coordinator.job_finished.connect(self._on_note_job_finished)
        self.ai_coordinator.job_failed.connect(self._on_note_job_failed)
        self.note_search = UnifiedSearchIndexService(notes.runtime)
        self.note_ai_search = NoteAiSearchService(notes.runtime)
        self.note_search_worker: NoteAiSearchWorker | None = None
        self._note_ai_search_ids: set[str] | None = None
        self._library_state: dict[str, object] | None = None
        self._build()
        self.reload()

    def _build(self) -> None:
        self.setObjectName("PageRoot")
        self.setMinimumSize(0, 0)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)
        self.page_stack = QStackedWidget()
        root.addWidget(self.page_stack, stretch=1)
        self.library_page = QWidget()
        self.library_page.setMinimumWidth(0)
        library_root = QVBoxLayout(self.library_page)
        library_root.setContentsMargins(0, 0, 0, 0)
        library_root.setSpacing(12)

        self.new_note_button = primary_button("新建笔记")
        self.new_note_button.clicked.connect(self._show_new_note_menu)
        self.resume_draft_button = ghost_button("继续草稿")
        self.resume_draft_button.clicked.connect(self._resume_note_draft)
        header = PageHeader(
            "笔记", "用可编辑的内容块整理公式、概念和学习记录。"
        )
        header.add_action(self.resume_draft_button)
        header.add_action(self.new_note_button)
        library_root.addWidget(header)

        search_toolbar = QFrame()
        search_toolbar.setObjectName("SearchToolbar")
        search_toolbar.setFixedHeight(44)
        search_row = QHBoxLayout(search_toolbar)
        search_row.setContentsMargins(8, 4, 8, 4)
        search_row.setSpacing(8)
        self.note_search_mode_group = QButtonGroup(self)
        self.note_search_mode_group.setExclusive(True)
        self.local_search_button = QPushButton("普通搜索")
        self.local_search_button.setCheckable(True)
        self.local_search_button.setChecked(True)
        describe_field(self.local_search_button, "普通搜索笔记", "完全离线，只查询本机笔记索引")
        self.ai_search_button = QPushButton("AI 搜索")
        self.ai_search_button.setCheckable(True)
        self.ai_search_button.setToolTip(
            "只向有限候选发送笔记标题、内容片段、标签和更新时间"
        )
        describe_field(self.ai_search_button, "AI 搜索笔记", "用自然语言描述想找的笔记")
        for button in (self.local_search_button, self.ai_search_button):
            button.setObjectName("SearchModeButton")
            button.setFixedHeight(36)
            self.note_search_mode_group.addButton(button)
            button.clicked.connect(self._on_note_search_mode_changed)
            search_row.addWidget(button)
        self.note_search_edit = SearchInput("搜索标题、内容、标签或合集")
        self.note_search_edit.setFixedHeight(36)
        describe_field(
            self.note_search_edit,
            "搜索笔记",
            "搜索标题、内容、标签或合集，按回车执行搜索",
        )
        self.note_search_edit.textChanged.connect(self.reload)
        self.note_search_edit.returnPressed.connect(self._submit_note_search)
        search_row.addWidget(self.note_search_edit, stretch=1)
        library_root.addWidget(search_toolbar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.workspace = split
        split.setObjectName("NoteWorkspace")
        split.setChildrenCollapsible(False)
        split.setMinimumWidth(0)
        split.setHandleWidth(10)
        split.setContentsMargins(8, 8, 8, 8)

        space = CardFrame()
        space.setObjectName("NoteSpacePane")
        self.space_pane = space
        space.setMinimumWidth(200)
        space.setMaximumWidth(260)
        space.add_title("笔记空间")
        self.space_back_button = IconButton("chevron-left", "返回笔记列表")
        self.space_back_button.clicked.connect(self._show_narrow_content)
        self.space_back_button.hide()
        space.body.addWidget(self.space_back_button)
        view_label = QLabel("视图")
        view_label.setObjectName("MutedLabel")
        space.body.addWidget(view_label)
        self.status_filter = QComboBox()
        describe_field(self.status_filter, "笔记视图")
        for label, status in (
            ("正式笔记", "active"),
            ("待整理", "inbox"),
            ("归档", "archived"),
            ("回收站", "trashed"),
            ("全部笔记", None),
        ):
            self.status_filter.addItem(label, status)
        self.status_filter.currentIndexChanged.connect(self.reload)
        space.body.addWidget(self.status_filter)

        collection_header = QHBoxLayout()
        collection_label = QLabel("合集")
        collection_label.setObjectName("MutedLabel")
        collection_header.addWidget(collection_label)
        collection_header.addStretch(1)
        new_collection = ghost_button("+ 新建")
        new_collection.clicked.connect(self._create_collection)
        collection_header.addWidget(new_collection)
        space.body.addLayout(collection_header)
        self.collection_list = QListWidget()
        self.collection_list.setObjectName("NoteCollectionList")
        self.collection_list.setAccessibleName("笔记合集")
        self.collection_list.setUniformItemSizes(True)
        self.collection_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.collection_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.collection_list.setMouseTracking(True)
        self.collection_list.setItemDelegate(
            SoftItemDelegate(
                self.collection_list,
                radius=9,
                horizontal_margin=2,
                vertical_margin=2,
                minimum_height=38,
            )
        )
        self.collection_list.currentItemChanged.connect(self._select_collection)
        space.body.addWidget(self.collection_list, stretch=1)
        self.collection_list_hint = QLabel()
        self.collection_list_hint.setObjectName("MutedLabel")
        self.collection_list_hint.setWordWrap(True)
        self.collection_list_hint.hide()
        space.body.addWidget(self.collection_list_hint)
        split.addWidget(space)

        middle = CardFrame()
        middle.setObjectName("NoteLibraryPane")
        self.note_library_pane = middle
        middle.setMinimumWidth(320)
        self.space_toggle_button = ghost_button("空间与合集")
        self.space_toggle_button.clicked.connect(self._show_narrow_space)
        self.space_toggle_button.hide()
        middle.body.addWidget(self.space_toggle_button)
        list_header = QHBoxLayout()
        list_title = QLabel("笔记列表")
        list_title.setObjectName("SectionTitle")
        self.note_count_label = QLabel("0 篇")
        self.note_count_label.setObjectName("MutedLabel")
        list_header.addWidget(list_title)
        list_header.addStretch(1)
        list_header.addWidget(self.note_count_label)
        middle.body.addLayout(list_header)
        self.note_list_hint = QLabel("")
        self.note_list_hint.setObjectName("MutedLabel")
        self.note_list_hint.setWordWrap(True)
        self.note_list_hint.hide()
        middle.body.addWidget(self.note_list_hint)
        self.note_list = QListWidget()
        self.note_list.setObjectName("NoteList")
        self.note_list.setAccessibleName("笔记列表")
        self.note_list.setAccessibleDescription("使用方向键选择笔记")
        self.note_list.setUniformItemSizes(True)
        self.note_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.note_list.setWordWrap(False)
        self.note_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.note_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.note_list.setMouseTracking(True)
        self.note_list.setItemDelegate(
            SoftItemDelegate(
                self.note_list,
                radius=10,
                horizontal_margin=2,
                vertical_margin=3,
                minimum_height=58,
            )
        )
        self.note_list.currentItemChanged.connect(self._select_note)
        self.note_list.itemSelectionChanged.connect(self._update_bulk_actions)
        self.note_list.itemDoubleClicked.connect(self._open_selected_note_detail)
        self.note_list.verticalScrollBar().valueChanged.connect(
            self._load_more_notes_at_end
        )
        middle.body.addWidget(self.note_list, stretch=1)
        self.bulk_actions = QFrame()
        self.bulk_actions.setObjectName("ContextBar")
        bulk_row = QHBoxLayout(self.bulk_actions)
        bulk_row.setContentsMargins(8, 6, 8, 6)
        self.bulk_selection_label = QLabel()
        self.bulk_selection_label.setObjectName("MutedLabel")
        self.bulk_move_button = ghost_button("移动到合集")
        self.bulk_move_button.clicked.connect(self._move_selected_notes_to_collection)
        self.bulk_archive_button = ghost_button("归档")
        self.bulk_archive_button.clicked.connect(
            lambda: self._update_selected_notes_status("archived")
        )
        self.bulk_trash_button = danger_button("移入回收站")
        self.bulk_trash_button.clicked.connect(
            lambda: self._update_selected_notes_status("trashed")
        )
        bulk_row.addWidget(self.bulk_selection_label)
        bulk_row.addStretch(1)
        bulk_row.addWidget(self.bulk_move_button)
        bulk_row.addWidget(self.bulk_archive_button)
        bulk_row.addWidget(self.bulk_trash_button)
        self.bulk_actions.hide()
        middle.body.addWidget(self.bulk_actions)
        set_tab_order_chain(
            self.note_search_edit,
            self.ai_search_button,
            self.note_list,
        )
        split.addWidget(middle)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setSizes([220, 1000])
        library_root.addWidget(split, stretch=1)
        self.page_stack.addWidget(self.library_page)
        self.note_detail_page = self._build_detail()
        self.page_stack.addWidget(self.note_detail_page)
        self.manual_create_page = self._build_manual_create_page()
        self.ai_intake_page = self._build_ai_intake_page()
        self.page_stack.addWidget(self.manual_create_page)
        self.page_stack.addWidget(self.ai_intake_page)
        self.draft_preview_page: NoteDraftPreviewPage | None = None

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        if hasattr(self, "workspace"):
            self._set_narrow_layout(self.width() < 960)

    def _set_narrow_layout(self, narrow: bool) -> None:
        if self._narrow_layout == narrow:
            return
        self._narrow_layout = narrow
        self.space_toggle_button.setVisible(narrow)
        self.space_back_button.setVisible(narrow)
        if narrow:
            self._show_narrow_content()
        else:
            self._narrow_space_open = False
            self.space_pane.setMaximumWidth(260)
            self.space_pane.show()
            self.note_library_pane.show()
            self.workspace.setSizes([220, 1000])

    def _show_narrow_space(self) -> None:
        if not self._narrow_layout:
            return
        self._narrow_space_open = True
        self.space_pane.setMaximumWidth(16777215)
        self.space_pane.show()
        self.note_library_pane.hide()
        self.workspace.setSizes([max(320, self.width()), 0])

    def _show_narrow_content(self) -> None:
        if not self._narrow_layout:
            return
        self._narrow_space_open = False
        self.space_pane.hide()
        self.note_library_pane.show()
        self.workspace.setSizes([0, max(360, self.width())])

    def _build_manual_create_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        header = PageHeader("新建笔记", "先建立笔记信息，再进入内容编辑。")
        back = IconButton("chevron-left", "返回笔记库")
        back.clicked.connect(self._show_library)
        header.add_leading(back)
        layout.addWidget(header)
        form = CardFrame()
        form.add_title("笔记信息")
        self.new_note_title = QLineEdit()
        describe_field(self.new_note_title, "笔记标题")
        self.new_note_title.setPlaceholderText("笔记标题")
        self.new_note_summary = QTextEdit()
        describe_field(self.new_note_summary, "笔记摘要")
        self.new_note_summary.setPlaceholderText("摘要（可选）")
        self.new_note_summary.setFixedHeight(110)
        form.body.addWidget(self.new_note_title)
        form.body.addWidget(self.new_note_summary)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_note_create_button = ghost_button("取消")
        self.cancel_note_create_button.clicked.connect(self._show_library)
        self.create_note_button = primary_button("创建并编辑")
        self.create_note_button.clicked.connect(self._create_note)
        actions.addWidget(self.cancel_note_create_button)
        actions.addWidget(self.create_note_button)
        form.body.addLayout(actions)
        set_tab_order_chain(
            self.new_note_title,
            self.new_note_summary,
            self.cancel_note_create_button,
            self.create_note_button,
        )
        layout.addWidget(form)
        layout.addStretch(1)
        return page

    def _build_ai_intake_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        header = PageHeader("AI 图片录入笔记", "选择笔记图片，AI 会先整理为可确认的内容草稿。")
        back = IconButton("chevron-left", "返回笔记库")
        back.clicked.connect(self._show_library)
        header.add_leading(back)
        layout.addWidget(header)
        form = CardFrame()
        form.add_title("识别设置")
        source_row = QHBoxLayout()
        self.ai_source_path = QLineEdit()
        describe_field(self.ai_source_path, "笔记图片路径")
        self.ai_source_path.setReadOnly(True)
        choose = ghost_button("选择图片")
        choose.clicked.connect(self._choose_ai_source)
        source_row.addWidget(self.ai_source_path, stretch=1)
        source_row.addWidget(choose)
        form.body.addLayout(source_row)
        self.ai_classification_mode = QComboBox()
        describe_field(self.ai_classification_mode, "笔记分类方式")
        self.ai_classification_mode.addItem("自定义分类", "custom")
        self.ai_classification_mode.addItem("AI 识别并分组", "ai")
        form.body.addWidget(self.ai_classification_mode)
        self.ai_instruction = QTextEdit()
        describe_field(self.ai_instruction, "AI 识别补充要求")
        self.ai_instruction.setPlaceholderText("补充要求（可选）")
        self.ai_instruction.setFixedHeight(100)
        form.body.addWidget(self.ai_instruction)
        self.ai_intake_status = QLabel()
        self.ai_intake_status.setObjectName("PageHint")
        self.ai_intake_status.setWordWrap(True)
        form.body.addWidget(self.ai_intake_status)
        actions = QHBoxLayout()
        actions.addStretch(1)
        resume = ghost_button("继续草稿")
        resume.clicked.connect(self._resume_note_draft)
        self.ai_start_button = primary_button("开始识别")
        self.ai_start_button.clicked.connect(self._start_ai_extraction)
        actions.addWidget(resume)
        actions.addWidget(self.ai_start_button)
        form.body.addLayout(actions)
        layout.addWidget(form)
        layout.addStretch(1)
        return page

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().closeEvent(event)

    def _build_detail(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        detail_header = PageHeader("笔记详情", "笔记状态")
        self.note_title_label = detail_header.title
        self.note_status = detail_header.description
        self.detail_back_button = IconButton("chevron-left", "返回笔记库")
        self.detail_back_button.clicked.connect(self._return_to_library)
        detail_header.add_leading(self.detail_back_button)
        layout.addWidget(detail_header)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self._build_editor())
        self.mode_stack.addWidget(self._build_reader())
        self.mode_stack.setCurrentIndex(1)
        layout.addWidget(self.mode_stack, stretch=1)

        action_bar = QFrame()
        action_bar.setObjectName("ContextBar")
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(10, 8, 10, 8)
        actions.setSpacing(8)
        self.read_button = primary_button("完成编辑")
        self.read_button.clicked.connect(lambda: self._set_mode("read"))
        self.edit_button = primary_button("编辑笔记")
        self.edit_button.clicked.connect(lambda: self._set_mode("edit"))
        self.more_button = ghost_button("更多")
        bind_icon(self.more_button, "more-horizontal")
        self.more_menu = QMenu(self.more_button)
        self.more_menu.addAction("加入合集", self._edit_note_collections)
        self.more_menu.addAction("加入复习计划", self._request_review)
        self.more_button.clicked.connect(
            lambda: show_dropdown_menu(self.more_menu, self.more_button)
        )
        actions.addWidget(self.more_button)
        actions.addStretch(1)
        actions.addWidget(self.read_button)
        actions.addWidget(self.edit_button)
        layout.addWidget(action_bar)
        self.read_button.hide()
        self.detail_back_shortcut = QShortcut(QKeySequence("Alt+Left"), page)
        self.detail_back_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.detail_back_shortcut.activated.connect(self._return_to_library)
        return page

    def _request_review(self) -> None:
        if self._note is not None:
            self.add_to_review_requested.emit(self._note.id)

    def _build_editor(self) -> QWidget:
        editor = QWidget()
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        section_nav = QHBoxLayout()
        self.editor_content_button = primary_button("内容结构")
        self.editor_content_button.clicked.connect(
            lambda: self._set_editor_section("content")
        )
        self.editor_info_button = ghost_button("笔记信息")
        self.editor_info_button.clicked.connect(lambda: self._set_editor_section("info"))
        section_nav.addWidget(self.editor_content_button)
        section_nav.addWidget(self.editor_info_button)
        section_nav.addStretch(1)
        section_hint = QLabel("编辑模式")
        section_hint.setObjectName("MutedLabel")
        section_nav.addWidget(section_hint)
        layout.addLayout(section_nav)

        self.editor_section_stack = QStackedWidget()

        info = CardFrame()
        info.add_title("笔记信息")
        self.title_edit = QLineEdit()
        describe_field(self.title_edit, "笔记标题")
        self.title_edit.setPlaceholderText("笔记标题")
        self.summary_edit = QTextEdit()
        describe_field(self.summary_edit, "笔记摘要")
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

        body = QSplitter(Qt.Orientation.Horizontal)
        block_card = CardFrame()
        block_card.add_title("内容块")
        block_actions = QHBoxLayout()
        for block_type in ("heading", "concept", "text", "formula", "callout"):
            button = QPushButton(f"+ {_BLOCK_LABELS[block_type]}")
            button.clicked.connect(
                lambda _checked=False, value=block_type: self._add_block(value)
            )
            block_actions.addWidget(button)
        block_card.body.addLayout(block_actions)
        self.block_list = NoteBlockList()
        self.block_list.setObjectName("NoteBlockList")
        self.block_list.setAccessibleName("笔记内容块")
        self.block_list.setAccessibleDescription("使用方向键选择需要编辑的内容块")
        self.block_list.currentItemChanged.connect(self._select_block)
        self.block_list.blocks_reordered.connect(self._persist_block_order)
        block_card.body.addWidget(self.block_list, stretch=1)
        self.move_block_up_button = QPushButton("上移")
        self.move_block_up_button.clicked.connect(lambda: self._move_block(-1))
        self.move_block_down_button = QPushButton("下移")
        self.move_block_down_button.clicked.connect(lambda: self._move_block(1))
        block_card.body.addLayout(
            self._row(self.move_block_up_button, self.move_block_down_button)
        )
        body.addWidget(block_card)

        self.block_editor = CardFrame()
        self.block_editor.add_title("编辑内容块")
        self.block_type_label = self.block_editor.add_hint("请选择一个内容块")
        self.block_content = QTextEdit()
        describe_field(self.block_content, "当前内容块内容")
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
        self.editor_section_stack.addWidget(body)
        self.editor_section_stack.addWidget(info)
        layout.addWidget(self.editor_section_stack, stretch=1)
        return editor

    def _build_reader(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.reader = MathContentView()
        self.reader.setObjectName("NoteReader")
        self.reading_canvas = ReadingCanvas(self.reader, maximum_width=920)
        layout.addWidget(self.reading_canvas)
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
            self._reload_collections()
            self._notes = self.notes.list_note_summaries(
                status=self.status_filter.currentData()
            )
            if self._collection_filter_id == "__unfiled__":
                assigned_ids = set().union(*self._collection_note_ids.values()) if self._collection_note_ids else set()
                self._notes = [
                    note for note in self._notes if note.id not in assigned_ids
                ]
            elif self._collection_filter_id:
                collection_ids = self._collection_note_ids.get(
                    self._collection_filter_id, set()
                )
                self._notes = [
                    note for note in self._notes if note.id in collection_ids
                ]
            query = self.note_search_edit.text().strip()
            if query:
                if self.ai_search_button.isChecked() and self._note_ai_search_ids is not None:
                    hit_ids = self._note_ai_search_ids
                else:
                    hits = self.note_search.search_notes(
                        query,
                        statuses=(self.status_filter.currentData(),)
                        if self.status_filter.currentData()
                        else ("active", "inbox", "archived", "trashed"),
                    )
                    hit_ids = {str(hit["entity_id"]) for hit in hits}
                self._notes = [note for note in self._notes if note.id in hit_ids]
        except DomainError as exc:
            self.note_list_hint.setText(f"笔记加载失败：{exc}")
            self.note_list_hint.setObjectName("DangerLabel")
            self.note_list_hint.show()
            self.status_message.emit(str(exc))
            return
        self._loading = True
        self.note_list_hint.setObjectName("MutedLabel")
        self.note_list_hint.setText(self._empty_note_message(query))
        self.note_list_hint.setVisible(not self._notes)
        self.note_count_label.setText(f"{len(self._notes)} 篇")
        selected_row = -1
        with deferred_view_updates(self.note_list):
            self.note_list.clear()
            self._note_visible_count = 0
            current_index = next(
                (
                    index
                    for index, note in enumerate(self._notes)
                    if note.id == current_id
                ),
                -1,
            )
            target_count = max(
                _NOTE_LIST_BATCH_SIZE,
                current_index + 1 if current_index >= 0 else 0,
            )
            self._append_note_batch(target_count)
            selected_row = current_index
        self._loading = False
        if selected_row >= 0:
            self.note_list.setCurrentRow(selected_row)
        elif self.note_list.count():
            self.note_list.setCurrentRow(0)
        else:
            self._note = None
            self._block = None
        self._update_bulk_actions()

    def _append_note_batch(self, target_count: int | None = None) -> None:
        if self._note_visible_count >= len(self._notes):
            return
        end = min(
            len(self._notes),
            target_count
            if target_count is not None
            else self._note_visible_count + _NOTE_LIST_BATCH_SIZE,
        )
        for note in self._notes[self._note_visible_count : end]:
            title = note.title or "未命名笔记"
            preview = note.summary.strip()
            status = _NOTE_STATUS_LABELS.get(note.status, note.status)
            detail = f"{status} · {preview or '尚未添加内容'}"
            item = QListWidgetItem(f"{title}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, note.id)
            item.setToolTip(f"{title}\n{detail}")
            item.setData(
                Qt.ItemDataRole.AccessibleDescriptionRole,
                f"{status}笔记；双击打开独立详情页",
            )
            self.note_list.addItem(item)
        self._note_visible_count = end

    def _load_more_notes_at_end(self, value: int) -> None:
        if self._loading or self._note_visible_count >= len(self._notes):
            return
        scroll = self.note_list.verticalScrollBar()
        if value >= scroll.maximum():
            with deferred_view_updates(self.note_list):
                self._append_note_batch()

    def _reload_collections(self) -> None:
        collections = self.notes.list_collections()
        self._collection_note_ids = {
            collection.id: {note.id for note in collection.notes}
            for collection in collections
        }
        selected = self._collection_filter_id
        self.collection_list.blockSignals(True)
        with deferred_view_updates(self.collection_list):
            self.collection_list.clear()
            all_item = QListWidgetItem("全部笔记")
            all_item.setData(Qt.ItemDataRole.UserRole, None)
            self.collection_list.addItem(all_item)
            unfiled_item = QListWidgetItem("未归入合集")
            unfiled_item.setData(Qt.ItemDataRole.UserRole, "__unfiled__")
            self.collection_list.addItem(unfiled_item)
            selected_row = 0
            for collection in collections:
                item = QListWidgetItem(
                    f"{collection.title}  ·  {len(collection.notes)}"
                )
                item.setData(Qt.ItemDataRole.UserRole, collection.id)
                self.collection_list.addItem(item)
                if collection.id == selected:
                    selected_row = self.collection_list.count() - 1
        if selected == "__unfiled__":
            selected_row = 1
        elif selected and selected not in self._collection_note_ids:
            self._collection_filter_id = None
        self.collection_list.setCurrentRow(selected_row)
        self.collection_list.blockSignals(False)
        self.collection_list_hint.setText(
            "暂无自定义合集；可从这里新建，或在中栏浏览未归入合集的笔记。"
        )
        self.collection_list_hint.setVisible(not collections)

    def _empty_note_message(self, query: str) -> str:
        if query:
            return "没有符合当前搜索条件的笔记；可调整关键词或搜索方式。"
        if self._collection_filter_id == "__unfiled__":
            return "未归入合集的笔记为空；可切换合集或新建笔记。"
        if self._collection_filter_id:
            return "当前合集暂无笔记；可切换合集或新建笔记。"
        status = self.status_filter.currentData()
        return {
            "archived": "归档中暂无笔记；可切换视图或新建笔记。",
            "trashed": "回收站为空；移入回收站的笔记会显示在这里。",
            "inbox": "待整理笔记为空；可新建笔记或切换视图。",
        }.get(status, "当前筛选下暂无笔记；可切换视图、合集或新建笔记。")

    def _select_collection(
        self, current: QListWidgetItem | None, _previous=None
    ) -> None:
        if self._loading or current is None:
            return
        collection_id = current.data(Qt.ItemDataRole.UserRole)
        self._collection_filter_id = str(collection_id) if collection_id else None
        self.reload()
        self._show_narrow_content()

    def _create_collection(self) -> None:
        title, accepted = QInputDialog.getText(self, "新建合集", "合集名称：")
        if not accepted:
            return
        try:
            collection = self.notes.create_collection(title)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._collection_filter_id = collection.id
        self.reload()
        self.status_message.emit(f"已新建合集“{collection.title}”")

    def _on_note_search_mode_changed(self, _checked: bool = False) -> None:
        if self.ai_search_button.isChecked():
            self.note_search_edit.setPlaceholderText(
                "描述想找的笔记，例如：包含等价无穷小与泰勒展开的整理"
            )
        else:
            self.note_search_edit.setPlaceholderText("搜索标题、内容、标签或合集")
        self._submit_note_search()

    def _submit_note_search(self) -> None:
        if not self.ai_search_button.isChecked():
            self._note_ai_search_ids = None
            self.reload()
            return
        query = self.note_search_edit.text().strip()
        if not query or (self.note_search_worker and self.note_search_worker.isRunning()):
            return
        statuses = (self.status_filter.currentData(),) if self.status_filter.currentData() else ("active", "inbox", "archived", "trashed")
        worker = NoteAiSearchWorker(self.note_ai_search, query=query, statuses=statuses, parent=self)
        self.note_search_worker = worker
        worker.finished_ok.connect(self._on_note_ai_search_done)
        worker.failed.connect(lambda error: self.status_message.emit(f"笔记 AI 搜索失败：{error}"))
        worker.finished.connect(lambda: setattr(self, "note_search_worker", None))
        worker.start()

    def _on_note_ai_search_done(self, matches: object) -> None:
        self._note_ai_search_ids = {item.note_id for item in matches}
        self.reload()

    def _selected_note_ids(self) -> list[str]:
        return [
            str(item.data(Qt.ItemDataRole.UserRole))
            for item in self.note_list.selectedItems()
        ]

    def _update_bulk_actions(self) -> None:
        if not hasattr(self, "bulk_actions"):
            return
        selected = self._selected_note_ids()
        visible = len(selected) > 1
        self.bulk_actions.setVisible(visible)
        if not visible:
            return
        selected_notes = [note for note in self._notes if note.id in selected]
        editable = bool(selected_notes) and all(
            note.status != "trashed" for note in selected_notes
        )
        self.bulk_selection_label.setText(f"已选择 {len(selected)} 篇")
        for button in (
            self.bulk_move_button,
            self.bulk_archive_button,
            self.bulk_trash_button,
        ):
            button.setEnabled(editable)

    def _move_selected_notes_to_collection(self) -> None:
        note_ids = self._selected_note_ids()
        collections = self.notes.list_collections()
        if not collections:
            self.status_message.emit("请先创建目标合集")
            return
        labels = [collection.title for collection in collections]
        label, accepted = QInputDialog.getItem(
            self, "移动笔记", "移动到合集：", labels, 0, False
        )
        if not accepted:
            return
        collection = collections[labels.index(label)]
        try:
            self.notes.move_notes_to_collection(note_ids, collection.id)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._collection_filter_id = collection.id
        self.reload(select_note_id=note_ids[0])
        self.status_message.emit(f"已将 {len(note_ids)} 篇笔记移动到“{collection.title}”")
        self.notes_changed.emit()

    def _update_selected_notes_status(self, status: str) -> None:
        note_ids = self._selected_note_ids()
        try:
            self.notes.update_notes_status(note_ids, status)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.reload()
        self.status_message.emit(
            f"已将 {len(note_ids)} 篇笔记移至{_STATUS_LABELS[status]}"
        )
        self.notes_changed.emit()

    def _select_note(self, current: QListWidgetItem | None, _previous=None) -> None:
        if self._loading or current is None:
            return
        note = self.notes.get_note(str(current.data(Qt.ItemDataRole.UserRole)))
        if note is None:
            self.reload()
            return
        changed_note = self._note is None or self._note.id != note.id
        self._note = note
        self._block = None
        if changed_note:
            self._set_mode("read")
        self._render_note()

    def _open_selected_note_detail(self, item: QListWidgetItem) -> None:
        note_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._capture_library_state(note_id)
        note = self.notes.get_note(note_id)
        if note is None:
            self.reload()
            return
        self._note = note
        self._block = None
        self._set_mode("read")
        self._render_note()
        self.page_stack.setCurrentWidget(self.note_detail_page)

    def _return_to_library(self) -> None:
        if self._has_unsaved_note_changes():
            choice = QMessageBox.question(
                self,
                "未保存的笔记修改",
                "当前笔记有未保存的修改。要放弃这些修改并返回笔记库吗？",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Discard:
                return
        self._restore_library_state()
        self.library_shown.emit()

    def _capture_library_state(self, note_id: str | None = None) -> None:
        self._library_state = {
            "collection_id": self._collection_filter_id,
            "status": self.status_filter.currentData(),
            "query": self.note_search_edit.text(),
            "search_mode": "ai" if self.ai_search_button.isChecked() else "local",
            "note_id": note_id or (self._note.id if self._note else None),
            "scroll": self.note_list.verticalScrollBar().value(),
            "collection_scroll": self.collection_list.verticalScrollBar().value(),
        }

    def _restore_library_state(self, select_note_id: str | None = None) -> None:
        state = self._library_state or {}
        self._collection_filter_id = state.get("collection_id")  # type: ignore[assignment]
        status = state.get("status")
        index = self.status_filter.findData(status)
        if index >= 0:
            self.status_filter.setCurrentIndex(index)
        search_mode = state.get("search_mode")
        self.ai_search_button.setChecked(search_mode == "ai")
        self.local_search_button.setChecked(search_mode != "ai")
        self.note_search_edit.setText(str(state.get("query", "")))
        self.page_stack.setCurrentWidget(self.library_page)
        self.reload(
            select_note_id=select_note_id
            or str(state.get("note_id") or "")
            or None
        )
        scroll = int(state.get("scroll", 0))
        collection_scroll = int(state.get("collection_scroll", 0))
        QTimer.singleShot(
            0,
            lambda: self.note_list.verticalScrollBar().setValue(scroll),
        )
        QTimer.singleShot(
            0,
            lambda: self.collection_list.verticalScrollBar().setValue(collection_scroll),
        )

    def _has_unsaved_note_changes(self) -> bool:
        if self._note is None or self.mode_stack.currentIndex() != 0:
            return False
        if (
            self.title_edit.text() != self._note.title
            or self.summary_edit.toPlainText() != self._note.summary
        ):
            return True
        if self._block is None:
            return False
        current = (
            self._block.content_latex
            if self._block.block_type == "formula"
            else self._block.content_markdown
        )
        return self.block_content.toPlainText() != current

    def _render_note(self) -> None:
        note = self._note
        if note is None:
            return
        self._loading = True
        self.title_edit.setText(note.title)
        self.summary_edit.setPlainText(note.summary)
        self.note_title_label.setText(note.title or "未命名笔记")
        self.note_status.setText(
            f"{_STATUS_LABELS[note.status]} · {len(note.blocks)} 个内容块 · 已保存到本地"
        )
        editable = note.status != "trashed"
        self.edit_button.setEnabled(editable)
        self.title_edit.setReadOnly(not editable)
        self.summary_edit.setReadOnly(not editable)
        self.save_note_button.setEnabled(editable)
        self.trash_button.setVisible(editable)
        self.restore_button.setVisible(note.status == "trashed")
        self.block_list.clear()
        for index, block in enumerate(note.blocks, start=1):
            value = block.content_latex if block.block_type == "formula" else block.content_markdown
            preview = value.replace("\n", " ")[:46] or "（空）"
            item = QListWidgetItem(f":: {index}. {_BLOCK_LABELS[block.block_type]} · {preview}")
            item.setData(Qt.ItemDataRole.UserRole, block.id)
            item.setToolTip("从左侧 :: 手柄拖动以调整顺序")
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
        self.block_list.setDragEnabled(editable)
        self.block_list.setAcceptDrops(editable)
        self.block_content.setReadOnly(not editable)
        self.save_block_button.setEnabled(editable)
        self.delete_block_button.setEnabled(editable)
        index = self.block_list.currentRow()
        self.move_block_up_button.setEnabled(editable and index > 0)
        self.move_block_down_button.setEnabled(
            editable and index >= 0 and index < self.block_list.count() - 1
        )

    def _clear_block_editor(self) -> None:
        self._block = None
        self.block_type_label.setText("请选择一个内容块")
        self.block_content.clear()
        self.block_content.setReadOnly(True)
        self.save_block_button.setEnabled(False)
        self.delete_block_button.setEnabled(False)
        self.move_block_up_button.setEnabled(False)
        self.move_block_down_button.setEnabled(False)

    def _show_library(self, *_args, select_note_id: str | None = None) -> None:
        self._restore_library_state(select_note_id=select_note_id)
        self.library_shown.emit()

    def _show_new_note_menu(self) -> None:
        """新建笔记下拉：AI 图片录入 / 手动录入。"""
        menu = QMenu(self)
        ai = menu.addAction("AI 图片录入")
        ai.triggered.connect(self._show_ai_intake)
        manual = menu.addAction("手动录入")
        manual.triggered.connect(self._show_manual_create)
        show_dropdown_menu(menu, self.new_note_button)

    def _show_manual_create(self) -> None:
        if self.page_stack.currentWidget() is self.library_page:
            self._capture_library_state()
        self.new_note_title.clear()
        self.new_note_summary.clear()
        self.page_stack.setCurrentWidget(self.manual_create_page)
        self.new_note_title.setFocus()

    def _show_ai_intake(self) -> None:
        if self.page_stack.currentWidget() is self.library_page:
            self._capture_library_state()
        self.ai_intake_status.setText("")
        self.page_stack.setCurrentWidget(self.ai_intake_page)

    def _choose_ai_source(self) -> None:
        path_text, _ = QFileDialog.getOpenFileName(
            self, "选择笔记图片", "", "图片 (*.jpg *.jpeg *.png *.webp)"
        )
        if path_text:
            self.ai_source_path.setText(path_text)

    def _create_note(self) -> None:
        try:
            note = self.notes.create_note(
                title=self.new_note_title.text() or "未命名笔记",
                summary=self.new_note_summary.toPlainText(),
                status="active",
            )
            if self._collection_filter_id not in (None, "__unfiled__"):
                note = self.notes.set_note_collections(
                    note.id, [self._collection_filter_id]
                )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._capture_library_state(note.id)
        self.reload(select_note_id=note.id)
        self.page_stack.setCurrentWidget(self.note_detail_page)
        self._set_mode("edit")
        self.status_message.emit("已新建笔记，可以开始添加内容块")
        self.notes_changed.emit()

    def _edit_note_collections(self) -> None:
        if self._note is None:
            return
        collections = self.notes.list_collections()
        if not collections:
            title, accepted = QInputDialog.getText(self, "新建合集", "合集标题：")
            if not accepted:
                return
            try:
                collection = self.notes.create_collection(title)
            except DomainError as exc:
                self.status_message.emit(str(exc))
                return
            collections = [collection]
        dialog = QDialog(self)
        dialog.setWindowTitle("笔记合集")
        dialog.resize(360, 320)
        root = QVBoxLayout(dialog)
        root.addWidget(QLabel("选择当前笔记所属的个人合集"))
        choices = QListWidget()
        selected_ids = {collection.id for collection in self._note.collections}
        for collection in collections:
            item = QListWidgetItem(collection.title)
            item.setData(Qt.ItemDataRole.UserRole, collection.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if collection.id in selected_ids
                else Qt.CheckState.Unchecked
            )
            choices.addItem(item)
        root.addWidget(choices, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        collection_ids = [
            str(choices.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(choices.count())
            if choices.item(index).checkState() == Qt.CheckState.Checked
        ]
        try:
            note = self.notes.set_note_collections(self._note.id, collection_ids)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.reload(select_note_id=note.id)
        self.status_message.emit("笔记合集已更新")

    def _start_ai_extraction(self) -> None:
        image_path = Path(self.ai_source_path.text())
        if not image_path.is_file():
            self.ai_intake_status.setText("请选择一张可读取的笔记图片。")
            return
        instruction = self.ai_instruction.toPlainText()
        classification_mode = str(self.ai_classification_mode.currentData())
        try:
            intake = self.note_intake.start_session(
                [image_path],
                classification_mode=classification_mode,
                user_instruction=instruction,
            )
        except DomainError as exc:
            self.ai_intake_status.setText(str(exc))
            return
        self._start_note_worker(intake, image_path)

    def _start_note_worker(self, intake: NoteIntakeSession, image_path: Path) -> None:
        try:
            self.note_intake.mark_processing(intake.id)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        job = self.ai_coordinator.ai.create_background_job(
            domain="note_intake",
            context_id=intake.id,
            job_type="note_extract",
            config={"classification_mode": intake.classification_mode},
        )
        self.note_job_id = job.id
        self.ai_coordinator.enqueue(job.id)
        self.ai_intake_status.setText(
            "笔记已提交到后台队列，可以继续录入其他内容。"
        )
        self.ai_source_path.clear()
        self.status_message.emit("AI 笔记录入任务已提交")

    def _run_note_job(self, job_id: str, emit_progress, should_cancel) -> dict[str, str]:
        job = self.ai_coordinator.ai.get_job(job_id)
        if job is None:
            raise DomainError("笔记录入任务不存在")
        intake = self.note_intake.get_session(job.context_id)
        if intake is None or not intake.assets:
            raise DomainError("笔记录入草稿或来源图片不存在")
        self.note_intake.mark_processing(intake.id)
        image_path = self.note_intake.resolve_source_path(intake.assets[0])
        chunks: list[str] = []
        last_flush = [perf_counter()]

        def flush(*, force: bool = False) -> None:
            length = sum(len(value) for value in chunks)
            if not chunks or (
                not force and length < 128 and perf_counter() - last_flush[0] < 0.35
            ):
                return
            text = "".join(chunks)
            chunks.clear()
            last_flush[0] = perf_counter()
            self.ai_coordinator.ai.append_job_event(
                job_id,
                "text_delta",
                text_value=text,
                append_response=True,
            )

        def receive(delta: str) -> None:
            if should_cancel() or not delta:
                return
            chunks.append(delta)
            emit_progress(
                {"stage": "streaming", "label": "正在接收 AI 回复", "text_delta": delta}
            )
            flush()

        try:
            draft = self.note_ai.extract_from_image(
                image_path,
                instruction=intake.user_instruction,
                classification_mode=intake.classification_mode,
                on_text_delta=receive,
                provider_name=job.provider,
                model=job.model,
            )
            flush(force=True)
            if should_cancel():
                raise DomainError("笔记录入任务已取消")
            self.note_intake.save_grouped_draft(intake.id, draft)
            return {"session_id": intake.id}
        except Exception as exc:
            flush(force=True)
            self.note_intake.mark_failed(intake.id, str(exc))
            raise

    def _on_note_job_finished(self, job_id: str) -> None:
        job = self.ai_coordinator.ai.get_job(job_id)
        if job is None or job.domain != "note_intake":
            return
        if job_id == self.note_job_id:
            self.ai_intake_status.setText("AI 笔记已生成，可从待审核队列继续。")
        self.status_message.emit("一个 AI 笔记草稿已进入待审核队列")

    def _on_note_job_failed(self, job_id: str, message: str) -> None:
        job = self.ai_coordinator.ai.get_job(job_id)
        if job is None or job.domain != "note_intake":
            return
        if job_id == self.note_job_id:
            self.ai_intake_status.setText(f"识别失败：{message}")
        self.status_message.emit(f"AI 笔记录入失败：{message}")

    def _on_ai_extraction_ready(self, payload: object) -> None:
        session_id, draft = payload
        if not isinstance(session_id, str) or not isinstance(draft, NoteExtractionDraft):
            self.status_message.emit("AI 笔记返回格式无效")
            return
        try:
            intake = self.note_intake.save_grouped_draft(session_id, draft)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._show_draft_preview(intake)

    def _on_ai_extraction_failed(self, payload: object) -> None:
        session_id, message = payload
        if isinstance(session_id, str):
            try:
                self.note_intake.mark_failed(session_id, str(message))
            except DomainError:
                pass
        self.ai_intake_status.setText(f"识别失败：{message}")
        self.status_message.emit(f"AI 笔记录入失败：{message}")

    def _on_ai_worker_finished(self) -> None:
        self.ai_start_button.setEnabled(True)

    def _resume_note_draft(self) -> None:
        if self.page_stack.currentWidget() is not self.ai_intake_page:
            self._show_ai_intake()
        intake = self.note_intake.latest_resumable_session()
        if intake is None:
            self.ai_intake_status.setText("没有可继续的笔记草稿。")
            return
        if intake.status == "review":
            self._show_draft_preview(intake)
            return
        if intake.status == "processing":
            self.ai_intake_status.setText("该笔记草稿仍在处理中。")
            return
        asset = intake.assets[0] if intake.assets else None
        if asset is None:
            self.status_message.emit("笔记草稿缺少来源图片")
            return
        source_path = self.note_intake.resolve_source_path(asset)
        if not source_path.is_file():
            self.status_message.emit("笔记草稿的来源图片已丢失，请从备份恢复")
            return
        self._start_note_worker(intake, source_path)

    def _draft_preview_back(self) -> None:
        """返回：从任务队列进入时直接回任务列表，否则回到 AI 录入页。"""
        if self._from_task_queue:
            self._from_task_queue = False
            self.library_shown.emit()
            return
        self._show_ai_intake()

    def _show_draft_preview(self, intake: NoteIntakeSession) -> None:
        if self.draft_preview_page is not None:
            self.page_stack.removeWidget(self.draft_preview_page)
            self.draft_preview_page.deleteLater()
        page = NoteDraftPreviewPage(intake, self.note_intake, self)
        page.back_requested.connect(self._draft_preview_back)
        page.confirmed.connect(self._finish_draft_confirmation)
        self.draft_preview_page = page
        self.page_stack.addWidget(page)
        self.page_stack.setCurrentWidget(page)

    def _finish_draft_confirmation(self, note_ids: tuple) -> None:
        ids = tuple(str(note_id) for note_id in note_ids)
        self._show_library(select_note_id=ids[0] if ids else None)
        self.notes_changed.emit()
        self.status_message.emit(f"已按分类组入库 {len(ids)} 篇笔记")

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
        if status == "trashed":
            self.reload()
        else:
            self.status_filter.setCurrentIndex(self.status_filter.findData(note.status))
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
        self._persist_block_order(ids, selected_block_id=self._block.id)

    def _persist_block_order(
        self, block_ids: list[str], *, selected_block_id: str | None = None
    ) -> None:
        """Persist a complete same-note order; reload restores the DB order on failure."""
        if self._note is None:
            return
        selected_id = selected_block_id or (
            self._block.id if self._block is not None else None
        )
        try:
            self.notes.reorder_blocks(self._note.id, block_ids)
        except DomainError as exc:
            self.reload(select_note_id=self._note.id)
            self.status_message.emit(str(exc))
            return
        self.reload(select_note_id=self._note.id)
        if selected_id is not None:
            for row in range(self.block_list.count()):
                item = self.block_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == selected_id:
                    self.block_list.setCurrentItem(item)
                    break
        self.notes_changed.emit()

    def _set_mode(self, mode: str) -> None:
        reading = mode == "read"
        self.mode_stack.setCurrentIndex(1 if reading else 0)
        self.read_button.setVisible(not reading)
        self.edit_button.setVisible(reading)
        self.more_button.setVisible(reading)
        if not reading:
            self._set_editor_section("content")
        if mode == "read":
            self._render_reader()

    def _set_editor_section(self, section: str) -> None:
        info = section == "info"
        self.editor_section_stack.setCurrentIndex(1 if info else 0)
        self.editor_info_button.setObjectName(
            "PrimaryButton" if info else "GhostButton"
        )
        self.editor_content_button.setObjectName(
            "GhostButton" if info else "PrimaryButton"
        )
        for button in (self.editor_info_button, self.editor_content_button):
            button.style().unpolish(button)
            button.style().polish(button)

    def _render_reader(self) -> None:
        note = self._note
        if note is None:
            self.reader.set_note({}, blocks=())
            return
        self.reader.set_note(
            {"title": note.title, "summary": note.summary},
            blocks=(
                {
                    "block_type": block.block_type,
                    "content_markdown": block.content_markdown,
                    "content_latex": block.content_latex,
                    "source_region": self._decode_source_region(
                        block.source_region_json
                    ),
                }
                for block in note.blocks
            ),
            tag_names=(tag.name for tag in note.tags),
        )

    @staticmethod
    def _decode_source_region(value: str) -> dict[str, float]:
        try:
            return normalize_region(json.loads(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
