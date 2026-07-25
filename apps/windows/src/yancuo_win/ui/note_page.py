"""In-shell note library, reader and block editor."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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
from yancuo_win.application.note_ai_service import (
    NoteAiService,
    NoteBlockDraft,
    NoteExtractionDraft,
)
from yancuo_win.application.note_intake_service import NoteIntakeService
from yancuo_win.application.services import AppServices
from yancuo_win.application.note_service import NoteService
from yancuo_win.application.note_ai_search_service import NoteAiSearchService
from yancuo_win.application.unified_search_service import UnifiedSearchIndexService
from yancuo_win.data.models import NoteBlock, NoteDocument, NoteIntakeSession
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.note_worker import NoteExtractionWorker
from yancuo_win.tasks.note_search_worker import NoteAiSearchWorker
from yancuo_win.ui.image_viewer import ImageViewerDialog
from yancuo_win.ui.math_content import MathContentView
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
    "concept": "概念",
    "formula": "公式",
    "callout": "提示",
    "image": "图片",
}


class NoteDraftGroupTree(QTreeWidget):
    """Tree that translates a block drop into a persistent draft operation."""

    block_dropped = Signal(str, str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
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


class NoteDraftPreviewDialog(QDialog):
    """Edit recoverable classification rows while preserving draft blocks."""

    def __init__(
        self, intake: NoteIntakeSession, note_intake: NoteIntakeService, parent=None
    ) -> None:
        super().__init__(parent)
        self.note_intake = note_intake
        self.catalog = AppServices(note_intake.runtime)
        self.intake = intake
        self.confirmed_note_ids: tuple[str, ...] = ()
        self._refreshing_groups = False
        self.setWindowTitle("AI 笔记草稿")
        self.resize(680, 520)
        root = QVBoxLayout(self)
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
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        confirm = buttons.addButton("确认入库", QDialogButtonBox.ButtonRole.AcceptRole)
        confirm.clicked.connect(self._confirm_groups)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
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
        self.accept()

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "无法更新分类草稿", message)


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
        self._original_path: Path | None = None
        self._loading = False
        self.note_ai = NoteAiService(notes.runtime)
        self.note_intake = NoteIntakeService(notes.runtime)
        self.note_search = UnifiedSearchIndexService(notes.runtime)
        self.note_ai_search = NoteAiSearchService(notes.runtime)
        self.note_search_worker: NoteAiSearchWorker | None = None
        self._note_ai_search_ids: set[str] | None = None
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
        self.resume_draft_button = ghost_button("继续草稿")
        self.resume_draft_button.clicked.connect(self._resume_note_draft)
        header.addWidget(self.resume_draft_button)
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
        self.note_search_edit = QLineEdit()
        self.note_search_edit.setObjectName("NoteSearchEdit")
        self.note_search_edit.setPlaceholderText("离线搜索标题、内容、标签或合集…")
        self.note_search_edit.textChanged.connect(self.reload)
        self.note_search_edit.returnPressed.connect(self._submit_note_search)
        left.body.addWidget(self.note_search_edit)
        self.note_search_mode = QComboBox()
        self.note_search_mode.addItem("普通搜索", "local")
        self.note_search_mode.addItem("AI 搜索", "ai")
        self.note_search_mode.currentIndexChanged.connect(self._submit_note_search)
        left.body.addWidget(self.note_search_mode)
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
        self.original_button = ghost_button("查看原图")
        self.original_button.setToolTip("按需打开录入时保存的不可变原图")
        self.original_button.clicked.connect(self._open_original)
        self.read_button = ghost_button("阅读预览")
        self.read_button.clicked.connect(lambda: self._set_mode("read"))
        self.edit_button = primary_button("编辑内容")
        self.edit_button.clicked.connect(lambda: self._set_mode("edit"))
        self.collections_button = ghost_button("加入合集")
        self.collections_button.clicked.connect(self._edit_note_collections)
        header.addWidget(self.original_button)
        header.addWidget(self.collections_button)
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
        for block_type in ("heading", "concept", "text", "formula", "callout"):
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
        self.reader = MathContentView()
        self.reader.setObjectName("NoteReader")
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
            query = self.note_search_edit.text().strip()
            if query:
                if self.note_search_mode.currentData() == "ai" and self._note_ai_search_ids is not None:
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

    def _submit_note_search(self) -> None:
        if self.note_search_mode.currentData() != "ai":
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
        original_asset = next(
            (asset for asset in note.assets if asset.role == "original"),
            None,
        )
        self._original_path = (
            self.note_ai.store.resolve(original_asset.relative_path)
            if original_asset is not None
            else None
        )
        has_original = original_asset is not None
        original_available = bool(
            self._original_path is not None and self._original_path.is_file()
        )
        self.original_button.setVisible(has_original)
        self.original_button.setEnabled(original_available)
        self.original_button.setToolTip(
            "按需打开录入时保存的不可变原图"
            if original_available
            else "原图文件已丢失，请从备份恢复"
        )
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
        if self.note_worker and self.note_worker.isRunning():
            self.status_message.emit("AI 笔记录入正在处理中，请稍候")
            return
        dialog = NoteIntakeSetupDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if values is None:
            self.status_message.emit("请选择一张可读取的笔记图片")
            return
        image_path, instruction, classification_mode = values
        try:
            intake = self.note_intake.start_session(
                [image_path],
                classification_mode=classification_mode,
                user_instruction=instruction,
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._start_note_worker(intake, image_path)

    def _start_note_worker(self, intake: NoteIntakeSession, image_path: Path) -> None:
        try:
            self.note_intake.mark_processing(intake.id)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.status_message.emit("正在整理笔记图片…")
        self.note_worker = NoteExtractionWorker(
            self.note_ai,
            intake.id,
            image_path,
            intake.user_instruction,
            intake.classification_mode,
            self,
        )
        self.note_worker.finished_ok.connect(self._on_ai_extraction_ready)
        self.note_worker.failed.connect(self._on_ai_extraction_failed)
        self.note_worker.finished.connect(self._on_ai_worker_finished)
        self.note_worker.start()

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
        self.status_message.emit(f"AI 笔记录入失败：{message}")

    def _on_ai_worker_finished(self) -> None:
        self.note_worker = None

    def _resume_note_draft(self) -> None:
        if self.note_worker and self.note_worker.isRunning():
            self.status_message.emit("AI 笔记录入正在处理中，请稍候")
            return
        intake = self.note_intake.latest_resumable_session()
        if intake is None:
            self.status_message.emit("没有可继续的笔记草稿")
            return
        if intake.status == "review":
            self._show_draft_preview(intake)
            return
        if intake.status == "processing":
            self.status_message.emit("该笔记草稿仍在处理中")
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

    def _show_draft_preview(self, intake: NoteIntakeSession) -> None:
        dialog = NoteDraftPreviewDialog(intake, self.note_intake, self)
        dialog.exec()
        if dialog.confirmed_note_ids:
            self.reload(select_note_id=dialog.confirmed_note_ids[0])
            self.notes_changed.emit()
            self.status_message.emit(f"已按分类组入库 {len(dialog.confirmed_note_ids)} 篇笔记")
        else:
            self.status_message.emit("AI 笔记草稿已保存，分类确认后再入库")

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

    def _open_original(self) -> None:
        if self._original_path is None or not self._original_path.is_file():
            self.status_message.emit("原图文件不存在，请从备份恢复")
            return
        pixmap = QPixmap(str(self._original_path))
        if pixmap.isNull():
            self.status_message.emit("原图格式无法读取")
            return
        regions = (
            self._decode_source_region(block.source_region_json)
            for block in (self._note.blocks if self._note is not None else ())
        )
        ImageViewerDialog(
            pixmap,
            self,
            source_regions=(region for region in regions if region),
        ).exec()

    @staticmethod
    def _decode_source_region(value: str) -> dict[str, float]:
        try:
            return normalize_region(json.loads(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
