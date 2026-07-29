"""In-shell review workflow with formula rendering and continuous grading."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.services import AppServices
from yancuo_win.application.note_service import NoteService
from yancuo_win.data.models import NoteDocument, Problem
from yancuo_win.domain.review_rules import REVIEW_GRADES
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.review_plan_builder import ReviewPlanBuilder
from yancuo_win.ui.widgets import CardFrame, PageHeader, ghost_button, primary_button


class ReviewPage(QWidget):
    """A resumable review session that stays inside the main content area."""

    status_message = Signal(str)
    open_problem_requested = Signal(str)
    queue_changed = Signal()

    def __init__(self, services: AppServices, notes: NoteService | None = None, parent=None) -> None:
        super().__init__(parent)
        self.services = services
        self.notes = notes
        self._queue: list[Problem] = []
        self._note_queue: list[NoteDocument] = []
        self._content_type = "problem"
        self._index = 0
        self._answer_visible = False
        self._session_grades: Counter[int] = Counter()
        self._session_completed = 0
        self._study_session_id: str | None = None
        self._answer_viewed_at: datetime | None = None
        self._selected_plan_id: str | None = None
        self._build()
        self.show_home()

    def _build(self) -> None:
        self.setObjectName("PageRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.home_page = self._build_home()
        self.plan_select_page, self.plan_select_layout = self._build_subpage("选择复习计划")
        self.plan_builder_page = ReviewPlanBuilder(self.services, self.notes)
        self.plan_builder_page.back_requested.connect(self.show_home)
        self.plan_builder_page.status_message.connect(self.status_message)
        self.plan_builder_page.plan_created.connect(self._open_created_plan)
        self._move_to_subpage(self.plan_select_card, self.plan_select_layout)
        self._add_home_actions()
        self.session_page = self._build_session()
        self.stack.addWidget(self.home_page)
        self.stack.addWidget(self.plan_select_page)
        self.stack.addWidget(self.plan_builder_page)
        self.stack.addWidget(self.session_page)
        root.addWidget(self.stack)

    def _open_created_plan(self, plan_id: str) -> None:
        self.show_home()
        index = self.plan_combo.findData(plan_id)
        if index >= 0:
            self.plan_combo.setCurrentIndex(index)
            self.stack.setCurrentWidget(self.plan_select_page)

    def _build_subpage(self, title_text: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        header = PageHeader(title_text)
        back = ghost_button("返回复习")
        back.clicked.connect(self.show_home)
        header.add_leading(back)
        layout.addWidget(header)
        return page, layout

    @staticmethod
    def _move_to_subpage(widget: QWidget, layout: QVBoxLayout) -> None:
        widget.setParent(None)
        layout.addWidget(widget)
        layout.addStretch(1)

    def _add_home_actions(self) -> None:
        layout = self.home_layout
        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(14)
        action_grid.setVerticalSpacing(14)
        select = CardFrame()
        select.add_title("选择并开始复习")
        select.add_hint("选择已有的题目或笔记复习计划，开始本轮复习。")
        button = primary_button("选择复习计划")
        button.clicked.connect(lambda: self.stack.setCurrentWidget(self.plan_select_page))
        select.body.addWidget(button)
        select.setMinimumHeight(178)
        action_grid.addWidget(select, 0, 0)
        create = CardFrame()
        create.add_title("制定复习计划")
        create.add_hint("从题库或笔记库选择资料，编辑等待队列并命名创建计划。")
        button = QPushButton("制定计划")
        button.clicked.connect(lambda: self.stack.setCurrentWidget(self.plan_builder_page))
        create.body.addWidget(button)
        create.setMinimumHeight(178)
        action_grid.addWidget(create, 0, 1)
        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        layout.addLayout(action_grid)
        layout.addStretch(1)

    def _build_session(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        header = PageHeader("复习会话")
        self.progress_label = header.description
        self.progress_label.setVisible(True)
        back = ghost_button("返回复习")
        back.clicked.connect(self.show_home)
        self.detail_button = QPushButton("打开题目详情")
        self.detail_button.clicked.connect(self._open_current_detail)
        header.add_leading(back)
        header.add_action(self.detail_button)
        root.addWidget(header)

        self.hero = QLabel("今日待复习")
        self.hero.setObjectName("HeroBanner")
        root.addWidget(self.hero)

        self.reader = MathContentView()
        root.addWidget(self.reader, stretch=1)

        self.note_complete_button = primary_button("标记已阅读并继续")
        self.note_complete_button.clicked.connect(self._complete_note)
        root.addWidget(self.note_complete_button)
        self.session_finish_button = primary_button("返回复习计划")
        self.session_finish_button.clicked.connect(self.show_home)
        self.session_finish_button.setVisible(False)
        root.addWidget(self.session_finish_button)

        self.grade_card = CardFrame()
        self.grade_card.add_title("完成思考后评分")
        self.grade_hint = self.grade_card.add_hint(
            "请先独立思考，再点击“显示答案与解析”；查看答案后才可评分。"
        )
        answer_row = QHBoxLayout()
        self.answer_button = primary_button("显示答案与解析")
        self.answer_button.clicked.connect(self._toggle_answer)
        answer_row.addWidget(self.answer_button)
        answer_row.addStretch(1)
        self.grade_card.body.addLayout(answer_row)
        grade_row = QHBoxLayout()
        self.grade_buttons: list[QPushButton] = []
        for grade, label in REVIEW_GRADES.items():
            button = QPushButton(f"{grade} {label}")
            button.clicked.connect(lambda _checked=False, value=grade: self._grade(value))
            button.setEnabled(False)
            grade_row.addWidget(button)
            self.grade_buttons.append(button)
        self.grade_card.body.addLayout(grade_row)

        tools = QHBoxLayout()
        self.pause_button = QPushButton("暂停当前题")
        self.pause_button.clicked.connect(self._pause_current)
        self.export_csv_button = QPushButton("导出数据表")
        self.export_csv_button.clicked.connect(self._export_csv)
        self.share_button = QPushButton("导出脱敏分享")
        self.share_button.clicked.connect(self._export_share)
        for button in (self.pause_button, self.export_csv_button, self.share_button):
            tools.addWidget(button)
        tools.addStretch(1)
        self.grade_card.body.addLayout(tools)

        nav = QHBoxLayout()
        previous = ghost_button("上一题")
        previous.clicked.connect(self._previous)
        skip = QPushButton("暂时跳过 / 下一题")
        skip.clicked.connect(self._skip)
        nav.addWidget(previous)
        nav.addWidget(skip)
        nav.addStretch(1)
        self.grade_card.body.addLayout(nav)
        root.addWidget(self.grade_card)
        return page

    def _build_home(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        root = QVBoxLayout(page)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        self.home_layout = root

        root.addWidget(
            PageHeader("复习", "选择计划后开始；答题与笔记阅读在独立会话中完成。")
        )

        self.review_overview = QLabel()
        self.review_overview.setObjectName("HeroBanner")
        root.addWidget(self.review_overview)

        plans = CardFrame()
        self.plan_select_card = plans
        plans.add_title("选择复习计划")
        plans.add_hint("必须选择一个题目或笔记复习计划后，才能开始本轮复习。")
        self.plan_combo = QComboBox()
        plans.body.addWidget(self.plan_combo)
        refresh_plans = ghost_button("刷新计划")
        refresh_plans.clicked.connect(self.show_home)
        start_selected = primary_button("开始所选计划")
        start_selected.clicked.connect(self.start_session)
        plans.body.addLayout(self._actions(refresh_plans, start_selected))

        queue_card = CardFrame()
        self.plan_builder_card = queue_card
        queue_card.add_title("制定复习计划")
        queue_card.add_hint("先将同一类型的资料加入等待队列，再命名创建复习计划。")
        self.queue_type = QComboBox()
        self.queue_type.addItem("题目复习", "problem")
        self.queue_type.addItem("笔记复习", "note")
        self.queue_type.currentIndexChanged.connect(self._refresh_plan_builder)
        queue_card.body.addWidget(self.queue_type)
        workspace = QSplitter(Qt.Orientation.Horizontal)
        source_pane = QWidget()
        source_layout = QVBoxLayout(source_pane)
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.addWidget(QLabel("可加入的资料"))
        self.source_list = QListWidget()
        self.source_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.source_list.setMinimumHeight(260)
        source_layout.addWidget(self.source_list)
        add_selected = primary_button("加入等待队列")
        add_selected.clicked.connect(self._add_selected_to_waiting)
        source_layout.addLayout(self._actions(add_selected))
        waiting_pane = QWidget()
        waiting_layout = QVBoxLayout(waiting_pane)
        waiting_layout.setContentsMargins(0, 0, 0, 0)
        waiting_layout.addWidget(QLabel("等待队列"))
        self.waiting_list = QListWidget()
        self.waiting_list.setMinimumHeight(260)
        waiting_layout.addWidget(self.waiting_list)
        remove_selected = ghost_button("移除选中项")
        remove_selected.clicked.connect(self._remove_selected_waiting)
        clear_waiting = ghost_button("清空等待队列")
        clear_waiting.clicked.connect(self._clear_waiting)
        waiting_layout.addLayout(self._actions(remove_selected, clear_waiting))
        workspace.addWidget(source_pane)
        workspace.addWidget(waiting_pane)
        workspace.setStretchFactor(0, 1)
        workspace.setStretchFactor(1, 1)
        queue_card.body.addWidget(workspace)
        self.plan_name_edit = QLineEdit()
        self.plan_name_edit.setPlaceholderText("复习计划名称")
        queue_card.body.addWidget(self.plan_name_edit)
        create_plan = primary_button("创建复习计划")
        create_plan.clicked.connect(self._create_plan)
        queue_card.body.addLayout(self._actions(create_plan))

        plan = CardFrame()
        plan.add_title("本次复习")
        plan.add_hint("这些设置只应用于即将开始的会话，不会修改题目的复习计划。")
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("今日到期", "due")
        self.scope_combo.addItem("全部已启用题目", "active")
        self.scope_combo.addItem("尚未复习过", "unreviewed")
        self.order_combo = QComboBox()
        self.order_combo.addItem("按复习计划顺序", "scheduled")
        self.order_combo.addItem("随机刷题", "random")
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 100)
        self.limit_spin.setValue(20)
        plan.body.addWidget(QLabel("复习范围"))
        plan.body.addWidget(self.scope_combo)
        plan.body.addWidget(QLabel("题目顺序"))
        plan.body.addWidget(self.order_combo)
        plan.body.addWidget(QLabel("题目数量"))
        plan.body.addWidget(self.limit_spin)
        plan.setVisible(False)
        plan.setParent(page)
        self._legacy_plan_controls = plan

        types = CardFrame()
        types.add_title("题型")
        types.add_hint("未勾选时包含全部题型；勾选后只复习选中的题型。")
        self.type_row = QHBoxLayout()
        self.type_row.addStretch(1)
        types.body.addLayout(self.type_row)
        types.setVisible(False)
        types.setParent(page)
        self._legacy_type_controls = types

        actions = QHBoxLayout()
        self.start_button = primary_button("开始本次复习")
        self.start_button.clicked.connect(self.start_session)
        self.start_button.setVisible(False)
        actions.addWidget(self.start_button)
        actions.addStretch(1)
        return page

    def _refresh_plan_builder(self) -> None:
        content_type = str(self.queue_type.currentData())
        self.source_list.clear()
        self.waiting_list.clear()
        labels: dict[str, str] = {}
        if content_type == "problem":
            sources = self.services.list_problems()
            for problem in sources:
                label = problem.title or "未命名题目"
                labels[problem.id] = label
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, problem.id)
                self.source_list.addItem(item)
        elif self.notes is not None:
            for note in self.notes.list_notes(status="active"):
                label = note.title or "未命名笔记"
                labels[note.id] = label
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, note.id)
                self.source_list.addItem(item)
        for source_id in self.services.list_review_waiting_ids(content_type):
            item = QListWidgetItem(labels.get(source_id, "已移除的资料"))
            item.setData(Qt.ItemDataRole.UserRole, source_id)
            self.waiting_list.addItem(item)

    def _add_selected_to_waiting(self) -> None:
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.source_list.selectedItems()]
        if ids:
            self.services.add_to_review_waiting_queue(str(self.queue_type.currentData()), ids)
            self._refresh_plan_builder()

    def _remove_selected_waiting(self) -> None:
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.waiting_list.selectedItems()]
        if ids:
            self.services.remove_from_review_waiting_queue(str(self.queue_type.currentData()), ids)
            self._refresh_plan_builder()

    def _clear_waiting(self) -> None:
        self.services.clear_review_waiting_queue(str(self.queue_type.currentData()))
        self._refresh_plan_builder()

    def _create_plan(self) -> None:
        try:
            self.services.create_review_plan_from_waiting_queue(
                str(self.queue_type.currentData()), self.plan_name_edit.text()
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self.plan_name_edit.clear()
        self._refresh_plan_builder()
        self.show_home()

    @staticmethod
    def _actions(*buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        return row

    def reload_queue(self, *, preserve_current: bool = True) -> None:
        if self.stack.currentWidget() is self.home_page:
            self.show_home()
        self.queue_changed.emit()

    def show_home(self) -> None:
        selected = self.plan_combo.currentData() if hasattr(self, "plan_combo") else None
        try:
            candidates = self.services.prepare_study_queue(
                scope="due", order="scheduled", limit=None
            )
            types = self.services.prepare_study_queue(
                scope="active", order="scheduled", limit=None
            )
        except DomainError as exc:
            self.review_overview.setText("无法加载复习任务")
            self.status_message.emit(str(exc))
            return
        plans = self.services.list_review_plans()
        self.plan_combo.clear()
        for plan in plans:
            kind = "当日" if plan.kind == "daily" else "自定义"
            label = f"{plan.name} · {'题目' if plan.content_type == 'problem' else '笔记'} · {len(plan.items)} 项 · {kind}"
            self.plan_combo.addItem(label, plan.id)
        if selected:
            index = self.plan_combo.findData(selected)
            if index >= 0:
                self.plan_combo.setCurrentIndex(index)
        else:
            self.plan_combo.setCurrentIndex(-1)
        self._refresh_plan_builder()
        self.review_overview.setText(
            f"今日待复习 {len(candidates)} 题 · 已启用题目 {len(types)} 题"
        )
        while self.type_row.count() > 1:
            item = self.type_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.type_checks: list[QCheckBox] = []
        for name in sorted({(problem.problem_type or "未标注").strip() for problem in types}):
            check = QCheckBox(name)
            self.type_row.insertWidget(self.type_row.count() - 1, check)
            self.type_checks.append(check)
        self.stack.setCurrentWidget(self.home_page)

    def start_session(self) -> None:
        self._session_grades.clear()
        self._session_completed = 0
        try:
            plan_id = self.plan_combo.currentData()
            if not plan_id:
                self.status_message.emit("请先选择复习计划")
                return
            plan = self.services.get_review_plan(str(plan_id))
            if plan is None or not plan.items:
                self.status_message.emit("复习计划不存在或没有内容")
                return
            self._selected_plan_id = plan.id
            if plan.content_type == "note":
                if self.notes is None:
                    self.status_message.emit("笔记服务不可用")
                    return
                self._content_type = "note"
                self._note_queue = [
                    note
                    for item in plan.items
                    if (note := self.notes.get_note(item.source_id)) is not None
                    and note.status == "active"
                ]
                if not self._note_queue:
                    self.status_message.emit("复习计划中的笔记已被移除")
                    self.show_home()
                    return
                self._queue = []
                self._index = 0
                self.stack.setCurrentWidget(self.session_page)
                self._render()
                return
            self._content_type = "problem"
            planned = self.services.list_problems_by_ids([item.source_id for item in plan.items])
            if not planned:
                self.status_message.emit("复习计划中的题目已被移除")
                return
            selection = {"review_plan_id": plan.id, "kind": plan.kind}
            session, queue = self.services.start_study_session(
                selection=selection,
                problem_ids=[problem.id for problem in planned],
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._study_session_id = session.id
        self._queue = queue
        self._index = 0
        self._answer_visible = False
        self._answer_viewed_at = None
        self.stack.setCurrentWidget(self.session_page)
        self._render()
        self.queue_changed.emit()

    @property
    def current_problem_id(self) -> str | None:
        problem = self._current()
        return problem.id if problem else None

    def problem_ids(self) -> list[str]:
        return [problem.id for problem in self._queue]

    def select_problem(self, problem_id: str) -> None:
        for index, problem in enumerate(self._queue):
            if problem.id == problem_id:
                self._index = index
                self._answer_visible = False
                self._render()
                break

    def _current(self) -> Problem | None:
        if self._content_type == "note":
            return None
        if not self._queue:
            return None
        self._index %= len(self._queue)
        return self._queue[self._index]

    def _current_note(self) -> NoteDocument | None:
        if not self._note_queue:
            return None
        self._index %= len(self._note_queue)
        return self._note_queue[self._index]

    @staticmethod
    def _fields(problem: Problem) -> dict[str, object]:
        return {
            "title": problem.title,
            "priority": problem.priority,
            "question_markdown": problem.question_markdown,
            "question_latex": problem.question_latex,
            "user_answer": problem.user_answer,
            "correct_answer": problem.correct_answer,
            "solution_markdown": problem.solution_markdown,
            "error_analysis": problem.error_analysis,
            "notes": problem.notes,
            "problem_type": problem.problem_type,
            "source_book": problem.source_book,
        }

    def _render(self) -> None:
        if self._content_type == "note":
            note = self._current_note()
            self.grade_card.setVisible(False)
            self.detail_button.setVisible(False)
            self.note_complete_button.setVisible(note is not None)
            self.session_finish_button.setVisible(note is None)
            self.hero.setText(
                f"本轮已完成 {self._session_completed} 篇 · 剩余 {len(self._note_queue)} 篇"
            )
            if note is None:
                self.progress_label.setText("笔记复习完成")
                self.reader.set_message(
                    "复习完成", f"本轮已完成 {self._session_completed} 篇笔记。"
                )
                return
            self.progress_label.setText(f"当前第 {self._index + 1} / {len(self._note_queue)} 篇")
            body = "\n\n".join(block.content_markdown for block in note.blocks)
            self.reader.set_problem(
                {"title": note.title, "question_markdown": body, "solution_markdown": note.summary},
                tag_names=[tag.name for tag in note.tags], include_answers=False,
            )
            return
        problem = self._current()
        self.grade_card.setVisible(True)
        self.detail_button.setVisible(True)
        self.note_complete_button.setVisible(False)
        self.session_finish_button.setVisible(False)
        remaining = len(self._queue)
        self.hero.setText(
            f"本轮已完成 {self._session_completed} 题  ·  剩余 {remaining} 题"
        )
        if not problem:
            summary = "今日没有待复习题目。"
            if self._session_completed:
                grade_summary = " · ".join(
                    f"{grade}分 {self._session_grades[grade]}题"
                    for grade in REVIEW_GRADES
                    if self._session_grades[grade]
                )
                summary = f"本轮完成 {self._session_completed} 题。{grade_summary}"
            self.progress_label.setText("今日复习已完成")
            self.reader.set_message("复习完成", summary)
            self.answer_button.setEnabled(False)
            self.grade_hint.setText("当前没有需要评分的题目。")
            self.detail_button.setEnabled(False)
            self.session_finish_button.setVisible(True)
            for button in self.grade_buttons:
                button.setEnabled(False)
            return

        self.progress_label.setText(
            f"当前第 {self._index + 1} / {remaining} 题 · "
            f"已复习 {problem.review_count} 次"
        )
        self.answer_button.setEnabled(True)
        self.answer_button.setText(
            "隐藏答案与解析" if self._answer_visible else "显示答案与解析"
        )
        self.grade_hint.setText(
            "答案与解析已显示，请根据掌握程度选择评分。"
            if self._answer_visible
            else "请先独立思考，再点击“显示答案与解析”；查看答案后才可评分。"
        )
        self.detail_button.setEnabled(True)
        for button in self.grade_buttons:
            button.setEnabled(self._answer_visible)
        self.reader.set_problem(
            self._fields(problem),
            tag_names=[tag.name for tag in (problem.tags or [])],
            include_answers=self._answer_visible,
        )

    def _complete_note(self) -> None:
        if not self._note_queue:
            return
        note = self._current_note()
        if note is None:
            return
        try:
            self.services.record_note_review(
                note.id, review_plan_id=self._selected_plan_id
            )
        except DomainError as exc:
            current = self.notes.get_note(note.id) if self.notes is not None else None
            if current is None or current.status == "trashed":
                self._note_queue.pop(self._index)
                self._index = self._index % len(self._note_queue) if self._note_queue else 0
                self.status_message.emit("已跳过不存在或已移入回收站的笔记")
                self._render()
                self.queue_changed.emit()
                return
            self.status_message.emit(str(exc))
            return
        self._note_queue.pop(self._index)
        if self._note_queue:
            self._index %= len(self._note_queue)
        else:
            self._index = 0
        self._session_completed += 1
        self.status_message.emit("已记录笔记阅读完成")
        self._render()
        self.queue_changed.emit()

    def _toggle_answer(self) -> None:
        if not self._current():
            return
        self._answer_visible = not self._answer_visible
        if self._answer_visible:
            self._answer_viewed_at = datetime.now(timezone.utc)
        self._render()

    def _grade(self, grade: int) -> None:
        problem = self._current()
        if not problem or not self._answer_visible:
            return
        try:
            if self._study_session_id:
                result = self.services.record_review(
                    problem.id,
                    grade,
                    study_session_id=self._study_session_id,
                    answer_viewed_at=self._answer_viewed_at,
                )
            else:
                result = self.services.record_review(problem.id, grade)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._session_grades[grade] += 1
        self._session_completed += 1
        self._queue.pop(self._index)
        if self._queue:
            self._index %= len(self._queue)
        else:
            self._index = 0
        self._answer_visible = False
        self._answer_viewed_at = None
        self.status_message.emit(
            f"已记录：{result['label']}；下次复习 {result['next_review_at'][:10]}"
        )
        self._render()
        self.queue_changed.emit()

    def _previous(self) -> None:
        if self._queue:
            self._index = (self._index - 1) % len(self._queue)
            self._answer_visible = False
            self._render()

    def _skip(self) -> None:
        if self._queue:
            self._index = (self._index + 1) % len(self._queue)
            self._answer_visible = False
            self._render()

    def _open_current_detail(self) -> None:
        if self.current_problem_id:
            self.open_problem_requested.emit(self.current_problem_id)

    def _pause_current(self) -> None:
        problem = self._current()
        if problem is None:
            return
        try:
            self.services.set_review_enabled(problem.id, False)
        except DomainError as exc:
            self.status_message.emit(str(exc))
            return
        self._queue.pop(self._index)
        if self._queue:
            self._index %= len(self._queue)
        self._answer_visible = False
        self._answer_viewed_at = None
        self.status_message.emit("已暂停当前题的复习")
        self._render()
        self.queue_changed.emit()

    def _export_csv(self) -> None:
        if not self._study_session_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出复习数据表", "review-records.csv", "CSV (*.csv)")
        if path:
            self.services.export_study_session_csv(self._study_session_id, Path(path))
            self.status_message.emit("复习数据表已导出")

    def _export_share(self) -> None:
        if not self._study_session_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出脱敏分享图片", "review-summary.png", "PNG (*.png)")
        if path:
            summary = self.services.finish_study_session(self._study_session_id)
            pixmap = QPixmap(960, 420)
            pixmap.fill(QColor("#f8fafc"))
            painter = QPainter(pixmap)
            painter.setPen(QColor("#172033"))
            painter.setFont(QFont("Microsoft YaHei", 24, QFont.Weight.Bold))
            painter.drawText(48, 72, "错题本 · 复习完成")
            painter.setFont(QFont("Microsoft YaHei", 15))
            painter.drawText(48, 128, f"完成 {summary['completed_count']} / {summary['problem_count']} 题")
            painter.drawText(48, 174, "评分分布")
            x = 48
            for grade in REVIEW_GRADES:
                count = summary["grades"][grade]
                painter.setBrush(QColor("#2463eb"))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(x, 210, 132, 112, 6, 6)
                painter.setPen(QColor("#ffffff"))
                painter.setFont(QFont("Microsoft YaHei", 17, QFont.Weight.Bold))
                painter.drawText(x + 18, 252, f"{grade} 分")
                painter.drawText(x + 18, 292, f"{count} 题")
                x += 156
            painter.end()
            pixmap.save(path, "PNG")
            self.status_message.emit("脱敏分享图片已导出")

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Space:
            self._toggle_answer()
            event.accept()
            return
        if Qt.Key_1 <= key <= Qt.Key_5 and self._answer_visible:
            self._grade(key - Qt.Key_0)
            event.accept()
            return
        if key == Qt.Key_Right:
            self._skip()
            event.accept()
            return
        if key == Qt.Key_Left:
            self._previous()
            event.accept()
            return
        super().keyPressEvent(event)
