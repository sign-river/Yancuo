"""Three-pane review-plan builder, separate from the review dashboard."""

from __future__ import annotations

from collections import Counter

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.note_service import NoteService
from yancuo_win.application.services import AppServices
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.widgets import (
    ConfirmDialog,
    PageHeader,
    SearchInput,
    StatusTag,
    ghost_button,
    primary_button,
)


class ReviewPlanBuilder(QWidget):
    """Browse, select, and package one content type into a review plan."""

    back_requested = Signal()
    plan_created = Signal(str)
    status_message = Signal(str)

    def __init__(self, services: AppServices, notes: NoteService | None, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self.notes = notes
        self.content_type = "problem"
        self._source_view = "list"
        self._selected_chapter_id: str | None = None
        self._narrow_layout = False
        self._showing_draft = False
        self._build()
        self.reload()

    def _build(self) -> None:
        self.setObjectName("PageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        back = ghost_button("返回复习")
        back.clicked.connect(self.back_requested)
        self.problem_mode = QPushButton("题目复习")
        self.problem_mode.setObjectName("SegmentButton")
        self.problem_mode.setCheckable(True)
        self.problem_mode.clicked.connect(lambda: self._set_content_type("problem"))
        self.note_mode = QPushButton("笔记复习")
        self.note_mode.setObjectName("SegmentButton")
        self.note_mode.setCheckable(True)
        self.note_mode.clicked.connect(lambda: self._set_content_type("note"))
        header = PageHeader("制定复习计划", "浏览资料、加入计划草稿，再创建可随时启用的复习计划。")
        header.add_leading(back)
        header.add_action(self.problem_mode)
        header.add_action(self.note_mode)
        self.draft_toggle = QPushButton("计划草稿")
        self.draft_toggle.clicked.connect(self._show_draft_view)
        self.draft_toggle.setVisible(False)
        header.add_action(self.draft_toggle)
        root.addWidget(header)

        self.workspace = QSplitter(Qt.Orientation.Horizontal)
        self.workspace.setObjectName("ReviewPlanWorkspace")
        self.workspace.setChildrenCollapsible(False)
        self.workspace.setHandleWidth(1)
        self.browse_workspace = QSplitter(Qt.Orientation.Horizontal)
        self.browse_workspace.setObjectName("ReviewPlanBrowseWorkspace")
        self.browse_workspace.setChildrenCollapsible(False)
        self.browse_workspace.setHandleWidth(1)
        self.browse_workspace.addWidget(self._build_tree())
        self.browse_workspace.addWidget(self._build_content())
        self.browse_workspace.setStretchFactor(0, 0)
        self.browse_workspace.setStretchFactor(1, 1)
        self.browse_workspace.setSizes([240, 760])
        self.workspace.addWidget(self.browse_workspace)
        self.workspace.addWidget(self._build_queue())
        self.workspace.setStretchFactor(0, 1)
        self.workspace.setStretchFactor(1, 0)
        self.workspace.setSizes([1000, 320])
        root.addWidget(self.workspace, stretch=1)

    def _build_tree(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("PlanDirectoryPane")
        pane.setMinimumWidth(220)
        pane.setMaximumWidth(280)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("我的题库"))
        self.tree_search = SearchInput("搜索目录")
        self.tree_search.textChanged.connect(self._filter_tree)
        layout.addWidget(self.tree_search)
        self.folder_tree = QTreeWidget()
        self.folder_tree.setObjectName("PlanFolderTree")
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.currentItemChanged.connect(self._select_folder)
        layout.addWidget(self.folder_tree, stretch=1)
        return pane

    def _build_content(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("PlanContentPane")
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        self.breadcrumb = QLabel("我的题库")
        self.breadcrumb.setObjectName("PageHint")
        layout.addWidget(self.breadcrumb)
        tools = QHBoxLayout()
        self.content_search = SearchInput("搜索当前目录")
        self.content_search.textChanged.connect(self._refresh_sources)
        tools.addWidget(self.content_search, stretch=1)
        self.list_view = QPushButton("列表")
        self.list_view.setObjectName("SegmentButton")
        self.list_view.setCheckable(True)
        self.list_view.setChecked(True)
        self.list_view.clicked.connect(lambda: self._set_source_view("list"))
        self.card_view = QPushButton("卡片")
        self.card_view.setObjectName("SegmentButton")
        self.card_view.setCheckable(True)
        self.card_view.clicked.connect(lambda: self._set_source_view("card"))
        tools.addWidget(self.list_view)
        tools.addWidget(self.card_view)
        layout.addLayout(tools)
        self.source_list = QListWidget()
        self.source_list.setObjectName("PlanSourceList")
        self.source_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.source_list, stretch=1)
        self.selection_hint = StatusTag("未选择资料", "muted")
        layout.addWidget(self.selection_hint, alignment=Qt.AlignmentFlag.AlignLeft)
        actions = QHBoxLayout()
        self.add_button = primary_button("加入计划草稿")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add_selected)
        self.source_list.itemSelectionChanged.connect(self._update_selection)
        actions.addWidget(self.add_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return pane

    def _set_source_view(self, view: str) -> None:
        self._source_view = view
        self.list_view.setChecked(view == "list")
        self.card_view.setChecked(view == "card")
        self.source_list.setViewMode(
            QListWidget.ViewMode.ListMode
            if view == "list"
            else QListWidget.ViewMode.IconMode
        )
        self.source_list.setResizeMode(
            QListWidget.ResizeMode.Fixed if view == "list" else QListWidget.ResizeMode.Adjust
        )
        self.source_list.setGridSize(QSize() if view == "list" else QSize(190, 96))
        self._refresh_sources()

    def _build_queue(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("PlanQueuePane")
        self.queue_pane = pane
        pane.setMinimumWidth(290)
        pane.setMaximumWidth(360)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.queue_title = QLabel("计划草稿")
        head.addWidget(self.queue_title)
        head.addStretch(1)
        self.draft_back_button = ghost_button("返回资料")
        self.draft_back_button.clicked.connect(self._show_browse_view)
        self.draft_back_button.setVisible(False)
        head.addWidget(self.draft_back_button)
        clear = ghost_button("清空")
        clear.clicked.connect(self._clear_queue)
        head.addWidget(clear)
        layout.addLayout(head)
        self.queue_summary = QLabel("")
        self.queue_summary.setObjectName("MutedLabel")
        layout.addWidget(self.queue_summary)
        self.queue_list = QListWidget()
        self.queue_list.setObjectName("PlanQueueList")
        self.queue_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        layout.addWidget(self.queue_list, stretch=1)
        remove = ghost_button("移除选中项")
        remove.clicked.connect(self._remove_selected)
        layout.addWidget(remove)
        self.plan_name = QLineEdit()
        self.plan_name.setPlaceholderText("复习计划名称")
        layout.addWidget(self.plan_name)
        self.create_button = primary_button("创建复习计划")
        self.create_button.clicked.connect(self._confirm_create)
        layout.addWidget(self.create_button)
        return pane

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._set_narrow_layout(self.width() < 1080)

    def _set_narrow_layout(self, narrow: bool) -> None:
        if self._narrow_layout == narrow:
            return
        self._narrow_layout = narrow
        self.draft_toggle.setVisible(narrow)
        self.draft_back_button.setVisible(narrow)
        if narrow:
            self._showing_draft = False
            self.browse_workspace.setVisible(True)
            self.queue_pane.setVisible(False)
        else:
            self._showing_draft = False
            self.browse_workspace.setVisible(True)
            self.queue_pane.setVisible(True)
            self.workspace.setSizes([1000, 320])

    def _show_draft_view(self) -> None:
        if not self._narrow_layout:
            return
        self._showing_draft = True
        self.browse_workspace.setVisible(False)
        self.queue_pane.setVisible(True)
        self.workspace.setSizes([0, max(320, self.width())])

    def _show_browse_view(self) -> None:
        if not self._narrow_layout:
            return
        self._showing_draft = False
        self.queue_pane.setVisible(False)
        self.browse_workspace.setVisible(True)
        self.workspace.setSizes([max(600, self.width()), 0])

    def _set_content_type(self, content_type: str) -> None:
        self.content_type = content_type
        self.problem_mode.setChecked(content_type == "problem")
        self.note_mode.setChecked(content_type == "note")
        self._selected_chapter_id = None
        self.reload()

    def reload(self) -> None:
        self._refresh_tree()
        self._refresh_sources()
        self._refresh_queue()

    def _refresh_tree(self) -> None:
        self.folder_tree.clear()
        root = QTreeWidgetItem(["我的题库"])
        root.setData(0, Qt.ItemDataRole.UserRole, None)
        self.folder_tree.addTopLevelItem(root)
        if self.content_type == "problem":
            counts = Counter(problem.chapter_id for problem in self.services.list_problems())
            subjects = getattr(self.services, "list_subjects", lambda: [])()
            choices = getattr(self.services, "list_category_choices", lambda: [])()
            for subject in subjects:
                subject_item = QTreeWidgetItem([subject.name])
                root.addChild(subject_item)
                for choice in choices:
                    if choice.subject_id != subject.id or not choice.chapter_id:
                        continue
                    label = f"{choice.chapter_path[-1]} ({counts[choice.chapter_id]})"
                    item = QTreeWidgetItem([label])
                    item.setData(0, Qt.ItemDataRole.UserRole, choice.chapter_id)
                    subject_item.addChild(item)
        root.setExpanded(True)
        self.folder_tree.setCurrentItem(root)

    def _filter_tree(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self.folder_tree.topLevelItemCount()):
            root = self.folder_tree.topLevelItem(index)
            for child_index in range(root.childCount()):
                child = root.child(child_index)
                child.setHidden(bool(needle and needle not in child.text(0).lower()))

    def _select_folder(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None) -> None:
        self._selected_chapter_id = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self.breadcrumb.setText(current.text(0) if current else "我的题库")
        self._refresh_sources()

    def _refresh_sources(self) -> None:
        self.source_list.clear()
        query = self.content_search.text().strip().lower()
        queued_ids = set(self.services.list_review_waiting_ids(self.content_type))
        if self.content_type == "problem":
            rows = self.services.list_problems()
            if self._selected_chapter_id:
                rows = [row for row in rows if row.chapter_id == self._selected_chapter_id]
            for problem in rows:
                label = problem.title or "未命名题目"
                if query and query not in label.lower():
                    continue
                state = "\n已加入计划草稿" if problem.id in queued_ids else ""
                item = QListWidgetItem(f"题目  ·  {label}{state}")
                item.setData(Qt.ItemDataRole.UserRole, problem.id)
                item.setData(Qt.ItemDataRole.UserRole + 1, problem.id in queued_ids)
                item.setToolTip("已加入计划草稿" if problem.id in queued_ids else label)
                self.source_list.addItem(item)
        elif self.notes:
            for note in self.notes.list_notes(status="active"):
                label = note.title or "未命名笔记"
                if query and query not in label.lower():
                    continue
                state = "\n已加入计划草稿" if note.id in queued_ids else ""
                item = QListWidgetItem(f"笔记  ·  {label}{state}")
                item.setData(Qt.ItemDataRole.UserRole, note.id)
                item.setData(Qt.ItemDataRole.UserRole + 1, note.id in queued_ids)
                item.setToolTip("已加入计划草稿" if note.id in queued_ids else label)
                self.source_list.addItem(item)
        self._update_selection()

    def _update_selection(self) -> None:
        selected = self.source_list.selectedItems()
        count = len(selected)
        addable = [item for item in selected if not item.data(Qt.ItemDataRole.UserRole + 1)]
        self.selection_hint.setText(f"已选择 {count} 项" if count else "未选择资料")
        self.selection_hint.set_variant("active" if count else "muted")
        self.add_button.setEnabled(bool(addable))

    def _add_selected(self) -> None:
        ids = [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self.source_list.selectedItems()
            if not item.data(Qt.ItemDataRole.UserRole + 1)
        ]
        if ids:
            added = self.services.add_to_review_waiting_queue(self.content_type, ids)
            self.status_message.emit(f"已加入计划草稿：{added} 项")
            self._refresh_queue()
            self._refresh_sources()

    def _refresh_queue(self) -> None:
        ids = self.services.list_review_waiting_ids(self.content_type)
        self.queue_list.clear()
        labels, total_questions = self._queue_details(ids)
        for source_id in ids:
            item = QListWidgetItem(labels.get(source_id, "已移除的资料"))
            item.setData(Qt.ItemDataRole.UserRole, source_id)
            self.queue_list.addItem(item)
        self.queue_summary.setText(f"已添加 {len(ids)} 项 · 共 {total_questions} 道题目")
        self.queue_list.setVisible(bool(ids))
        if not ids:
            self._show_queue_empty_state()
        self.create_button.setEnabled(bool(ids))

    def _queue_details(self, ids: list[str]) -> tuple[dict[str, str], int]:
        if self.content_type == "problem":
            problems = {problem.id: problem for problem in self.services.list_problems()}
            return (
                {
                    source_id: (problems[source_id].title or "未命名题目")
                    for source_id in ids
                    if source_id in problems
                },
                sum(1 for source_id in ids if source_id in problems),
            )
        notes = {
            note.id: note
            for note in (self.notes.list_notes(status="active") if self.notes else [])
        }
        return (
            {
                source_id: (notes[source_id].title or "未命名笔记")
                for source_id in ids
                if source_id in notes
            },
            len(ids),
        )

    def _show_queue_empty_state(self) -> None:
        # Keep the list instance stable for drag/drop; its placeholder is intentionally concise.
        placeholder = QListWidgetItem("从左侧选择资料加入计划草稿")
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        placeholder.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_list.addItem(placeholder)
        self.queue_list.setVisible(True)

    def _remove_selected(self) -> None:
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.queue_list.selectedItems()]
        if ids:
            self.services.remove_from_review_waiting_queue(self.content_type, ids)
            self._refresh_queue()

    def _clear_queue(self) -> None:
        self.services.clear_review_waiting_queue(self.content_type)
        self._refresh_queue()

    def _confirm_create(self) -> None:
        name = self.plan_name.text().strip()
        count = self.queue_list.count()
        if not name or not count:
            self.status_message.emit("请填写计划名称并至少加入一项资料")
            return
        ids = [self.queue_list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(count)]
        if not ConfirmDialog.ask(
            self,
            "确认创建复习计划",
            f"计划名称：{name}\n资料数量：{count}\n确认后将清空当前计划草稿。",
            "创建计划",
        ):
            return
        self.create_button.setEnabled(False)
        try:
            plan = self.services.create_review_plan_from_waiting_queue(
                self.content_type, name, ids
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            self.create_button.setEnabled(True)
            return
        self.plan_name.clear()
        self.status_message.emit("复习计划已创建")
        self.plan_created.emit(plan.id)
        self._refresh_queue()
