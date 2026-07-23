"""主窗口：侧栏分页 + 题库三栏（现代化壳，业务槽复用）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.ai_service import AIService
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.cloud_service import CloudBackupService
from yancuo_win.application.intake_service import ProblemIntakeService
from yancuo_win.application.services import AppServices, ProblemFilter
from yancuo_win.application.sync_service import SyncService
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.domain.rules import DomainError
from yancuo_win.import_export.ebpack import EbpackService
from yancuo_win.import_export.gmshare import GmshareService
from yancuo_win.import_export.workspace import WorkspaceService
from yancuo_win.tasks.worker import AIJobWorker
from yancuo_win.ui.duplicate_dialog import DuplicateDialog
from yancuo_win.ui.intake_page import IntakePage
from yancuo_win.ui.problem_detail import ProblemDetailPage
from yancuo_win.ui.problem_editor import ProblemEditorDialog
from yancuo_win.ui.review_dialog import ReviewDialog
from yancuo_win.ui.settings_dialog import SettingsDialog
from yancuo_win.ui.today_review import TodayReviewDialog
from yancuo_win.ui.widgets import (
    CardFrame,
    button_row,
    danger_button,
    ghost_button,
    primary_button,
)

_PAGE_DASHBOARD = 0
_PAGE_INTAKE = 1
_PAGE_LIBRARY = 2
_PAGE_REVIEW = 3
_PAGE_DATA = 4
_PAGE_SETTINGS = 5
_PAGE_PROBLEM_DETAIL = 6

_STATUS_LABELS = {
    "inbox": "收件箱",
    "active": "正式",
    "archived": "归档",
    "trashed": "回收站",
}


class MainWindow(QMainWindow):
    def __init__(self, runtime: RuntimeContext) -> None:
        super().__init__()
        self.runtime = runtime
        self.services = AppServices(runtime)
        self.ai = AIService(runtime)
        self.intake = ProblemIntakeService(runtime)
        self.workspace = WorkspaceService(runtime)
        self.ebpack = EbpackService(runtime)
        self.gmshare = GmshareService(runtime)
        self.cloud = CloudBackupService(runtime)
        self.sync = SyncService(runtime)
        self._nav_mode = "active"
        self._selected_problem_id: str | None = None
        self._ai_worker: AIJobWorker | None = None
        self._ctx_buttons: list[QPushButton] = []

        self.setWindowTitle("研错库")
        self.resize(1320, 840)
        self._build_central()
        self._build_status()
        self.refresh_all()
        self._update_context_bar(False)
        self._refresh_focus_pages()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.intake_page.shutdown()
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            self._ai_worker.wait(3000)
        super().closeEvent(event)

    # —— 壳布局 ——

    def _build_central(self) -> None:
        root = QWidget()
        root.setObjectName("PageRoot")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_dashboard_page())
        self.intake_page = IntakePage(self.intake)
        self.intake_page.problem_committed.connect(self._on_intake_committed)
        self.intake_page.status_message.connect(
            lambda message: self.statusBar().showMessage(message)
        )
        self.intake_page.dashboard_requested.connect(self._show_dashboard)
        self.intake_page.open_problem_requested.connect(self._open_problem_from_intake)
        self.stack.addWidget(self.intake_page)
        self.stack.addWidget(self._build_library_page())
        self.stack.addWidget(self._build_review_page())
        self.stack.addWidget(self._build_data_page())
        self.stack.addWidget(self._build_settings_page())
        self.problem_detail_page = ProblemDetailPage()
        self.problem_detail_page.back_requested.connect(self._close_problem_detail)
        self.problem_detail_page.edit_requested.connect(self._edit_problem_from_detail)
        self.stack.addWidget(self.problem_detail_page)
        layout.addWidget(self.stack, stretch=1)

        self.setCentralWidget(root)
        self.main_nav.setCurrentRow(0)

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("AppSidebar")
        side.setFixedWidth(200)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 18, 14, 14)
        lay.setSpacing(4)

        brand = QLabel("研错库")
        brand.setObjectName("BrandTitle")
        sub = QLabel("本地优先错题本")
        sub.setObjectName("BrandSubtitle")
        lay.addWidget(brand)
        lay.addWidget(sub)

        self.main_nav = QListWidget()
        self.main_nav.setObjectName("MainNav")
        self.main_nav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for label, page in (
            ("工作台", _PAGE_DASHBOARD),
            ("录题", _PAGE_INTAKE),
            ("题库", _PAGE_LIBRARY),
            ("复习", _PAGE_REVIEW),
            ("数据与同步", _PAGE_DATA),
            ("设置", _PAGE_SETTINGS),
        ):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.main_nav.addItem(item)
        self.main_nav.currentRowChanged.connect(self._on_main_nav)
        self.main_nav.itemClicked.connect(self._on_main_nav_clicked)
        lay.addWidget(self.main_nav, stretch=1)

        stats = QLabel()
        stats.setObjectName("MutedLabel")
        stats.setWordWrap(True)
        self.sidebar_stats = stats
        lay.addWidget(stats)
        return side

    def _on_main_nav(self, row: int) -> None:
        if row < 0:
            return
        item = self.main_nav.item(row)
        page = item.data(Qt.ItemDataRole.UserRole) if item else _PAGE_LIBRARY
        self.stack.setCurrentIndex(int(page))
        if page == _PAGE_DASHBOARD:
            self._refresh_focus_pages()
        elif page == _PAGE_LIBRARY:
            self.refresh_problems()
        elif page in (_PAGE_REVIEW, _PAGE_SETTINGS):
            self._refresh_focus_pages()

    def _on_main_nav_clicked(self, item: QListWidgetItem) -> None:
        """Re-open an already selected section when a nested page is active."""

        page = int(item.data(Qt.ItemDataRole.UserRole))
        if self.stack.currentIndex() != page:
            self._on_main_nav(self.main_nav.row(item))

    def _build_status(self) -> None:
        self.status = QStatusBar()
        self.setStatusBar(self.status)

    # —— 工作台 ——

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("工作台")
        title.setObjectName("PageTitle")
        hint = QLabel("从一项明确任务开始，未完成的工作会在这里继续。")
        hint.setObjectName("PageHint")
        layout.addWidget(title)
        layout.addWidget(hint)

        self.dashboard_hero = QLabel("开始整理你的第一道错题")
        self.dashboard_hero.setObjectName("HeroBanner")
        layout.addWidget(self.dashboard_hero)

        record = CardFrame()
        record.add_title("录入错题")
        record.add_hint("手动填写，或上传图片让 AI 自动整理。两种方式都在录题页连续完成。")
        manual = primary_button("手动录题")
        manual.clicked.connect(self._show_manual_intake)
        ai = QPushButton("AI 图片录题")
        ai.clicked.connect(self._show_ai_intake)
        record.body.addLayout(button_row(manual, ai))
        layout.addWidget(record)

        row = QHBoxLayout()
        pending = CardFrame()
        pending.add_title("待继续")
        self.dashboard_pending = pending.add_hint("暂无未完成任务")
        continue_intake = QPushButton("继续录题")
        continue_intake.clicked.connect(self._show_intake_home)
        changes = QPushButton("查看待确认变更")
        changes.clicked.connect(self._open_review)
        pending.body.addLayout(button_row(continue_intake, changes))
        row.addWidget(pending, stretch=1)

        review = CardFrame()
        review.add_title("今日复习")
        self.dashboard_review = review.add_hint("正在计算今日任务…")
        start_review = QPushButton("开始今日复习")
        start_review.clicked.connect(self._today_review)
        review.body.addLayout(button_row(start_review))
        row.addWidget(review, stretch=1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _show_dashboard(self) -> None:
        self.main_nav.setCurrentRow(_PAGE_DASHBOARD)

    def _show_intake_home(self) -> None:
        self.main_nav.setCurrentRow(_PAGE_INTAKE)
        self.intake_page.show_home()

    def _show_manual_intake(self) -> None:
        self.main_nav.setCurrentRow(_PAGE_INTAKE)
        self.intake_page.show_manual()

    def _show_ai_intake(self) -> None:
        self.main_nav.setCurrentRow(_PAGE_INTAKE)
        self.intake_page.show_ai_upload()

    def _on_intake_committed(self, problem_id: str) -> None:
        self.refresh_nav()
        self.refresh_problems()
        self._refresh_focus_pages()
        self.status.showMessage(f"题目已入库：{problem_id}")

    def _open_problem_from_intake(self, problem_id: str) -> None:
        self._nav_mode = "active"
        self.main_nav.setCurrentRow(_PAGE_LIBRARY)
        self.refresh_nav()
        self.refresh_problems()
        for index in range(self.problem_list.count()):
            item = self.problem_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == problem_id:
                item.setSelected(True)
                self.problem_list.scrollToItem(item)
                self._on_problem_selected()
                break
        self._open_problem_detail(problem_id)

    # —— 题库页 ——

    def _build_library_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("题库")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("SearchEdit")
        self.search_edit.setPlaceholderText("搜索题目 / 答案 / 备注 / 书名…")
        self.search_edit.setFixedWidth(280)
        self.search_edit.returnPressed.connect(self.refresh_problems)
        header.addWidget(self.search_edit)

        btn_new = primary_button("录入题目")
        btn_new.clicked.connect(self._show_manual_intake)
        btn_import = QPushButton("AI 图片录题")
        btn_import.clicked.connect(self._show_ai_intake)
        btn_more = QPushButton("更多 ▾")
        btn_more.clicked.connect(self._library_more_menu)
        header.addWidget(btn_new)
        header.addWidget(btn_import)
        header.addWidget(btn_more)
        outer.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        filter_wrap = CardFrame()
        filter_wrap.body.setContentsMargins(10, 12, 10, 10)
        filter_wrap.add_title("筛选")
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("FilterNav")
        self.nav_list.currentItemChanged.connect(self._on_nav_changed)
        filter_wrap.body.addWidget(self.nav_list, stretch=1)
        filter_btns = QHBoxLayout()
        b_sub = ghost_button("新建科目")
        b_sub.clicked.connect(self._new_subject)
        b_tag = ghost_button("新建标签")
        b_tag.clicked.connect(self._new_tag)
        filter_btns.addWidget(b_sub)
        filter_btns.addWidget(b_tag)
        filter_wrap.body.addLayout(filter_btns)
        filter_wrap.setMinimumWidth(180)
        filter_wrap.setMaximumWidth(240)
        splitter.addWidget(filter_wrap)

        center = QWidget()
        center_lay = QVBoxLayout(center)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(8)
        list_hint = QLabel("错题列表 · 双击打开详情")
        list_hint.setObjectName("MutedLabel")
        center_lay.addWidget(list_hint)
        self.problem_list = QListWidget()
        self.problem_list.setObjectName("ProblemList")
        self.problem_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.problem_list.itemSelectionChanged.connect(self._on_problem_selected)
        self.problem_list.itemDoubleClicked.connect(self._open_selected_detail)
        center_lay.addWidget(self.problem_list, stretch=1)

        self.context_bar = QFrame()
        self.context_bar.setObjectName("ContextBar")
        ctx = QHBoxLayout(self.context_bar)
        ctx.setContentsMargins(10, 8, 10, 8)
        ctx.setSpacing(8)
        self._ctx_buttons = []
        for text, slot, kind in (
            ("打开详情", self._open_selected_detail, "primary"),
            ("编辑", self._edit_selected, "normal"),
            ("入正式库", self._promote_selected, "normal"),
            ("加入复习", self._schedule_review, "normal"),
            ("AI 补全", self._ai_recognize, "normal"),
            ("撤销 AI 修改", self._undo_ai, "normal"),
            ("删除", self._trash_selected, "danger"),
            ("恢复", self._restore_selected, "normal"),
            ("清空回收站", self._purge_trash, "danger"),
        ):
            if kind == "primary":
                btn = primary_button(text)
            elif kind == "danger":
                btn = danger_button(text)
            else:
                btn = QPushButton(text)
            btn.clicked.connect(slot)
            ctx.addWidget(btn)
            self._ctx_buttons.append(btn)
        ctx.addStretch(1)
        center_lay.addWidget(self.context_bar)
        splitter.addWidget(center)

        detail_card = CardFrame()
        detail_card.add_title("属性")
        self.detail = QLabel("选中一道题查看详情")
        self.detail.setObjectName("MutedLabel")
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        detail_card.body.addWidget(self.detail, stretch=1)
        detail_card.setMinimumWidth(240)
        splitter.addWidget(detail_card)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([200, 640, 280])
        outer.addWidget(splitter, stretch=1)
        return page

    def _library_more_menu(self) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("查重", self._find_duplicates)
        menu.addAction("批量优先级", self._batch_priority)
        menu.addSeparator()
        menu.addAction("导出 Word", self._export_word)
        menu.addAction("导出工作区", self._export_workspace)
        menu.addAction("导入工作区", self._import_workspace)
        sender = self.sender()
        if isinstance(sender, QPushButton):
            menu.exec(sender.mapToGlobal(sender.rect().bottomLeft()))
        else:
            menu.exec(self.cursor().pos())

    def _update_context_bar(self, has_selection: bool) -> None:
        self.context_bar.setVisible(has_selection or self._nav_mode == "trashed")
        for btn in self._ctx_buttons:
            label = btn.text()
            if label == "清空回收站":
                btn.setVisible(self._nav_mode == "trashed")
                btn.setEnabled(True)
            elif label == "恢复":
                btn.setEnabled(has_selection and self._nav_mode == "trashed")
            elif label in ("入正式库", "加入复习", "AI 补全", "撤销 AI 修改"):
                btn.setEnabled(has_selection and self._nav_mode != "trashed")
            else:
                btn.setEnabled(has_selection)

    # —— 复习 / AI / 数据 / 设置页 ——

    def _build_review_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("复习")
        title.setObjectName("PageTitle")
        lay.addWidget(title)

        self.review_hero = QLabel("今日待复习")
        self.review_hero.setObjectName("HeroBanner")
        lay.addWidget(self.review_hero)

        card = CardFrame()
        card.add_title("今日复习")
        card.add_hint("按计划复习错题，巩固薄弱点。可从题库多选后「加入复习」。")
        btn_start = primary_button("开始今日复习")
        btn_start.clicked.connect(self._today_review)
        btn_due = QPushButton("查看题库中的待复习")
        btn_due.clicked.connect(self._goto_due_in_library)
        card.body.addLayout(button_row(btn_start, btn_due))
        lay.addWidget(card)
        lay.addStretch(1)
        return page

    def _build_data_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("数据")
        title.setObjectName("PageTitle")
        hint = QLabel("备份与迁移 · 不是每题实时同步")
        hint.setObjectName("PageHint")
        lay.addWidget(title)
        lay.addWidget(hint)

        pack = CardFrame()
        pack.add_title("完整备份包")
        pack.add_hint("推荐使用 ebpack；zip 为旧版兼容。")
        p1 = primary_button("导出 ebpack")
        p1.clicked.connect(self._export_ebpack)
        p2 = QPushButton("导入 ebpack")
        p2.clicked.connect(self._import_ebpack)
        p3 = QPushButton("备份 (zip)")
        p3.clicked.connect(self._backup)
        p4 = QPushButton("恢复 zip")
        p4.clicked.connect(self._restore_backup)
        pack.body.addLayout(button_row(p1, p2, p3, p4))
        lay.addWidget(pack)

        share = CardFrame()
        share.add_title("分享与工作区")
        share.add_hint("分享包会脱敏；工作区用于外部编辑 Markdown。")
        s1 = QPushButton("导出分享包")
        s1.clicked.connect(self._export_gmshare)
        s2 = QPushButton("导入分享包")
        s2.clicked.connect(self._import_gmshare)
        s3 = QPushButton("导出工作区")
        s3.clicked.connect(self._export_workspace)
        s4 = QPushButton("导入工作区")
        s4.clicked.connect(self._import_workspace)
        share.body.addLayout(button_row(s1, s2, s3, s4))
        lay.addWidget(share)

        cloud = CardFrame()
        cloud.add_title("云备份")
        cloud.add_hint("完整包上传 / 恢复；增量推拉目前主要支持本地文件夹提供商。")
        c1 = primary_button("云备份")
        c1.clicked.connect(self._cloud_backup)
        c2 = QPushButton("云恢复")
        c2.clicked.connect(self._cloud_restore)
        c3 = QPushButton("推送增量")
        c3.clicked.connect(self._sync_push)
        c4 = QPushButton("拉取合并")
        c4.clicked.connect(self._sync_pull)
        cloud.body.addLayout(button_row(c1, c2, c3, c4))
        lay.addWidget(cloud)
        lay.addStretch(1)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        lay.addWidget(title)

        card = CardFrame()
        card.add_title("应用与密钥")
        self.settings_summary = QLabel()
        self.settings_summary.setObjectName("MutedLabel")
        self.settings_summary.setWordWrap(True)
        card.body.addWidget(self.settings_summary)
        btn = primary_button("打开设置…")
        btn.clicked.connect(self._open_settings)
        card.body.addLayout(button_row(btn))
        lay.addWidget(card)

        path_card = CardFrame()
        path_card.add_title("数据目录")
        self.data_path_label = QLabel(str(self.runtime.paths.root))
        self.data_path_label.setWordWrap(True)
        path_card.body.addWidget(self.data_path_label)
        lay.addWidget(path_card)
        lay.addStretch(1)
        return page

    def _goto_due_in_library(self) -> None:
        self.main_nav.setCurrentRow(_PAGE_LIBRARY)
        for i in range(self.nav_list.count()):
            it = self.nav_list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == "due":
                self.nav_list.setCurrentRow(i)
                break

    def _refresh_focus_pages(self) -> None:
        due = 0
        try:
            due = len(
                self.services.list_problems(
                    ProblemFilter(status="active", due_for_review=True)
                )
            )
        except DomainError:
            due = 0
        self.review_hero.setText(f"今日待复习  ·  {due} 题")

        ai = self.runtime.settings.ai
        active = self.services.count_problems("active")
        pending_changes = len(self.ai.list_open_review_items())
        self.dashboard_hero.setText(
            f"正式题库 {active} 题  ·  今日待复习 {due} 题"
            if active
            else "从录入第一道错题开始：手动填写，或上传图片让 AI 整理"
        )
        ai_provider = (
            "Faro API" if ai.default_provider == "openai_compatible" else "Mock"
        )
        self.dashboard_pending.setText(
            f"待确认变更 {pending_changes} 项；"
            f"AI {ai_provider} {'已启用' if ai.enabled else '未启用'}。"
        )
        self.dashboard_review.setText(
            f"今日还有 {due} 道题需要复习。" if due else "今日复习已完成。"
        )
        cloud = self.runtime.settings.cloud
        self.settings_summary.setText(
            f"语言 {self.runtime.settings.application.language} · "
            f"云提供商 {cloud.default_provider} · schema v{self.runtime.schema_version}"
        )
        self.data_path_label.setText(str(self.runtime.paths.root))

    # —— 刷新 ——

    def refresh_all(self) -> None:
        self.refresh_nav()
        self.refresh_problems()
        self._update_status()
        self._refresh_focus_pages()

    def refresh_nav(self) -> None:
        current_mode = self._nav_mode
        self.nav_list.blockSignals(True)
        self.nav_list.clear()
        items = [
            ("正式题库", "active"),
            ("待整理 / 收件箱", "inbox"),
            ("今日复习", "due"),
            ("归档", "archived"),
            ("回收站", "trashed"),
        ]
        for label, mode in items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, mode)
            self.nav_list.addItem(item)

        for sub in self.services.list_subjects():
            item = QListWidgetItem(f"科目 · {sub.name}")
            item.setData(Qt.ItemDataRole.UserRole, f"subject:{sub.id}")
            self.nav_list.addItem(item)

        for i in range(self.nav_list.count()):
            it = self.nav_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == current_mode:
                self.nav_list.setCurrentRow(i)
                break
        else:
            self.nav_list.setCurrentRow(0)
            self._nav_mode = "active"
        self.nav_list.blockSignals(False)

    def _filter_from_nav(self) -> ProblemFilter:
        mode = self._nav_mode
        q = self.search_edit.text().strip() or None
        if mode.startswith("subject:"):
            return ProblemFilter(
                status="active", subject_id=mode.split(":", 1)[1], query=q
            )
        if mode == "due":
            return ProblemFilter(status="active", due_for_review=True, query=q)
        return ProblemFilter(status=mode, query=q)

    def refresh_problems(self) -> None:
        self.problem_list.clear()
        try:
            problems = self.services.list_problems(self._filter_from_nav())
        except DomainError as exc:
            QMessageBox.warning(self, "筛选失败", str(exc))
            return
        for p in problems:
            title = p.title or "(无标题)"
            status = _STATUS_LABELS.get(p.status, p.status)
            tags = " · ".join(t.name for t in (p.tags or []))
            line1 = f"{title}"
            line2 = f"{status}  ·  P{p.priority}"
            if tags:
                line2 += f"  ·  {tags}"
            item = QListWidgetItem(f"{line1}\n{line2}")
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            self.problem_list.addItem(item)
        self._update_status()
        self._update_context_bar(bool(self.problem_list.selectedItems()))

    def _update_status(self) -> None:
        total = self.services.count_problems()
        inbox = self.services.count_problems("inbox")
        active = self.services.count_problems("active")
        trash = self.services.count_problems("trashed")
        summary = f"共 {total} · 收件箱 {inbox} · 正式 {active} · 回收站 {trash}"
        self.status.showMessage(
            f"{summary} · schema v{self.runtime.schema_version} · {self.runtime.paths.root}"
        )
        self.sidebar_stats.setText(summary)

    def _on_nav_changed(
        self, current: QListWidgetItem | None, _prev: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        self._nav_mode = current.data(Qt.ItemDataRole.UserRole) or "active"
        self.refresh_problems()

    def _on_problem_selected(self) -> None:
        items = self.problem_list.selectedItems()
        has = bool(items)
        self._update_context_bar(has)
        if not items:
            self._selected_problem_id = None
            self.detail.setText("选中一道题查看详情")
            self.detail.setObjectName("MutedLabel")
            self.detail.style().unpolish(self.detail)
            self.detail.style().polish(self.detail)
            return
        pid = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_problem_id = pid
        p = self.services.get_problem(pid)
        if not p:
            self.detail.setText("题目不存在")
            return
        assets = "\n".join(
            f"· {a.role}: {a.relative_path}{' (不可变)' if a.is_immutable else ''}"
            for a in (p.assets or [])
        ) or "（无）"
        tags = ", ".join(t.name for t in (p.tags or [])) or "（无）"
        status = _STATUS_LABELS.get(p.status, p.status)
        self.detail.setObjectName("")
        self.detail.setText(
            f"<b>{p.title or '（无标题）'}</b><br>"
            f"<span style='color:#8F959E'>{status} · P{p.priority} · r{p.revision}</span><br><br>"
            f"<b>标签</b><br>{tags}<br><br>"
            f"<b>原题预览</b><br>"
            f"{(p.question_markdown or '（空）')[:300]}<br><br>"
            f"<b>附件</b><br>{assets.replace(chr(10), '<br>')}<br><br>"
            f"<span style='color:#8F959E;font-size:11px'>ID {p.id}</span>"
        )
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.style().unpolish(self.detail)
        self.detail.style().polish(self.detail)

    def _selected_ids(self) -> list[str]:
        return [
            it.data(Qt.ItemDataRole.UserRole)
            for it in self.problem_list.selectedItems()
        ]

    def _require_one(self) -> str | None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择一道题")
            return None
        return ids[0]

    # —— 业务槽（保持行为） ——

    def _new_problem(self) -> None:
        try:
            p = self.services.create_problem(title="新题目", status="inbox")
            self.refresh_problems()
            self._open_editor(p.id)
        except DomainError as exc:
            QMessageBox.warning(self, "创建失败", str(exc))

    def _edit_selected(self) -> None:
        pid = self._require_one()
        if pid:
            self._open_editor(pid)

    def _open_selected_detail(self, *_args) -> None:
        pid = self._require_one()
        if pid:
            self._open_problem_detail(pid)

    def _open_problem_detail(self, problem_id: str) -> None:
        problem = self.services.get_problem(problem_id)
        if not problem:
            QMessageBox.information(self, "无法打开", "题目不存在或已被删除。")
            return

        image_path: Path | None = None
        originals = [asset for asset in (problem.assets or []) if asset.role == "original"]
        candidates = originals or list(problem.assets or [])
        for asset in candidates:
            resolved = self.services.store.resolve(asset.relative_path)
            if resolved.is_file():
                image_path = resolved
                break

        subject_name: str | None = None
        chapter_name: str | None = None
        if problem.subject_id:
            subject_name = next(
                (
                    subject.name
                    for subject in self.services.list_subjects()
                    if subject.id == problem.subject_id
                ),
                None,
            )
            if problem.chapter_id:
                chapter_name = next(
                    (
                        chapter.name
                        for chapter in self.services.list_chapters(problem.subject_id)
                        if chapter.id == problem.chapter_id
                    ),
                    None,
                )

        self._selected_problem_id = problem_id
        self.problem_detail_page.set_problem(
            problem,
            image_path=image_path,
            subject_name=subject_name,
            chapter_name=chapter_name,
        )
        self.stack.setCurrentIndex(_PAGE_PROBLEM_DETAIL)

    def _close_problem_detail(self) -> None:
        self.stack.setCurrentIndex(_PAGE_LIBRARY)
        if self.main_nav.currentRow() != _PAGE_LIBRARY:
            self.main_nav.setCurrentRow(_PAGE_LIBRARY)

    def _edit_problem_from_detail(self, problem_id: str) -> None:
        self._open_editor(problem_id)
        if self.services.get_problem(problem_id):
            self._open_problem_detail(problem_id)
        else:
            self._close_problem_detail()

    def _open_editor(self, problem_id: str) -> None:
        p = self.services.get_problem(problem_id)
        if not p:
            return
        dlg = ProblemEditorDialog(self.services, p, self)
        if dlg.exec():
            self.refresh_problems()
            self._on_problem_selected()

    def _promote_selected(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.services.promote_to_active(pid)
            self.refresh_problems()
        except DomainError as exc:
            QMessageBox.warning(self, "无法转入正式库", str(exc))

    def _trash_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择题目")
            return
        if self.runtime.settings.application.confirm_before_delete:
            if (
                QMessageBox.question(self, "确认", f"将 {len(ids)} 道题移入回收站？")
                != QMessageBox.StandardButton.Yes
            ):
                return
        try:
            for pid in ids:
                self.services.trash_problem(pid)
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _restore_selected(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.services.restore_problem(pid, "inbox")
            self.refresh_problems()
        except DomainError as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _purge_trash(self) -> None:
        if (
            QMessageBox.question(self, "确认", "清空回收站？此操作不可撤销。")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            n = self.services.purge_trashed()
        except DomainError as exc:
            QMessageBox.warning(self, "清空失败", str(exc))
            return
        QMessageBox.information(self, "完成", f"已永久删除 {n} 道题")
        self.refresh_all()

    def _import_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        if not files:
            return
        try:
            result = self.services.import_images([Path(f) for f in files])
            tip = result.get("duplicate_tip") or ""
            QMessageBox.information(
                self,
                "导入完成",
                f"新建 {len(result['created'])} 题，跳过重复 {len(result['skipped'])} 个"
                + (f"\n{tip}" if tip else ""),
            )
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not folder:
            return
        try:
            result = self.services.import_folder(Path(folder))
            QMessageBox.information(
                self,
                "导入完成",
                f"新建 {len(result['created'])} 题，跳过重复 {len(result['skipped'])} 个",
            )
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _export_word(self) -> None:
        ids = self._selected_ids()
        if not ids:
            ids = [
                self.problem_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.problem_list.count())
            ]
        if not ids:
            QMessageBox.information(self, "提示", "没有可导出的题目")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Word",
            str(self.runtime.paths.export_dir / "错题导出.docx"),
            "Word (*.docx)",
        )
        if not path:
            return
        try:
            dest = self.services.export_problems_docx(ids, Path(path))
            QMessageBox.information(self, "导出完成", str(dest))
        except DomainError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _backup(self) -> None:
        try:
            dest = self.services.create_backup()
            QMessageBox.information(self, "备份完成", str(dest))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "备份失败", str(exc))

    def _export_ebpack(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 ebpack",
            str(self.runtime.paths.backup_dir / "yancuo.ebpack"),
            "Yancuo Pack (*.ebpack)",
        )
        if not path:
            return
        try:
            dest = self.ebpack.export_ebpack(Path(path))
            QMessageBox.information(self, "导出完成", str(dest))
        except DomainError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _import_ebpack(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 ebpack",
            str(self.runtime.paths.backup_dir),
            "Yancuo Pack (*.ebpack)",
        )
        if not path:
            return
        target = QFileDialog.getExistingDirectory(
            self, "选择恢复到的数据目录（建议空目录）"
        )
        if not target:
            return
        try:
            result = self.ebpack.restore_ebpack(Path(path), Path(target))
            QMessageBox.information(
                self,
                "恢复完成",
                f"已恢复到：{result['target_root']}\n"
                f"schema v{result['schema_version']}\n"
                "请将 YANCUO_DATA_ROOT 指向该目录后重启。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _export_gmshare(self) -> None:
        ids: list[str] = []
        for item in self.problem_list.selectedItems():
            pid = item.data(Qt.ItemDataRole.UserRole)
            if pid:
                ids.append(str(pid))
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出分享包",
            str(self.runtime.paths.backup_dir / "share.gmshare"),
            "Yancuo Share (*.gmshare)",
        )
        if not path:
            return
        try:
            result = self.gmshare.export_share(
                ids or None,
                dest=Path(path),
                title="研错库分享",
            )
            QMessageBox.information(
                self,
                "分享包已导出",
                f"{result.path}\n题目 {result.problem_count}，图片 {result.asset_count}\n"
                "已默认排除手写作答、私人备注与复习史。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _import_gmshare(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择分享包",
            str(self.runtime.paths.backup_dir),
            "Yancuo Share (*.gmshare)",
        )
        if not path:
            return
        try:
            result = self.gmshare.import_share(Path(path))
            QMessageBox.information(
                self,
                "导入完成",
                f"新建 {result.created}，跳过重复 {result.skipped_duplicates}\n"
                f"package={result.package_id}",
            )
            self.refresh_problems()
        except DomainError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _cloud_backup(self) -> None:
        try:
            self.cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            self.cloud.ensure_repository()
            result = self.cloud.upload_backup()
            QMessageBox.information(
                self,
                "云备份完成",
                f"tag={result['tag']}\nsha256={result['sha256'][:16]}…\n"
                "已先上传完整包，再更新 latest 指针（非实时同步）。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "云备份失败", str(exc))

    def _cloud_restore(self) -> None:
        target = QFileDialog.getExistingDirectory(
            self, "选择恢复到的数据目录（建议空目录）"
        )
        if not target:
            return
        try:
            self.cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            backups = self.cloud.list_backups()
            latest = next((b for b in backups if b.get("is_latest")), None)
            summary = "云端备份列表：\n" + "\n".join(
                f"- {b['tag']}{' (latest)' if b.get('is_latest') else ''}"
                for b in backups[:20]
            )
            if not backups:
                QMessageBox.information(self, "云恢复", "没有可恢复的备份")
                return
            if (
                QMessageBox.question(
                    self,
                    "确认恢复",
                    summary
                    + "\n\n将下载 latest（若无则需手动指定）并恢复到所选目录。继续？",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            result = self.cloud.restore_latest_to(Path(target))
            QMessageBox.information(
                self,
                "云恢复完成",
                f"{result['target_root']}\n请设置 YANCUO_DATA_ROOT 后重启。\n"
                f"（当前 latest={latest['tag'] if latest else '未知'}）",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "云恢复失败", str(exc))

    def _sync_push(self) -> None:
        try:
            self.sync = SyncService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            result = self.sync.push_operations()
            QMessageBox.information(
                self,
                "推送增量",
                f"已推送 {result['pushed']} 条 Operation（非实时同步；需 local_folder）。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "推送失败", str(exc))

    def _sync_pull(self) -> None:
        try:
            self.sync = SyncService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            result = self.sync.pull_and_merge()
            msg = (
                f"应用 {result['applied']} 条\n"
                f"自动合并字段约 {result['auto_merged_fields']}\n"
                f"冲突字段 {result['conflicts']}\n"
            )
            if result.get("snapshot"):
                msg += f"合并前快照：{result['snapshot']}\n"
            if result.get("review_session_id"):
                msg += "请在工作台打开「待确认变更」处理同步冲突。"
            QMessageBox.information(self, "拉取合并", msg)
            if result.get("review_session_id"):
                ReviewDialog(self.ai, self.services, self).exec()
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "拉取失败", str(exc))

    def _restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择备份包", str(self.runtime.paths.backup_dir), "Zip (*.zip)"
        )
        if not path:
            return
        target = QFileDialog.getExistingDirectory(
            self, "选择恢复到的空数据目录（将写入库与资源）"
        )
        if not target:
            return
        try:
            root = self.services.restore_backup(Path(path), Path(target))
            QMessageBox.information(
                self,
                "恢复完成",
                f"已恢复到：{root}\n请将 YANCUO_DATA_ROOT 指向该目录后重启。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _open_settings(self) -> None:
        SettingsDialog(self.runtime, self).exec()
        self._refresh_focus_pages()

    def _ai_recognize(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择带原图的题目")
            return
        if self._ai_worker and self._ai_worker.isRunning():
            QMessageBox.information(self, "提示", "已有 AI 任务在后台运行")
            return
        try:
            job = self.ai.create_structure_job(ids)
            self._ai_worker = AIJobWorker(self.ai, job.id, self)
            self._ai_worker.finished_ok.connect(self._on_ai_job_done)
            self._ai_worker.failed.connect(self._on_ai_job_fail)
            self._ai_worker.start()
            self.status.showMessage(f"AI 任务已开始：{job.id}（不阻塞界面）")
        except DomainError as exc:
            QMessageBox.warning(self, "无法创建 AI 任务", str(exc))

    def _on_ai_job_done(self, job_id: str) -> None:
        QMessageBox.information(
            self,
            "AI 完成",
            f"任务 {job_id} 已完成。结果可从工作台的「待确认变更」继续处理。",
        )
        self.refresh_all()

    def _on_ai_job_fail(self, job_id: str, err: str) -> None:
        QMessageBox.warning(self, "AI 失败", f"{job_id}\n{err}")

    def _open_review(self) -> None:
        ReviewDialog(self.ai, self.services, self).exec()
        self.refresh_all()

    def _undo_ai(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.ai.undo_last_ai_accept(pid)
            QMessageBox.information(self, "已撤销", "已恢复到接受之前的内容。")
            self.refresh_problems()
            self._on_problem_selected()
        except DomainError as exc:
            QMessageBox.warning(self, "无法撤销", str(exc))

    def _export_workspace(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要导出的题目")
            return
        try:
            dest = self.workspace.export_workspace(ids)
            QMessageBox.information(
                self,
                "导出完成",
                f"{dest}\n\n请只编辑工作区内的 Markdown/JSON，不要直接改数据库。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _import_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "选择工作区目录（含 manifest.json）",
            str(self.runtime.paths.workspace_dir),
        )
        if not folder:
            return
        try:
            result = self.workspace.import_workspace(Path(folder))
            msg = (
                f"已生成审核项 {len(result['items'])} 个，"
                f"其中冲突 {len(result['conflicts'])} 个。\n"
                "请在「待确认变更」中查看差异。"
            )
            if result["errors"]:
                msg += "\n\n部分失败：\n" + "\n".join(result["errors"][:10])
            QMessageBox.information(self, "导入完成", msg)
            self._open_review()
        except DomainError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _today_review(self) -> None:
        TodayReviewDialog(self.services, self).exec()
        self.refresh_all()

    def _schedule_review(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择题目")
            return
        try:
            for pid in ids:
                self.services.schedule_initial_review(pid)
            QMessageBox.information(self, "完成", f"已将 {len(ids)} 题加入今日复习")
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _find_duplicates(self) -> None:
        pid = self._selected_ids()[0] if self._selected_ids() else None
        DuplicateDialog(self.services, focus_problem_id=pid, parent=self).exec()

    def _batch_priority(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择题目")
            return
        value, ok = QInputDialog.getInt(self, "批量优先级", "优先级 1–5：", 3, 1, 5)
        if not ok:
            return
        try:
            n = self.services.batch_update_problems(ids, priority=value)
            QMessageBox.information(self, "完成", f"已更新 {n} 题")
            self.refresh_problems()
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _new_subject(self) -> None:
        name, ok = QInputDialog.getText(self, "新建科目", "科目名称：")
        if not ok or not name.strip():
            return
        try:
            self.services.create_subject(name.strip())
            self.refresh_nav()
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "新建标签", "标签名称：")
        if not ok or not name.strip():
            return
        try:
            self.services.create_tag(name.strip())
            QMessageBox.information(self, "完成", f"已创建标签：{name.strip()}")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))
