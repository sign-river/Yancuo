"""Three-pane review-plan builder, separate from the review dashboard."""

from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.note_service import NoteService
from yancuo_win.application.services import AppServices, ProblemFilter
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.widgets import ghost_button, primary_button


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
        self._selected_chapter_id: str | None = None
        self._build()
        self.reload()

    def _build(self) -> None:
        self.setObjectName("PageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        back = ghost_button("← 返回复习")
        back.clicked.connect(self.back_requested)
        header.addWidget(back)
        title = QLabel("制定复习计划")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.problem_mode = primary_button("题目复习")
        self.problem_mode.clicked.connect(lambda: self._set_content_type("problem"))
        self.note_mode = QPushButton("笔记复习")
        self.note_mode.clicked.connect(lambda: self._set_content_type("note"))
        header.addWidget(self.problem_mode)
        header.addWidget(self.note_mode)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("ReviewPlanWorkspace")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)
        splitter.addWidget(self._build_tree())
        splitter.addWidget(self._build_content())
        splitter.addWidget(self._build_queue())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([240, 760, 320])
        root.addWidget(splitter, stretch=1)

    def _build_tree(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("PlanDirectoryPane")
        pane.setMinimumWidth(220)
        pane.setMaximumWidth(280)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("我的题库"))
        self.tree_search = QLineEdit()
        self.tree_search.setPlaceholderText("搜索目录")
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
        self.content_search = QLineEdit()
        self.content_search.setPlaceholderText("搜索当前目录")
        self.content_search.textChanged.connect(self._refresh_sources)
        tools.addWidget(self.content_search, stretch=1)
        self.list_view = primary_button("列表")
        self.list_view.setEnabled(False)
        self.card_view = QPushButton("卡片")
        self.card_view.setEnabled(False)
        tools.addWidget(self.list_view)
        tools.addWidget(self.card_view)
        layout.addLayout(tools)
        self.source_list = QListWidget()
        self.source_list.setObjectName("PlanSourceList")
        self.source_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self.source_list, stretch=1)
        self.selection_hint = QLabel("未选择资料")
        self.selection_hint.setObjectName("MutedLabel")
        layout.addWidget(self.selection_hint)
        actions = QHBoxLayout()
        self.add_button = primary_button("加入计划草稿")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add_selected)
        self.source_list.itemSelectionChanged.connect(self._update_selection)
        actions.addWidget(self.add_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return pane

    def _build_queue(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("PlanQueuePane")
        pane.setMinimumWidth(290)
        pane.setMaximumWidth(360)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.queue_title = QLabel("计划草稿")
        head.addWidget(self.queue_title)
        head.addStretch(1)
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

    def _set_content_type(self, content_type: str) -> None:
        self.content_type = content_type
        self.problem_mode.setEnabled(content_type != "problem")
        self.note_mode.setEnabled(content_type != "note")
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
        if self.content_type == "problem":
            rows = self.services.list_problems()
            if self._selected_chapter_id:
                rows = [row for row in rows if row.chapter_id == self._selected_chapter_id]
            for problem in rows:
                label = problem.title or "未命名题目"
                if query and query not in label.lower():
                    continue
                item = QListWidgetItem(f"题目  ·  {label}")
                item.setData(Qt.ItemDataRole.UserRole, problem.id)
                self.source_list.addItem(item)
        elif self.notes:
            for note in self.notes.list_notes(status="active"):
                label = note.title or "未命名笔记"
                if query and query not in label.lower():
                    continue
                item = QListWidgetItem(f"笔记  ·  {label}")
                item.setData(Qt.ItemDataRole.UserRole, note.id)
                self.source_list.addItem(item)
        self._update_selection()

    def _update_selection(self) -> None:
        count = len(self.source_list.selectedItems())
        self.selection_hint.setText(f"已选择 {count} 项" if count else "未选择资料")
        self.add_button.setEnabled(count > 0)

    def _add_selected(self) -> None:
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.source_list.selectedItems()]
        if ids:
            added = self.services.add_to_review_waiting_queue(self.content_type, ids)
            self.status_message.emit(f"已加入计划草稿：{added} 项")
            self._refresh_queue()

    def _refresh_queue(self) -> None:
        ids = self.services.list_review_waiting_ids(self.content_type)
        self.queue_list.clear()
        labels = {item.data(Qt.ItemDataRole.UserRole): item.text().split("  ·  ")[-1] for item in [self.source_list.item(i) for i in range(self.source_list.count())]}
        for source_id in ids:
            item = QListWidgetItem(labels.get(source_id, "已移除的资料"))
            item.setData(Qt.ItemDataRole.UserRole, source_id)
            self.queue_list.addItem(item)
        self.queue_summary.setText(f"已选择 {len(ids)} 项")
        self.create_button.setEnabled(bool(ids))

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
        if QMessageBox.question(self, "确认创建", f"计划：{name}\n资料数量：{count}\n确认创建？") != QMessageBox.StandardButton.Yes:
            return
        self.create_button.setEnabled(False)
        try:
            plan = self.services.create_review_plan_from_waiting_queue(self.content_type, name)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            self.create_button.setEnabled(True)
            return
        self.plan_name.clear()
        self.status_message.emit("复习计划已创建")
        self.plan_created.emit(plan.id)
        self._refresh_queue()
