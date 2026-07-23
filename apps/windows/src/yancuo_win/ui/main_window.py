"""主窗口：侧栏分页 + 题库三栏（现代化壳，业务槽复用）。"""

from __future__ import annotations

from html import escape
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.ai_service import AIService
from yancuo_win.application.ai_search_service import (
    AiSearchDisclosure,
    AiSearchResult,
    AiSearchService,
)
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.cloud_service import CloudBackupService
from yancuo_win.application.intake_service import ProblemIntakeService
from yancuo_win.application.search_service import SearchIndexService
from yancuo_win.application.search_spec import SearchBoundary
from yancuo_win.application.services import AppServices, ProblemFilter
from yancuo_win.application.sync_service import SyncService
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.data.models import Problem
from yancuo_win.domain.rules import DomainError
from yancuo_win.import_export.ebpack import EbpackService
from yancuo_win.import_export.gmshare import GmshareService
from yancuo_win.import_export.workspace import WorkspaceService
from yancuo_win.tasks.worker import AIJobWorker
from yancuo_win.tasks.search_worker import AiSearchWorker
from yancuo_win.ui.duplicate_dialog import DuplicateDialog
from yancuo_win.ui.intake_page import IntakePage
from yancuo_win.ui.problem_detail import ProblemDetailPage
from yancuo_win.ui.problem_editor import ProblemEditorDialog
from yancuo_win.ui.review_dialog import ReviewDialog
from yancuo_win.ui.review_page import ReviewPage
from yancuo_win.ui.settings_dialog import SettingsDialog
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
_NAV_PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class MainWindow(QMainWindow):
    def __init__(self, runtime: RuntimeContext) -> None:
        super().__init__()
        self.runtime = runtime
        self.services = AppServices(runtime)
        self.search = SearchIndexService(runtime)
        self.ai_search = AiSearchService(runtime)
        self.ai = AIService(runtime)
        self.intake = ProblemIntakeService(runtime)
        self.workspace = WorkspaceService(runtime)
        self.ebpack = EbpackService(runtime)
        self.gmshare = GmshareService(runtime)
        self.cloud = CloudBackupService(runtime)
        self.sync = SyncService(runtime)
        self._library_view = "browse"
        self._library_modes = {
            "browse": "active",
            "process": "inbox",
        }
        self._knowledge_expanded_modes: set[str] = set()
        self._knowledge_scroll_value = 0
        self._nav_mode = "active"
        self._selected_problem_id: str | None = None
        self._ai_worker: AIJobWorker | None = None
        self._ai_search_worker: AiSearchWorker | None = None
        self._ai_search_query = ""
        self._ai_search_problem_ids: list[str] | None = None
        self._ai_search_matches = {}
        self._ai_search_result: AiSearchResult | None = None
        self._ctx_buttons: list[QPushButton] = []
        self._detail_return_page = _PAGE_LIBRARY

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
        if self._ai_search_worker and self._ai_search_worker.isRunning():
            worker = self._ai_search_worker
            worker.cancel()
            if not worker.wait(3000):
                worker.setParent(None)
                worker.finished.connect(worker.deleteLater)
            self._ai_search_worker = None
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
        self.problem_detail_page.previous_requested.connect(
            lambda: self._move_problem_detail(-1)
        )
        self.problem_detail_page.next_requested.connect(
            lambda: self._move_problem_detail(1)
        )
        self.problem_detail_page.schedule_review_requested.connect(
            self._schedule_problem_from_detail
        )
        self.problem_detail_page.favorite_requested.connect(
            self._favorite_problem_from_detail
        )
        self.problem_detail_page.archive_requested.connect(
            self._archive_problem_from_detail
        )
        self.problem_detail_page.trash_requested.connect(self._trash_problem_from_detail)
        self.problem_detail_page.restore_requested.connect(
            self._restore_problem_from_detail
        )
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
        elif page == _PAGE_REVIEW:
            self.review_page.reload_queue(preserve_current=True)
            self._refresh_focus_pages()
        elif page == _PAGE_SETTINGS:
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
        record.add_hint("推荐上传图片让 AI 自动整理；手动填写作为补充。两种方式都在录题页连续完成。")
        ai = primary_button("AI 图片录题")
        ai.clicked.connect(self._show_ai_intake)
        manual = QPushButton("手动录题")
        manual.clicked.connect(self._show_manual_intake)
        record.body.addLayout(button_row(ai, manual))
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
        self._refresh_problem_item(problem_id)
        self._refresh_focus_pages()
        self.status.showMessage(f"题目已入库：{problem_id}")

    def _open_problem_from_intake(self, problem_id: str) -> None:
        self._library_modes["browse"] = "active"
        if self._library_view != "browse":
            self._set_library_view("browse")
        self._nav_mode = "active"
        self.main_nav.setCurrentRow(_PAGE_LIBRARY)
        self.refresh_nav()
        self._refresh_problem_item(problem_id, select=True)
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

        btn_import = primary_button("AI 图片录题")
        btn_import.clicked.connect(self._show_ai_intake)
        btn_new = QPushButton("手动录题")
        btn_new.clicked.connect(self._show_manual_intake)
        btn_more = QPushButton("更多 ▾")
        btn_more.clicked.connect(self._library_more_menu)
        header.addWidget(btn_import)
        header.addWidget(btn_new)
        header.addWidget(btn_more)
        outer.addLayout(header)

        search_bar = QFrame()
        search_bar.setObjectName("SearchToolbar")
        search_row = QHBoxLayout(search_bar)
        search_row.setContentsMargins(8, 8, 8, 8)
        search_row.setSpacing(8)
        self.search_mode_group = QButtonGroup(self)
        self.search_mode_group.setExclusive(True)
        self.local_search_button = QPushButton("普通搜索")
        self.local_search_button.setCheckable(True)
        self.local_search_button.setChecked(True)
        self.ai_search_button = QPushButton("AI 搜索")
        self.ai_search_button.setCheckable(True)
        self.ai_search_button.setToolTip(
            "只向当前范围内的有限候选发送标题、题干、路径、标签和更新时间"
        )
        for button in (self.local_search_button, self.ai_search_button):
            button.setObjectName("SearchModeButton")
            self.search_mode_group.addButton(button)
            search_row.addWidget(button)
            button.clicked.connect(self._on_search_mode_changed)

        self.search_scope_combo = QComboBox()
        self.search_scope_combo.setObjectName("SearchScopeCombo")
        self.search_scope_combo.addItem("当前范围", "current")
        self.search_scope_combo.addItem("全部正式题目", "all_active")
        self.search_scope_combo.setMinimumWidth(190)
        self.search_scope_combo.currentIndexChanged.connect(
            self._on_search_scope_changed
        )
        search_row.addWidget(self.search_scope_combo)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("SearchEdit")
        self.search_edit.setPlaceholderText("搜索题目、答案、解析、标签、备注或来源…")
        self.search_edit.returnPressed.connect(self._submit_library_search)
        self.search_edit.textEdited.connect(self._on_search_text_edited)
        search_row.addWidget(self.search_edit, stretch=1)
        self.search_button = primary_button("搜索")
        self.search_button.clicked.connect(self._submit_library_search)
        clear_search = ghost_button("清除")
        clear_search.clicked.connect(self._clear_library_search)
        search_row.addWidget(self.search_button)
        search_row.addWidget(clear_search)
        outer.addWidget(search_bar)

        self.search_privacy_hint = QLabel(
            "普通搜索完全离线，只查询本机索引；AI 搜索尚未开放，不会发送题目内容。"
        )
        self.search_privacy_hint.setObjectName("MutedLabel")
        self.search_privacy_hint.setWordWrap(True)
        outer.addWidget(self.search_privacy_hint)

        view_row = QHBoxLayout()
        view_row.setSpacing(8)
        self.library_view_group = QButtonGroup(self)
        self.library_view_group.setExclusive(True)
        self.library_browse_button = QPushButton("浏览题库")
        self.library_process_button = QPushButton("处理中心")
        for button, view in (
            (self.library_browse_button, "browse"),
            (self.library_process_button, "process"),
        ):
            button.setObjectName("LibraryViewButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, target=view: self._set_library_view(target)
            )
            self.library_view_group.addButton(button)
            view_row.addWidget(button)
        self.library_browse_button.setChecked(True)
        self.library_view_hint = QLabel(
            "按科目与知识结构浏览正式题目；待整理、归档和回收站集中在处理中心。"
        )
        self.library_view_hint.setObjectName("MutedLabel")
        view_row.addWidget(self.library_view_hint)
        view_row.addStretch(1)
        outer.addLayout(view_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        filter_wrap = CardFrame()
        filter_wrap.body.setContentsMargins(10, 12, 10, 10)
        self.library_nav_title = filter_wrap.add_title("知识浏览")
        self.library_nav_hint = filter_wrap.add_hint("正式题目按科目查看")
        self.library_nav_stack = QStackedWidget()
        self.knowledge_tree = QTreeWidget()
        self.knowledge_tree.setObjectName("KnowledgeTree")
        self.knowledge_tree.setHeaderHidden(True)
        self.knowledge_tree.setIndentation(16)
        self.knowledge_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.knowledge_tree.customContextMenuRequested.connect(
            self._show_catalog_context_menu
        )
        self.knowledge_tree.currentItemChanged.connect(
            self._on_knowledge_nav_changed
        )
        self.process_nav = QListWidget()
        self.process_nav.setObjectName("FilterNav")
        self.process_nav.currentItemChanged.connect(self._on_process_nav_changed)
        self.library_nav_stack.addWidget(self.knowledge_tree)
        self.library_nav_stack.addWidget(self.process_nav)
        filter_wrap.body.addWidget(self.library_nav_stack, stretch=1)
        filter_btns = QHBoxLayout()
        self.new_subject_button = ghost_button("新建科目")
        self.new_subject_button.clicked.connect(self._new_subject)
        self.new_tag_button = ghost_button("新建标签")
        self.new_tag_button.clicked.connect(self._new_tag)
        self.catalog_menu_button = ghost_button("目录操作 ▾")
        self.catalog_menu_button.clicked.connect(self._show_catalog_menu)
        filter_btns.addWidget(self.new_subject_button)
        filter_btns.addWidget(self.new_tag_button)
        filter_btns.addWidget(self.catalog_menu_button)
        filter_wrap.body.addLayout(filter_btns)
        filter_wrap.setMinimumWidth(210)
        filter_wrap.setMaximumWidth(300)
        splitter.addWidget(filter_wrap)

        center = QWidget()
        center_lay = QVBoxLayout(center)
        center_lay.setContentsMargins(0, 0, 0, 0)
        center_lay.setSpacing(8)
        self.library_breadcrumb = QLabel("题库 / 全部正式题目")
        self.library_breadcrumb.setObjectName("LibraryBreadcrumb")
        center_lay.addWidget(self.library_breadcrumb)
        self.library_list_hint = QLabel("正式题目 · 双击打开详情")
        self.library_list_hint.setObjectName("MutedLabel")
        center_lay.addWidget(self.library_list_hint)
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
            ("移动分类", self._move_selected_category, "normal"),
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

    def _set_library_view(self, view: str) -> None:
        if view not in {"browse", "process"}:
            raise ValueError(f"unknown library view: {view}")
        if view == self._library_view:
            return
        self._invalidate_ai_search(cancel=True)
        if self._library_view == "browse":
            self._capture_knowledge_tree_state()
        self._library_modes[self._library_view] = self._nav_mode
        self._library_view = view
        self._nav_mode = self._library_modes[view]
        self.library_browse_button.setChecked(view == "browse")
        self.library_process_button.setChecked(view == "process")
        self.library_nav_stack.setCurrentIndex(0 if view == "browse" else 1)
        self.refresh_nav()
        self.refresh_problems()

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
            elif label in (
                "入正式库",
                "加入复习",
                "AI 补全",
                "撤销 AI 修改",
                "移动分类",
            ):
                btn.setEnabled(has_selection and self._nav_mode != "trashed")
            else:
                btn.setEnabled(has_selection)

    # —— 复习 / AI / 数据 / 设置页 ——

    def _build_review_page(self) -> QWidget:
        self.review_page = ReviewPage(self.services)
        self.review_page.status_message.connect(
            lambda message: self.statusBar().showMessage(message)
        )
        self.review_page.open_problem_requested.connect(self._open_problem_detail)
        self.review_page.queue_changed.connect(self._refresh_focus_pages)
        return self.review_page

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

        search_card = CardFrame()
        search_card.add_title("本地搜索索引")
        self.search_index_summary = QLabel(
            self.search.check_consistency().summary
        )
        self.search_index_summary.setObjectName("MutedLabel")
        self.search_index_summary.setWordWrap(True)
        search_card.body.addWidget(self.search_index_summary)
        rebuild_search = ghost_button("检查并重建搜索索引")
        rebuild_search.clicked.connect(self._rebuild_search_index)
        search_card.body.addLayout(button_row(rebuild_search))
        lay.addWidget(search_card)
        lay.addStretch(1)
        return page

    def _goto_due_in_library(self) -> None:
        self.main_nav.setCurrentRow(_PAGE_LIBRARY)
        if self._library_view != "browse":
            self._set_library_view("browse")
        item = self._find_knowledge_item("due")
        if item is not None:
            self.knowledge_tree.setCurrentItem(item)

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
        if self._library_view == "browse":
            self.library_nav_title.setText("知识浏览")
            self.library_nav_hint.setText("展开科目与章节，父节点包含全部下级题目")
            self.library_view_hint.setText(
                "按科目与知识结构浏览正式题目；待整理、归档和回收站集中在处理中心。"
            )
            self.library_list_hint.setText("正式题目 · 双击打开详情")
            self.new_subject_button.setVisible(True)
            self.new_tag_button.setVisible(True)
            self.catalog_menu_button.setVisible(True)
            self.library_nav_stack.setCurrentIndex(0)
            self._refresh_knowledge_tree(current_mode)
        else:
            self.library_nav_title.setText("处理中心")
            self.library_nav_hint.setText("按生命周期处理题目")
            self.library_view_hint.setText(
                "集中处理待整理、已归档和回收站题目，不与知识目录混排。"
            )
            self.library_list_hint.setText("待处理题目 · 双击打开详情")
            self.new_subject_button.setVisible(False)
            self.new_tag_button.setVisible(False)
            self.catalog_menu_button.setVisible(False)
            self.library_nav_stack.setCurrentIndex(1)
            self._refresh_process_nav(current_mode)
        self._library_modes[self._library_view] = self._nav_mode
        self._update_library_breadcrumb()

    def _capture_knowledge_tree_state(self) -> None:
        expanded: set[str] = set()

        def visit(item: QTreeWidgetItem) -> None:
            mode = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
            if item.isExpanded() and mode:
                expanded.add(mode)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.knowledge_tree.topLevelItemCount()):
            visit(self.knowledge_tree.topLevelItem(index))
        self._knowledge_expanded_modes = expanded
        self._knowledge_scroll_value = (
            self.knowledge_tree.verticalScrollBar().value()
        )

    def _iter_knowledge_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []

        def visit(item: QTreeWidgetItem) -> None:
            items.append(item)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.knowledge_tree.topLevelItemCount()):
            visit(self.knowledge_tree.topLevelItem(index))
        return items

    def _find_knowledge_item(self, mode: str) -> QTreeWidgetItem | None:
        return next(
            (
                item
                for item in self._iter_knowledge_items()
                if item.data(0, Qt.ItemDataRole.UserRole) == mode
            ),
            None,
        )

    @staticmethod
    def _set_tree_item_data(
        item: QTreeWidgetItem,
        *,
        mode: str,
        path: str,
    ) -> None:
        item.setData(0, Qt.ItemDataRole.UserRole, mode)
        item.setData(0, _NAV_PATH_ROLE, path)

    def _append_chapter_nodes(
        self,
        parent: QTreeWidgetItem,
        nodes,
        *,
        subject_name: str,
    ) -> None:
        for node in nodes:
            item = QTreeWidgetItem(
                [f"{node.name} · {node.total_problem_count}"]
            )
            mode = f"chapter:{node.subject_id}:{node.chapter_id}"
            self._set_tree_item_data(
                item,
                mode=mode,
                path=f"题库 / {subject_name} / {node.path_label}",
            )
            parent.addChild(item)
            self._append_chapter_nodes(
                item,
                node.children,
                subject_name=subject_name,
            )

    def _refresh_knowledge_tree(self, current_mode: str) -> None:
        self._capture_knowledge_tree_state()
        self.knowledge_tree.blockSignals(True)
        self.knowledge_tree.clear()

        all_item = QTreeWidgetItem(["全部正式题目"])
        self._set_tree_item_data(
            all_item,
            mode="active",
            path="题库 / 全部正式题目",
        )
        self.knowledge_tree.addTopLevelItem(all_item)
        due_item = QTreeWidgetItem(["今日待复习"])
        self._set_tree_item_data(
            due_item,
            mode="due",
            path="题库 / 今日待复习",
        )
        self.knowledge_tree.addTopLevelItem(due_item)
        favorite_count = len(
            self.services.list_problems(
                ProblemFilter(status="active", favorite_only=True)
            )
        )
        favorite_item = QTreeWidgetItem([f"我的收藏 · {favorite_count}"])
        self._set_tree_item_data(
            favorite_item,
            mode="favorite",
            path="题库 / 我的收藏",
        )
        self.knowledge_tree.addTopLevelItem(favorite_item)
        recent_count = len(
            self.services.list_problems(
                ProblemFilter(status="active", created_within_days=30)
            )
        )
        recent_item = QTreeWidgetItem([f"最近入库 · {recent_count}"])
        self._set_tree_item_data(
            recent_item,
            mode="recent",
            path="题库 / 最近入库（30 天）",
        )
        self.knowledge_tree.addTopLevelItem(recent_item)

        for subject in self.services.list_subjects():
            subject_problems = self.services.list_problems(
                ProblemFilter(status="active", subject_id=subject.id)
            )
            subject_item = QTreeWidgetItem(
                [f"{subject.name} · {len(subject_problems)}"]
            )
            subject_mode = f"subject:{subject.id}"
            self._set_tree_item_data(
                subject_item,
                mode=subject_mode,
                path=f"题库 / {subject.name}",
            )
            self.knowledge_tree.addTopLevelItem(subject_item)

            uncategorized_count = sum(
                problem.chapter_id is None for problem in subject_problems
            )
            uncategorized = QTreeWidgetItem(
                [f"未分类 · {uncategorized_count}"]
            )
            self._set_tree_item_data(
                uncategorized,
                mode=f"uncategorized:{subject.id}",
                path=f"题库 / {subject.name} / 未分类",
            )
            subject_item.addChild(uncategorized)
            self._append_chapter_nodes(
                subject_item,
                self.services.list_chapter_tree(subject.id),
                subject_name=subject.name,
            )

        for item in self._iter_knowledge_items():
            mode = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
            item.setExpanded(mode in self._knowledge_expanded_modes)

        current = self._find_knowledge_item(current_mode)
        if current is None:
            current = all_item
            self._nav_mode = "active"
        self.knowledge_tree.setCurrentItem(current)
        parent = current.parent()
        while parent is not None:
            parent.setExpanded(True)
            mode = str(parent.data(0, Qt.ItemDataRole.UserRole) or "")
            if mode:
                self._knowledge_expanded_modes.add(mode)
            parent = parent.parent()
        self.knowledge_tree.blockSignals(False)
        scrollbar = self.knowledge_tree.verticalScrollBar()
        QTimer.singleShot(
            0,
            lambda value=self._knowledge_scroll_value, bar=scrollbar: bar.setValue(
                min(value, bar.maximum())
            ),
        )

    def _refresh_process_nav(self, current_mode: str) -> None:
        self.process_nav.blockSignals(True)
        self.process_nav.clear()
        for label, mode in (
            (f"待整理 · {self.services.count_problems('inbox')}", "inbox"),
            (f"已归档 · {self.services.count_problems('archived')}", "archived"),
            (f"回收站 · {self.services.count_problems('trashed')}", "trashed"),
        ):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, mode)
            item.setData(
                _NAV_PATH_ROLE,
                f"处理中心 / {label.split(' · ', 1)[0]}",
            )
            self.process_nav.addItem(item)

        for index in range(self.process_nav.count()):
            item = self.process_nav.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == current_mode:
                self.process_nav.setCurrentRow(index)
                break
        else:
            self.process_nav.setCurrentRow(0)
            self._nav_mode = "inbox"
        self.process_nav.blockSignals(False)

    def _update_library_breadcrumb(self) -> None:
        if self._library_view == "browse":
            item = self.knowledge_tree.currentItem()
            path = item.data(0, _NAV_PATH_ROLE) if item else None
        else:
            item = self.process_nav.currentItem()
            path = item.data(_NAV_PATH_ROLE) if item else None
        self.library_breadcrumb.setText(str(path or "题库"))
        self._refresh_search_scope_control()

    def _refresh_search_scope_control(self) -> None:
        if not hasattr(self, "search_scope_combo"):
            return
        current_path = self.library_breadcrumb.text()
        current_label = current_path.split(" / ", 1)[-1]
        self.search_scope_combo.blockSignals(True)
        self.search_scope_combo.setItemText(0, f"当前：{current_label}")
        if self._library_view == "process":
            self.search_scope_combo.setCurrentIndex(0)
            self.search_scope_combo.setEnabled(False)
            self.search_scope_combo.setToolTip(
                "处理中心搜索固定在当前生命周期状态，避免混入其他状态"
            )
        else:
            self.search_scope_combo.setEnabled(True)
            self.search_scope_combo.setToolTip(
                "可搜索当前知识范围，或临时扩展到全部正式题目"
            )
        self.search_scope_combo.blockSignals(False)

    def _on_search_scope_changed(self, _index: int) -> None:
        if self._is_ai_search_mode():
            self._invalidate_ai_search(cancel=True)
            self.search_privacy_hint.setText(
                "AI 搜索范围已变化，请再次点击搜索；普通搜索仍可随时切换。"
            )
            self.refresh_problems()
        elif self.search_edit.text().strip():
            self.refresh_problems()

    def _clear_library_search(self) -> None:
        if not self.search_edit.text() and self._ai_search_problem_ids is None:
            return
        self._invalidate_ai_search(cancel=True)
        self.search_edit.clear()
        self.refresh_problems()

    def _is_ai_search_mode(self) -> bool:
        return self.ai_search_button.isChecked()

    def _on_search_mode_changed(self, _checked: bool = False) -> None:
        if self._is_ai_search_mode():
            self._invalidate_ai_search(cancel=False)
            self.search_edit.setPlaceholderText(
                "描述想找的题目，例如：最近用泰勒展开判断等价阶数的高数题"
            )
            self.search_privacy_hint.setText(
                "AI 会先解析意图并在本机召回；默认仅发送最多 20 条候选的"
                "标题、题干、知识路径、标签和更新时间。"
            )
        else:
            self._invalidate_ai_search(cancel=True)
            self.search_edit.setPlaceholderText(
                "搜索题目、答案、解析、标签、备注或来源…"
            )
            self.search_privacy_hint.setText(
                "普通搜索完全离线，只查询本机索引，不产生 AI 请求或费用。"
            )
        self.refresh_problems()

    def _on_search_text_edited(self, _text: str) -> None:
        if self._is_ai_search_mode():
            self._invalidate_ai_search(cancel=True)
            self.search_privacy_hint.setText(
                "描述已修改，请点击搜索；默认不会发送答案、作答、错因、备注或原图。"
            )

    def _submit_library_search(self) -> None:
        query = self.search_edit.text().strip()
        if not query:
            self._invalidate_ai_search(cancel=True)
            self.refresh_problems()
            return
        if not self._is_ai_search_mode():
            self._invalidate_ai_search(cancel=True)
            self.refresh_problems()
            return
        self._start_ai_search(query)

    def _current_ai_search_boundary(self) -> SearchBoundary:
        use_all_active = (
            self._library_view == "browse"
            and self.search_scope_combo.currentData() == "all_active"
        )
        allowed_problem_ids: frozenset[str] | None = None
        if self._library_view == "process":
            statuses = (self._nav_mode,)
            scope = None
        elif use_all_active:
            statuses = ("active",)
            scope = None
        else:
            statuses = ("active",)
            scope = self._knowledge_scope_from_nav()
            if self._nav_mode in {"due", "favorite", "recent"}:
                allowed_problem_ids = frozenset(
                    problem.id
                    for problem in self.services.list_problems(
                        self._filter_from_nav(include_query=False)
                    )
                )
        return SearchBoundary(
            scope=scope,
            statuses=statuses,
            allowed_problem_ids=allowed_problem_ids,
            max_candidates=50,
            max_results=10,
        )

    def _start_ai_search(self, query: str) -> None:
        if self._ai_search_worker and self._ai_search_worker.isRunning():
            self.status.showMessage("上一轮 AI 搜索正在结束，请稍候", 3000)
            return
        self._invalidate_ai_search(cancel=False)
        try:
            boundary = self._current_ai_search_boundary()
            worker = AiSearchWorker(
                self.ai_search,
                query=query,
                boundary=boundary,
                disclosure=AiSearchDisclosure(),
                parent=self,
            )
        except DomainError as exc:
            self._on_ai_search_failed(str(exc))
            return
        self._ai_search_worker = worker
        worker.progress.connect(self._on_ai_search_progress)
        worker.finished_ok.connect(self._on_ai_search_done)
        worker.failed.connect(self._on_ai_search_failed)
        worker.finished.connect(self._on_ai_search_worker_finished)
        self._set_ai_search_busy(True)
        self.library_list_hint.setText("AI 搜索 · 正在解析搜索意图…")
        self.search_privacy_hint.setText(
            "阶段 1/3：只发送当前搜索描述以生成安全 SearchSpec。"
        )
        worker.start()

    def _on_ai_search_progress(self, stage: str) -> None:
        if self.sender() is not self._ai_search_worker:
            return
        labels = {
            "intent": (
                "AI 搜索 · 正在解析搜索意图…",
                "阶段 1/3：只发送当前搜索描述以生成安全 SearchSpec。",
            ),
            "local_recall": (
                "AI 搜索 · 正在本机召回候选…",
                "阶段 2/3：正在本机执行目录、状态、关键词和结构化筛选。",
            ),
            "rerank": (
                "AI 搜索 · 正在重排有限候选…",
                "阶段 3/3：默认只发送标题、题干、知识路径、标签和更新时间。",
            ),
        }
        if stage in labels:
            hint, privacy = labels[stage]
            self.library_list_hint.setText(hint)
            self.search_privacy_hint.setText(privacy)

    def _on_ai_search_done(self, result: AiSearchResult) -> None:
        if self.sender() is not self._ai_search_worker:
            return
        self._set_ai_search_busy(False)
        self._ai_search_result = result
        self._ai_search_query = result.query
        self._ai_search_problem_ids = [
            match.problem.id for match in result.matches
        ]
        self._ai_search_matches = {
            match.problem.id: match for match in result.matches
        }
        diagnostics = result.diagnostics
        fields = "、".join(diagnostics.disclosed_fields)
        stages = diagnostics.stages_ms
        self.search_privacy_hint.setText(
            f"本次向 {diagnostics.provider}/{diagnostics.model} 发送 "
            f"{diagnostics.candidates_sent} 条候选（{diagnostics.payload_bytes} 字节）；"
            f"字段：{fields}。耗时：意图 {stages.get('intent', 0.0) / 1000:.2f}s、"
            f"本地 {stages.get('local_recall', 0.0) / 1000:.2f}s、"
            f"重排 {stages.get('rerank', 0.0) / 1000:.2f}s、"
            f"总计 {stages.get('total', 0.0) / 1000:.2f}s；"
            f"{diagnostics.total_tokens} tokens，估算费用 "
            f"{diagnostics.cost_estimate:.6f}，请求尝试 {diagnostics.request_attempts} 次。"
        )
        self.refresh_problems()
        self.status.showMessage(
            f"AI 搜索完成：{len(result.matches)} 条推荐，"
            f"{diagnostics.total_tokens} tokens，估算费用 "
            f"{diagnostics.cost_estimate:.6f}",
            8000,
        )

    def _on_ai_search_failed(self, error: str) -> None:
        if self.sender() is not None and self.sender() is not self._ai_search_worker:
            return
        self._set_ai_search_busy(False)
        self._ai_search_problem_ids = None
        self._ai_search_matches = {}
        self._ai_search_result = None
        self.library_list_hint.setText("AI 搜索失败 · 查询内容已保留")
        self.search_privacy_hint.setText(
            f"AI 搜索失败：{error}。可修改后重试，或切换“普通搜索”离线查询。"
        )
        self.status.showMessage("AI 搜索失败；普通搜索仍可使用", 8000)

    def _on_ai_search_worker_finished(self) -> None:
        worker = self.sender()
        if worker is self._ai_search_worker:
            self._ai_search_worker = None
        if worker is not None:
            worker.deleteLater()

    def _set_ai_search_busy(self, busy: bool) -> None:
        self.search_button.setEnabled(not busy)
        self.search_button.setText("AI 搜索中…" if busy else "搜索")
        self.ai_search_button.setEnabled(not busy)
        self.search_scope_combo.setEnabled(
            not busy and self._library_view != "process"
        )

    def _invalidate_ai_search(self, *, cancel: bool) -> None:
        if cancel and self._ai_search_worker and self._ai_search_worker.isRunning():
            self._ai_search_worker.cancel()
        self._ai_search_query = ""
        self._ai_search_problem_ids = None
        self._ai_search_matches = {}
        self._ai_search_result = None
        self._set_ai_search_busy(False)

    def _filter_from_nav(self, *, include_query: bool = True) -> ProblemFilter:
        mode = self._nav_mode
        q = self.search_edit.text().strip() or None if include_query else None
        if mode == "due":
            return ProblemFilter(status="active", due_for_review=True, query=q)
        if mode == "favorite":
            return ProblemFilter(status="active", favorite_only=True, query=q)
        if mode == "recent":
            return ProblemFilter(
                status="active",
                created_within_days=30,
                query=q,
            )
        scope = next(
            (
                item
                for item in self.services.list_knowledge_scopes()
                if item.key == mode
            ),
            None,
        )
        if scope is not None:
            return self.services.filter_for_knowledge_scope(scope, query=q)
        return ProblemFilter(status=mode, query=q)

    def _knowledge_scope_from_nav(self):
        return next(
            (
                scope
                for scope in self.services.list_knowledge_scopes()
                if scope.key == self._nav_mode
            ),
            None,
        )

    def _search_current_view(self, query: str) -> list[Problem]:
        use_all_active = (
            self._library_view == "browse"
            and self.search_scope_combo.currentData() == "all_active"
        )
        if self._library_view == "process":
            statuses = (self._nav_mode,)
            scope = None
        elif use_all_active:
            statuses = ("active",)
            scope = None
        else:
            statuses = ("active",)
            scope = self._knowledge_scope_from_nav()

        hits = self.search.search(
            query,
            scope=scope,
            statuses=statuses,
            limit=200,
        )
        problems = self.services.list_problems_by_ids(
            hit.problem_id for hit in hits
        )
        if (
            not use_all_active
            and self._library_view == "browse"
            and self._nav_mode in {"due", "favorite", "recent"}
        ):
            allowed_ids = {
                problem.id
                for problem in self.services.list_problems(
                    self._filter_from_nav(include_query=False)
                )
            }
            problems = [
                problem for problem in problems if problem.id in allowed_ids
            ]
        return problems

    def _problems_for_current_view(self) -> list[Problem]:
        query = self.search_edit.text().strip()
        if query:
            if self._is_ai_search_mode():
                if (
                    self._ai_search_problem_ids is not None
                    and self._ai_search_query == query
                ):
                    return self.services.list_problems_by_ids(
                        self._ai_search_problem_ids
                    )
                return self.services.list_problems(
                    self._filter_from_nav(include_query=False)
                )
            return self._search_current_view(query)
        return self.services.list_problems(
            self._filter_from_nav(include_query=False)
        )

    def _update_library_list_hint(self, result_count: int | None = None) -> None:
        query = self.search_edit.text().strip()
        if query and result_count is not None:
            scope = self.search_scope_combo.currentText()
            if (
                self._is_ai_search_mode()
                and self._ai_search_result is not None
                and self._ai_search_query == query
            ):
                diagnostics = self._ai_search_result.diagnostics
                total_seconds = diagnostics.stages_ms.get("total", 0.0) / 1000
                self.library_list_hint.setText(
                    f"AI 推荐 · {result_count} 条 · {scope} · "
                    f"本地候选 {diagnostics.candidates_considered} / "
                    f"发送 {diagnostics.candidates_sent} · "
                    f"{total_seconds:.2f}s · {diagnostics.total_tokens} tokens"
                )
            elif self._is_ai_search_mode():
                self.library_list_hint.setText(
                    f"AI 搜索 · {scope} · 输入描述后点击搜索"
                )
            else:
                self.library_list_hint.setText(
                    f"普通搜索 · {result_count} 条结果 · {scope} · 最多显示 200 条"
                )
        elif self._library_view == "browse":
            self.library_list_hint.setText("正式题目 · 双击打开详情")
        else:
            self.library_list_hint.setText("待处理题目 · 双击打开详情")

    @staticmethod
    def _problem_item_text(problem: Problem) -> str:
        title = problem.title or "(无标题)"
        status = _STATUS_LABELS.get(problem.status, problem.status)
        tags = " · ".join(tag.name for tag in (problem.tags or []))
        line2 = f"{status}  ·  P{problem.priority}"
        if tags:
            line2 += f"  ·  {tags}"
        return f"{title}\n{line2}"

    def _make_problem_item(self, problem: Problem) -> QListWidgetItem:
        text = self._problem_item_text(problem)
        match = self._ai_search_matches.get(problem.id)
        if match is not None and self._is_ai_search_mode():
            text += f"\nAI {match.score:.0%} · {match.reason}"
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, problem.id)
        return item

    def _find_problem_item(self, problem_id: str) -> tuple[int, QListWidgetItem | None]:
        for index in range(self.problem_list.count()):
            item = self.problem_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == problem_id:
                return index, item
        return -1, None

    def refresh_problems(self, *, preserve_view: bool = True) -> None:
        selected_ids = set(self._selected_ids()) if preserve_view else set()
        current_item = self.problem_list.currentItem() if preserve_view else None
        current_id = (
            current_item.data(Qt.ItemDataRole.UserRole) if current_item else None
        )
        scroll_value = (
            self.problem_list.verticalScrollBar().value() if preserve_view else 0
        )
        try:
            problems = self._problems_for_current_view()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "搜索或筛选失败", str(exc))
            return
        self.problem_list.blockSignals(True)
        self.problem_list.clear()
        for p in problems:
            item = self._make_problem_item(p)
            self.problem_list.addItem(item)
            if p.id in selected_ids:
                item.setSelected(True)
            if p.id == current_id:
                self.problem_list.setCurrentItem(item)
        self.problem_list.blockSignals(False)
        if preserve_view:
            scrollbar = self.problem_list.verticalScrollBar()
            QTimer.singleShot(
                0,
                lambda value=scroll_value, bar=scrollbar: bar.setValue(
                    min(value, bar.maximum())
                ),
            )
        self._on_problem_selected()
        self._update_library_list_hint(len(problems))
        self._update_status()

    def _refresh_problem_item(
        self,
        problem_id: str,
        *,
        select: bool = False,
        update_summary: bool = True,
    ) -> None:
        """Update one visible row without rebuilding the library list."""

        try:
            matching = next(
                (
                    problem
                    for problem in self._problems_for_current_view()
                    if problem.id == problem_id
                ),
                None,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "刷新失败", str(exc))
            return

        row, item = self._find_problem_item(problem_id)
        self.problem_list.blockSignals(True)
        if matching is None and item is not None:
            was_current = item is self.problem_list.currentItem()
            self.problem_list.takeItem(row)
            if was_current and self.problem_list.count():
                self.problem_list.setCurrentRow(min(row, self.problem_list.count() - 1))
        elif matching is not None and item is None:
            item = self._make_problem_item(matching)
            self.problem_list.insertItem(0, item)
        elif matching is not None and item is not None:
            text = self._problem_item_text(matching)
            match = self._ai_search_matches.get(matching.id)
            if match is not None and self._is_ai_search_mode():
                text += f"\nAI {match.score:.0%} · {match.reason}"
            item.setText(text)
        if select and item is not None and matching is not None:
            self.problem_list.clearSelection()
            self.problem_list.setCurrentItem(item)
            item.setSelected(True)
            self.problem_list.scrollToItem(item)
        self.problem_list.blockSignals(False)
        self._on_problem_selected()
        if update_summary:
            self._update_status()
            self._refresh_focus_pages()

    def _remove_problem_items(self, problem_ids: list[str]) -> None:
        rows = sorted(
            (
                row
                for problem_id in problem_ids
                for row, item in [self._find_problem_item(problem_id)]
                if item is not None
            ),
            reverse=True,
        )
        self.problem_list.blockSignals(True)
        for row in rows:
            self.problem_list.takeItem(row)
        if not self.problem_list.selectedItems() and self.problem_list.count():
            self.problem_list.setCurrentRow(min(rows[-1] if rows else 0, self.problem_list.count() - 1))
        self.problem_list.blockSignals(False)
        self._on_problem_selected()
        self._update_status()
        self._refresh_focus_pages()

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

    def _apply_nav_mode(self, mode: str) -> None:
        self._invalidate_ai_search(cancel=True)
        self._nav_mode = mode
        self._library_modes[self._library_view] = mode
        self._update_library_breadcrumb()
        self.refresh_problems()

    def _on_knowledge_nav_changed(
        self,
        current: QTreeWidgetItem | None,
        _prev: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            return
        self._apply_nav_mode(
            str(current.data(0, Qt.ItemDataRole.UserRole) or "active")
        )

    def _on_process_nav_changed(
        self,
        current: QListWidgetItem | None,
        _prev: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        self._apply_nav_mode(
            str(current.data(Qt.ItemDataRole.UserRole) or "inbox")
        )

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
        match = self._ai_search_matches.get(p.id)
        ai_reason = (
            f"<b>AI 匹配原因</b><br>{escape(match.reason)}"
            f"<br><small>推荐分数 {match.score:.0%}</small><br><br>"
            if match is not None and self._is_ai_search_mode()
            else ""
        )
        self.detail.setObjectName("")
        self.detail.setText(
            f"<b>{p.title or '（无标题）'}</b><br>"
            f"<small>{status} · P{p.priority} · r{p.revision}</small><br><br>"
            f"{ai_reason}"
            f"<b>标签</b><br>{tags}<br><br>"
            f"<b>原题预览</b><br>"
            f"{(p.question_markdown or '（空）')[:300]}<br><br>"
            f"<b>附件</b><br>{assets.replace(chr(10), '<br>')}<br><br>"
            f"<small>ID {p.id}</small>"
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
                        " / ".join(choice.chapter_path)
                        for choice in self.services.list_category_choices()
                        if choice.chapter_id == problem.chapter_id
                    ),
                    None,
                )

        current_page = self.stack.currentIndex()
        if current_page != _PAGE_PROBLEM_DETAIL:
            self._detail_return_page = (
                current_page if 0 <= current_page <= _PAGE_SETTINGS else _PAGE_LIBRARY
            )
        return_labels = {
            _PAGE_DASHBOARD: "← 返回工作台",
            _PAGE_INTAKE: "← 返回录题",
            _PAGE_LIBRARY: "← 返回题库",
            _PAGE_REVIEW: "← 返回复习",
            _PAGE_DATA: "← 返回数据与同步",
            _PAGE_SETTINGS: "← 返回设置",
        }
        self.problem_detail_page.set_back_text(
            return_labels.get(self._detail_return_page, "← 返回")
        )
        self._selected_problem_id = problem_id
        self.problem_detail_page.set_problem(
            problem,
            image_path=image_path,
            subject_name=subject_name,
            chapter_name=chapter_name,
        )
        if self._detail_return_page == _PAGE_REVIEW:
            self.review_page.select_problem(problem_id)
        elif self._detail_return_page == _PAGE_LIBRARY:
            for index in range(self.problem_list.count()):
                item = self.problem_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == problem_id:
                    self.problem_list.setCurrentItem(item)
                    self.problem_list.scrollToItem(item)
                    break
        self.stack.setCurrentIndex(_PAGE_PROBLEM_DETAIL)

    def _close_problem_detail(self) -> None:
        target = self._detail_return_page
        self.stack.setCurrentIndex(target)
        if self.main_nav.currentRow() != target:
            self.main_nav.setCurrentRow(target)

    def _edit_problem_from_detail(self, problem_id: str) -> None:
        self._open_editor(problem_id)
        if self.services.get_problem(problem_id):
            self._open_problem_detail(problem_id)
        else:
            self._close_problem_detail()

    def _detail_problem_ids(self) -> list[str]:
        if self._detail_return_page == _PAGE_REVIEW:
            return self.review_page.problem_ids()
        return [
            self.problem_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.problem_list.count())
        ]

    def _detail_neighbor(self, delta: int) -> str | None:
        current_id = self.problem_detail_page.problem_id
        ids = self._detail_problem_ids()
        if not current_id or current_id not in ids or len(ids) <= 1:
            return None
        index = ids.index(current_id)
        return ids[(index + delta) % len(ids)]

    def _move_problem_detail(self, delta: int) -> None:
        neighbor = self._detail_neighbor(delta)
        if neighbor:
            self._open_problem_detail(neighbor)
        else:
            self.statusBar().showMessage("当前筛选中没有其他题目")

    def _schedule_problem_from_detail(self, problem_id: str) -> None:
        try:
            self.services.schedule_initial_review(problem_id)
            self.review_page.reload_queue(preserve_current=True)
            self._open_problem_detail(problem_id)
            self._refresh_focus_pages()
            self.statusBar().showMessage("已加入今日复习")
        except DomainError as exc:
            QMessageBox.warning(self, "无法加入复习", str(exc))

    def _favorite_problem_from_detail(self, problem_id: str, favorite: bool) -> None:
        try:
            self.services.update_problem(problem_id, {"is_favorite": favorite})
            self._open_problem_detail(problem_id)
            self.statusBar().showMessage("已收藏" if favorite else "已取消收藏")
        except DomainError as exc:
            QMessageBox.warning(self, "无法更新收藏", str(exc))

    def _archive_problem_from_detail(self, problem_id: str) -> None:
        neighbor = self._detail_neighbor(1)
        try:
            self.services.set_problem_status(problem_id, "archived")
            self._after_detail_collection_change(neighbor, "题目已归档")
        except DomainError as exc:
            QMessageBox.warning(self, "无法归档", str(exc))

    def _trash_problem_from_detail(self, problem_id: str) -> None:
        if self.runtime.settings.application.confirm_before_delete:
            if (
                QMessageBox.question(self, "确认删除", "将当前题目移入回收站？")
                != QMessageBox.StandardButton.Yes
            ):
                return
        neighbor = self._detail_neighbor(1)
        try:
            self.services.trash_problem(problem_id)
            self._after_detail_collection_change(neighbor, "题目已移入回收站")
        except DomainError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _restore_problem_from_detail(self, problem_id: str) -> None:
        neighbor = self._detail_neighbor(1)
        try:
            self.services.restore_problem(problem_id, to_status="active")
            self._after_detail_collection_change(neighbor, "题目已恢复到正式题库")
        except DomainError as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _after_detail_collection_change(
        self, neighbor_id: str | None, message: str
    ) -> None:
        changed_id = self.problem_detail_page.problem_id
        self.refresh_nav()
        if changed_id:
            self._refresh_problem_item(changed_id)
        self.review_page.reload_queue(preserve_current=True)
        if neighbor_id and self.services.get_problem(neighbor_id):
            self._open_problem_detail(neighbor_id)
        else:
            self._close_problem_detail()
        self.statusBar().showMessage(message)

    def _open_editor(self, problem_id: str) -> None:
        p = self.services.get_problem(problem_id)
        if not p:
            return
        dlg = ProblemEditorDialog(self.services, p, self)
        if dlg.exec():
            self._refresh_problem_item(problem_id, select=True)

    def _promote_selected(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.services.promote_to_active(pid)
            self.refresh_nav()
            self._refresh_problem_item(pid, select=True)
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
            self.refresh_nav()
            self._remove_problem_items(ids)
            self.review_page.reload_queue(preserve_current=True)
        except DomainError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _restore_selected(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.services.restore_problem(pid, "inbox")
            self.refresh_nav()
            self._refresh_problem_item(pid)
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
            self.refresh_nav()
            for problem_id in result["created"]:
                self._refresh_problem_item(problem_id, update_summary=False)
            self._update_status()
            self._refresh_focus_pages()
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
            self.refresh_nav()
            for problem_id in result["created"]:
                self._refresh_problem_item(problem_id, update_summary=False)
            self._update_status()
            self._refresh_focus_pages()
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

    def _rebuild_search_index(self) -> None:
        self.search_index_summary.setText("正在检查并重建本地索引…")
        self.status.showMessage("正在重建本地搜索索引")
        QApplication.processEvents()
        try:
            count = self.search.rebuild()
            health = self.search.check_consistency()
            self.search_index_summary.setText(health.summary)
            if self.search_edit.text().strip():
                self.refresh_problems()
            self.status.showMessage(f"本地搜索索引已重建：{count} 道题", 5000)
            QMessageBox.information(
                self,
                "搜索索引已重建",
                f"已处理 {count} 道题。\n{health.summary}",
            )
        except Exception as exc:  # noqa: BLE001
            self.search_index_summary.setText(f"重建失败：{exc}")
            self.status.showMessage("本地搜索索引重建失败", 5000)
            QMessageBox.warning(self, "搜索索引重建失败", str(exc))

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
        self._refresh_focus_pages()

    def _on_ai_job_fail(self, job_id: str, err: str) -> None:
        QMessageBox.warning(self, "AI 失败", f"{job_id}\n{err}")

    def _open_review(self) -> None:
        ReviewDialog(self.ai, self.services, self).exec()
        self.refresh_problems(preserve_view=True)
        self._refresh_focus_pages()

    def _undo_ai(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.ai.undo_last_ai_accept(pid)
            QMessageBox.information(self, "已撤销", "已恢复到接受之前的内容。")
            self._refresh_problem_item(pid, select=True)
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
        self.main_nav.setCurrentRow(_PAGE_REVIEW)
        self.review_page.start_session()

    def _schedule_review(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择题目")
            return
        try:
            for pid in ids:
                self.services.schedule_initial_review(pid)
            QMessageBox.information(self, "完成", f"已将 {len(ids)} 题加入今日复习")
            self.review_page.reload_queue(preserve_current=True)
            for pid in ids:
                self._refresh_problem_item(pid, update_summary=False)
            self._update_status()
            self._refresh_focus_pages()
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
            for pid in ids:
                self._refresh_problem_item(pid, update_summary=False)
            self._update_status()
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _show_catalog_context_menu(self, position) -> None:  # noqa: ANN001
        item = self.knowledge_tree.itemAt(position)
        if item is not None:
            self.knowledge_tree.setCurrentItem(item)
        menu = self._build_catalog_menu()
        menu.exec(self.knowledge_tree.viewport().mapToGlobal(position))

    def _show_catalog_menu(self) -> None:
        menu = self._build_catalog_menu()
        menu.exec(
            self.catalog_menu_button.mapToGlobal(
                self.catalog_menu_button.rect().bottomLeft()
            )
        )

    def _build_catalog_menu(self):
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("新建科目", self._new_subject)
        mode = self._nav_mode
        if mode.startswith("subject:"):
            subject_id = mode.split(":", 1)[1]
            menu.addAction(
                "新建一级章节",
                lambda: self._new_chapter(subject_id, None),
            )
            menu.addSeparator()
            menu.addAction("重命名科目", lambda: self._rename_subject(subject_id))
            menu.addAction("科目上移", lambda: self._reorder_subject(subject_id, -1))
            menu.addAction("科目下移", lambda: self._reorder_subject(subject_id, 1))
            menu.addAction("删除科目", lambda: self._delete_subject(subject_id))
        elif mode.startswith("uncategorized:"):
            subject_id = mode.split(":", 1)[1]
            menu.addAction(
                "新建一级章节",
                lambda: self._new_chapter(subject_id, None),
            )
        elif mode.startswith("chapter:"):
            _, subject_id, chapter_id = mode.split(":", 2)
            menu.addAction(
                "新建子章节",
                lambda: self._new_chapter(subject_id, chapter_id),
            )
            menu.addSeparator()
            menu.addAction("重命名章节", lambda: self._rename_chapter(chapter_id))
            menu.addAction(
                "移动到其他上级",
                lambda: self._move_chapter_dialog(subject_id, chapter_id),
            )
            menu.addAction("章节上移", lambda: self._reorder_chapter(chapter_id, -1))
            menu.addAction("章节下移", lambda: self._reorder_chapter(chapter_id, 1))
            menu.addAction(
                "删除章节",
                lambda: self._delete_chapter(subject_id, chapter_id),
            )
        return menu

    def _refresh_catalog_to(self, mode: str) -> None:
        self._library_view = "browse"
        self._nav_mode = mode
        self._library_modes["browse"] = mode
        self.refresh_nav()
        self.refresh_problems()

    def _new_subject(self) -> None:
        name, ok = QInputDialog.getText(self, "新建科目", "科目名称：")
        if not ok or not name.strip():
            return
        try:
            subject = self.services.create_subject(name.strip())
            self._refresh_catalog_to(f"subject:{subject.id}")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _new_chapter(self, subject_id: str, parent_id: str | None) -> None:
        name, ok = QInputDialog.getText(self, "新建章节", "章节名称：")
        if not ok or not name.strip():
            return
        try:
            chapter = self.services.create_chapter(
                subject_id,
                name.strip(),
                parent_id=parent_id,
            )
            self._knowledge_expanded_modes.update(
                {f"subject:{subject_id}", f"chapter:{subject_id}:{parent_id}"}
                if parent_id
                else {f"subject:{subject_id}"}
            )
            self._refresh_catalog_to(
                f"chapter:{subject_id}:{chapter.id}"
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法新建章节", str(exc))

    def _rename_subject(self, subject_id: str) -> None:
        subject = next(
            (item for item in self.services.list_subjects() if item.id == subject_id),
            None,
        )
        if subject is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "重命名科目",
            "科目名称：",
            text=subject.name,
        )
        if not ok or not name.strip():
            return
        try:
            self.services.rename_subject(subject_id, name.strip())
            self._refresh_catalog_to(f"subject:{subject_id}")
        except DomainError as exc:
            QMessageBox.warning(self, "无法重命名", str(exc))

    def _rename_chapter(self, chapter_id: str) -> None:
        chapter = next(
            (
                item
                for subject in self.services.list_subjects()
                for item in self.services.list_chapters(subject.id)
                if item.id == chapter_id
            ),
            None,
        )
        if chapter is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "重命名章节",
            "章节名称：",
            text=chapter.name,
        )
        if not ok or not name.strip():
            return
        try:
            self.services.rename_chapter(chapter_id, name.strip())
            self._refresh_catalog_to(
                f"chapter:{chapter.subject_id}:{chapter_id}"
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法重命名", str(exc))

    def _move_chapter_dialog(self, subject_id: str, chapter_id: str) -> None:
        choices = [
            choice
            for choice in self.services.list_category_choices()
            if choice.subject_id == subject_id and choice.chapter_id is not None
        ]
        current = next(
            (choice for choice in choices if choice.chapter_id == chapter_id),
            None,
        )
        valid = [
            choice
            for choice in choices
            if current is None
            or choice.chapter_path[: len(current.chapter_path)]
            != current.chapter_path
        ]
        labels = ["（科目根目录）", *(choice.label for choice in valid)]
        selected, ok = QInputDialog.getItem(
            self,
            "移动章节",
            "选择新的上级：",
            labels,
            editable=False,
        )
        if not ok:
            return
        parent_id = None
        if selected != labels[0]:
            parent_id = valid[labels.index(selected) - 1].chapter_id
        try:
            self.services.move_chapter(chapter_id, parent_id)
            self._refresh_catalog_to(f"chapter:{subject_id}:{chapter_id}")
        except DomainError as exc:
            QMessageBox.warning(self, "无法移动章节", str(exc))

    def _reorder_subject(self, subject_id: str, delta: int) -> None:
        try:
            self.services.reorder_subject(subject_id, delta)
            self._refresh_catalog_to(f"subject:{subject_id}")
        except DomainError as exc:
            QMessageBox.warning(self, "无法排序", str(exc))

    def _reorder_chapter(self, chapter_id: str, delta: int) -> None:
        chapter = next(
            (
                item
                for subject in self.services.list_subjects()
                for item in self.services.list_chapters(subject.id)
                if item.id == chapter_id
            ),
            None,
        )
        if chapter is None:
            return
        try:
            self.services.reorder_chapter(chapter_id, delta)
            self._refresh_catalog_to(
                f"chapter:{chapter.subject_id}:{chapter_id}"
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法排序", str(exc))

    def _delete_subject(self, subject_id: str) -> None:
        if (
            QMessageBox.question(self, "删除科目", "确认删除这个空科目？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.services.delete_subject(subject_id)
            self._refresh_catalog_to("active")
        except DomainError as exc:
            QMessageBox.warning(self, "无法删除", str(exc))

    def _delete_chapter(self, subject_id: str, chapter_id: str) -> None:
        if (
            QMessageBox.question(self, "删除章节", "确认删除这个空章节？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.services.delete_chapter(chapter_id)
            self._refresh_catalog_to(f"subject:{subject_id}")
        except DomainError as exc:
            QMessageBox.warning(self, "无法删除", str(exc))

    def _move_selected_category(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择题目")
            return
        choices = self.services.list_category_choices()
        labels = ["（未指定科目）", *(choice.label for choice in choices)]
        selected, ok = QInputDialog.getItem(
            self,
            "移动分类",
            f"将 {len(ids)} 道题移动到：",
            labels,
            editable=False,
        )
        if not ok:
            return
        subject_id = None
        chapter_id = None
        if selected != labels[0]:
            choice = choices[labels.index(selected) - 1]
            subject_id = choice.subject_id
            chapter_id = choice.chapter_id
        try:
            count = self.services.move_problems_to_category(
                ids,
                subject_id=subject_id,
                chapter_id=chapter_id,
            )
            self.refresh_nav()
            self.refresh_problems()
            QMessageBox.information(self, "完成", f"已移动 {count} 道题")
        except DomainError as exc:
            QMessageBox.warning(self, "无法移动分类", str(exc))

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "新建标签", "标签名称：")
        if not ok or not name.strip():
            return
        try:
            self.services.create_tag(name.strip())
            QMessageBox.information(self, "完成", f"已创建标签：{name.strip()}")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))
