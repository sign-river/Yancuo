"""主窗口：侧栏分页 + 题库三栏（现代化壳，业务槽复用）。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QDateTime, QPoint, QSize, QTimer, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFontMetrics,
    QKeySequence,
    QPalette,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QMenu,
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
from yancuo_win.application.note_service import NoteService
from yancuo_win.application.problem_chat_service import ProblemChatService
from yancuo_win.application.search_service import SearchIndexHealth, SearchIndexService
from yancuo_win.application.search_spec import SearchBoundary
from yancuo_win.application.services import AppServices, ProblemFilter
from yancuo_win.application.sync_service import SyncService
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.data.models import Problem
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.credentials import get_secret
from yancuo_win.import_export.ebpack import EbpackService
from yancuo_win.import_export.gmshare import GmshareService
from yancuo_win.import_export.workspace import WorkspaceService
from yancuo_win.tasks.worker import AIJobWorker, CallableWorker
from yancuo_win.tasks.search_worker import AiSearchWorker
from yancuo_win.ui.duplicate_dialog import DuplicateDialog
from yancuo_win.ui.icons import bind_icon
from yancuo_win.ui.intake_page import IntakePage
from yancuo_win.ui.math_content import MathContentView, set_preview_zoom_scale
from yancuo_win.ui.note_page import NotePage
from yancuo_win.ui.problem_detail import ProblemDetailPage
from yancuo_win.ui.problem_editor import ProblemEditorDialog
from yancuo_win.ui.review_dialog import ReviewDialog
from yancuo_win.ui.review_page import ReviewPage
from yancuo_win.ui.settings_dialog import ServiceSettingsPage
from yancuo_win.ui.widgets import (
    CardFrame,
    ConfirmDialog,
    CompletionNotification,
    IconButton,
    OperationResultDialog,
    PageHeader,
    SearchInput,
    SoftItemDelegate,
    ToastMessage,
    apply_themed_tree_branches,
    button_row,
    danger_button,
    deferred_view_updates,
    ghost_button,
    primary_button,
    set_tab_order_chain,
)

_PAGE_DASHBOARD = 0
_PAGE_INTAKE = 1
_PAGE_LIBRARY = 2
_PAGE_REVIEW = 3
_PAGE_NOTES = 4
_PAGE_SETTINGS = 5
_PAGE_PROBLEM_DETAIL = 6

_STATUS_LABELS = {
    "inbox": "收件箱",
    "active": "正式",
    "archived": "归档",
    "trashed": "回收站",
}
_NAV_PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 1


@dataclass(frozen=True)
class CatalogNodeContext:
    """The selected catalog node, normalized for all catalog actions."""

    node_type: str
    subject_id: str | None = None
    chapter_id: str | None = None


@dataclass(frozen=True)
class CatalogAction:
    action_id: str
    label: str
    callback: Callable[[], None]
    enabled: bool = True
    separator_before: bool = False
    danger: bool = False
    disabled_hint: str = ""


class _InlineQuestionItem(QWidget):
    """One question row with one header and an optional inline preview."""

    def __init__(
        self,
        problem: Problem,
        *,
        expanded: bool,
        on_toggle: Callable[[], None],
        on_open: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._on_open = on_open
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(180)
        self._click_timer.timeout.connect(self._on_toggle)
        self.setObjectName("InlineQuestionItem")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel(problem.title or "(无标题题目)")
        title.setObjectName("QuestionItemTitle")
        title.setWordWrap(False)
        title.setToolTip(title.text())
        self._title_label = title
        self._title_text = title.text()
        header.addWidget(title, 1)
        chevron = QPushButton()
        chevron.setObjectName("QuestionChevron")
        bind_icon(
            chevron,
            "chevron-up" if expanded else "chevron-down",
            size=16,
        )
        chevron.setToolTip("收起原题预览" if expanded else "展开原题预览")
        chevron.clicked.connect(on_toggle)
        header.addWidget(chevron)
        layout.addLayout(header)
        status = _STATUS_LABELS.get(problem.status, problem.status)
        metadata = QHBoxLayout()
        metadata.setSpacing(8)
        values = [status, f"P{problem.priority}"]
        if problem.problem_type:
            values.append(problem.problem_type)
        values.extend(tag.name for tag in (problem.tags or [])[:3])
        for value in values:
            tag = QLabel(value)
            tag.setObjectName("QuestionMetaTag")
            metadata.addWidget(tag)
        metadata.addStretch(1)
        layout.addLayout(metadata)
        if not expanded:
            self.setFixedHeight(72)
            return

        reader = MathContentView()
        reader.setObjectName("InlineQuestionPreview")
        reader.set_adaptive_content_height(420)
        reader.content_height_changed.connect(self.updateGeometry)
        question_markdown = problem.question_markdown or ""
        if problem.question_latex and problem.question_latex not in question_markdown:
            question_markdown = (
                f"{question_markdown}\n\n$$\n{problem.question_latex}\n$$".strip()
            )
        reader.set_problem(
            {
                "question_markdown": question_markdown,
            },
            include_answers=False,
            show_header=False,
            show_answer_notice=False,
        )
        layout.addWidget(reader)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._title_label.setText(
            QFontMetrics(self._title_label.font()).elidedText(
                self._title_text,
                Qt.TextElideMode.ElideRight,
                max(self._title_label.width(), 0),
            )
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.start()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self._on_open()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, runtime: RuntimeContext) -> None:
        super().__init__()
        self.runtime = runtime
        set_preview_zoom_scale(runtime.settings.application.preview_zoom_scale)
        self.services = AppServices(runtime)
        self.search = SearchIndexService(runtime)
        self.ai_search = AiSearchService(runtime)
        self.ai = AIService(runtime)
        self.problem_chat = ProblemChatService(runtime)
        self.intake = ProblemIntakeService(runtime)
        self.notes = NoteService(runtime)
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
        self._expanded_question_id: str | None = None
        self._ai_worker: AIJobWorker | None = None
        self._ai_search_worker: AiSearchWorker | None = None
        self._search_index_worker: CallableWorker | None = None
        self._cloud_profile_worker: CallableWorker | None = None
        self._cloud_operation_worker: CallableWorker | None = None
        self._pending_cloud_restore: tuple[CloudBackupService, Path, str] | None = None
        self._local_restore_worker: CallableWorker | None = None
        self._local_backup_worker: CallableWorker | None = None
        self._ai_search_query = ""
        self._ai_search_problem_ids: list[str] | None = None
        self._ai_search_matches = {}
        self._ai_search_result: AiSearchResult | None = None
        self._ctx_buttons: list[QPushButton] = []
        self._detail_return_page = _PAGE_LIBRARY
        self._sidebar_collapsed = False
        self._sidebar_narrow_open = False
        self._problem_rows: dict[str, Problem] = {}
        self._materialized_problem_rows: set[int] = set()
        self._problem_widget_timer = QTimer(self)
        self._problem_widget_timer.setSingleShot(True)
        self._problem_widget_timer.timeout.connect(
            self._materialize_visible_problem_widgets
        )

        self.setWindowTitle("研错库")
        self.setMinimumSize(760, 560)
        self.resize(1320, 840)
        self._build_central()
        self._build_status()
        self.toast = ToastMessage(self)
        self.ai_completion_notification = CompletionNotification(self)
        self.ai_completion_notification.activated.connect(
            self._open_completed_ai_review
        )
        self._build_shortcuts()
        self.refresh_all()
        self._update_context_bar(False)
        self._refresh_focus_pages()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.intake_page.shutdown()
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            self._ai_worker.wait(300)
        if self._ai_search_worker and self._ai_search_worker.isRunning():
            worker = self._ai_search_worker
            worker.cancel()
            if not worker.wait(300):
                worker.setParent(None)
                worker.finished.connect(worker.deleteLater)
            self._ai_search_worker = None
        super().closeEvent(event)

    # —— 壳布局 ——

    def _build_central(self) -> None:
        root = QWidget()
        root.setObjectName("PageRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.sidebar_toggle = IconButton("chevron-left", "收起导航栏")
        self.sidebar_toggle.clicked.connect(self._toggle_sidebar)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = self._build_sidebar()
        layout.addWidget(self.sidebar)

        self.sidebar_rail = QFrame()
        self.sidebar_rail.setObjectName("SidebarToggleRail")
        self.sidebar_rail.setFixedWidth(40)
        rail_layout = QVBoxLayout(self.sidebar_rail)
        rail_layout.setContentsMargins(4, 16, 4, 0)
        self.sidebar_expand_button = IconButton("chevron-right", "展开导航栏")
        self.sidebar_expand_button.clicked.connect(self._toggle_sidebar)
        rail_layout.addWidget(self.sidebar_expand_button)
        rail_layout.addStretch(1)
        layout.addWidget(self.sidebar_rail)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_dashboard_page())
        self.intake_page = IntakePage(self.intake)
        self.intake_page.problem_committed.connect(self._on_intake_committed)
        self.intake_page.status_message.connect(
            lambda message: self.statusBar().showMessage(message)
        )
        self.intake_page.ai_review_ready.connect(self._show_ai_completion_notification)
        self.intake_page.dashboard_requested.connect(self._show_dashboard)
        self.intake_page.library_requested.connect(self._show_library)
        self.intake_page.open_problem_requested.connect(self._open_problem_from_intake)
        self.stack.addWidget(self.intake_page)
        self.stack.addWidget(self._build_library_page())
        self.stack.addWidget(self._build_review_page())
        self.note_page = NotePage(self.notes)
        self.note_page.status_message.connect(
            lambda message: self.statusBar().showMessage(message)
        )
        self.note_page.notes_changed.connect(self._refresh_focus_pages)
        self.note_page.add_to_review_requested.connect(self._add_note_to_daily_review)
        self.stack.addWidget(self.note_page)
        self.stack.addWidget(self._build_settings_page())
        self.problem_detail_page = ProblemDetailPage(self.problem_chat)
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
        root_layout.addLayout(layout, stretch=1)

        self.setCentralWidget(root)
        self.main_nav.setCurrentRow(0)
        self._apply_sidebar_visibility()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._apply_sidebar_visibility()
        if hasattr(self, "_problem_widget_timer"):
            self._queue_problem_widget_materialization()

    def _toggle_sidebar(self) -> None:
        if self.width() < 900:
            self._sidebar_narrow_open = not self._sidebar_narrow_open
        else:
            self._sidebar_collapsed = not self._sidebar_collapsed
        self._apply_sidebar_visibility()

    def _apply_sidebar_visibility(self) -> None:
        if not hasattr(self, "sidebar"):
            return
        auto_hidden = self.width() < 900
        if auto_hidden:
            visible = self._sidebar_narrow_open
        else:
            self._sidebar_narrow_open = False
            visible = not self._sidebar_collapsed
        self.sidebar.setVisible(visible)
        self.sidebar_rail.setVisible(not visible)
        self.sidebar_toggle.setToolTip("收起导航栏")
        self.sidebar_toggle.setAccessibleName("收起导航栏")
        self.sidebar_expand_button.setToolTip("展开导航栏")
        self.sidebar_expand_button.setAccessibleName("展开导航栏")
        self._apply_library_workspace_visibility()

    def _apply_library_workspace_visibility(self) -> None:
        if not hasattr(self, "library_navigation_panel"):
            return
        content_width = self.width() - (0 if self.sidebar.isHidden() else 200)
        hide_navigation = content_width < 860
        self.library_navigation_panel.setVisible(not hide_navigation)

    def _build_sidebar(self) -> QFrame:
        side = QFrame()
        side.setObjectName("AppSidebar")
        side.setFixedWidth(200)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(14, 18, 14, 14)
        lay.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(8)
        brand = QLabel("研错库")
        brand.setObjectName("BrandTitle")
        brand_row.addWidget(brand)
        brand_row.addStretch(1)
        brand_row.addWidget(self.sidebar_toggle)
        sub = QLabel("本地优先错题本")
        sub.setObjectName("BrandSubtitle")
        lay.addLayout(brand_row)
        lay.addWidget(sub)

        self.main_nav = QListWidget()
        self.main_nav.setObjectName("MainNav")
        self.main_nav.setAccessibleName("主导航")
        self.main_nav.setAccessibleDescription("使用上下方向键切换工作台、题库、笔记、复习和设置")
        self.main_nav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for label, page, shortcut in (
            ("工作台", _PAGE_DASHBOARD, "Ctrl+1"),
            ("题库", _PAGE_LIBRARY, "Ctrl+2"),
            ("笔记", _PAGE_NOTES, "Ctrl+3"),
            ("复习", _PAGE_REVIEW, "Ctrl+4"),
            ("设置", _PAGE_SETTINGS, "Ctrl+,"),
        ):
            item = QListWidgetItem(label)
            item.setToolTip(f"{label}（{shortcut}）")
            item.setData(Qt.ItemDataRole.UserRole, page)
            self.main_nav.addItem(item)
        self.main_nav.currentRowChanged.connect(self._on_main_nav)
        self.main_nav.itemClicked.connect(self._on_main_nav_clicked)
        self.main_nav.itemActivated.connect(self._on_main_nav_clicked)
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
        if (
            self.stack.currentIndex() == _PAGE_SETTINGS
            and int(page) != _PAGE_SETTINGS
            and not self._confirm_discard_settings()
        ):
            self.main_nav.blockSignals(True)
            self.main_nav.setCurrentRow(
                next(
                    index
                    for index in range(self.main_nav.count())
                    if self.main_nav.item(index).data(Qt.ItemDataRole.UserRole) == _PAGE_SETTINGS
                )
            )
            self.main_nav.blockSignals(False)
            return
        self.stack.setCurrentIndex(int(page))
        if page == _PAGE_DASHBOARD:
            self._refresh_focus_pages()
        elif page == _PAGE_LIBRARY:
            self.refresh_problems()
        elif page == _PAGE_REVIEW:
            self.review_page.show_home()
            self._refresh_focus_pages()
        elif page == _PAGE_NOTES:
            self.note_page.reload()
        elif page == _PAGE_SETTINGS:
            self._refresh_focus_pages()
            self._refresh_account_page()

    def _confirm_discard_settings(self) -> bool:
        pages = (
            getattr(self, "ai_settings_page", None),
            getattr(self, "appearance_settings_page", None),
            getattr(self, "cloud_settings_page", None),
        )
        dirty = [page for page in pages if page is not None and page.has_unsaved_changes]
        if not dirty:
            return True
        choice = QMessageBox.question(
            self,
            "放弃未保存设置",
            "当前设置尚未保存，是否放弃修改？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return False
        for settings_page in dirty:
            settings_page.discard_unsaved_changes()
        return True

    def _on_main_nav_clicked(self, item: QListWidgetItem) -> None:
        """Re-open an already selected section when a nested page is active."""

        page = int(item.data(Qt.ItemDataRole.UserRole))
        if self.stack.currentIndex() != page:
            self._on_main_nav(self.main_nav.row(item))
        if self.width() < 900:
            self._sidebar_narrow_open = False
            self._apply_sidebar_visibility()

    def _show_navigation_page(self, page: int) -> None:
        """Select a visible navigation item by its page value."""

        for row in range(self.main_nav.count()):
            item = self.main_nav.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == page:
                if self.main_nav.currentRow() == row:
                    self._on_main_nav(row)
                else:
                    self.main_nav.setCurrentRow(row)
                return
        self.stack.setCurrentIndex(page)

    def _build_status(self) -> None:
        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def _show_toast(self, message: str) -> None:
        self.toast.show_message(message)

    def _show_ai_completion_notification(self, job_id: str, candidates: int) -> None:
        duration_ms = self.runtime.settings.application.ai_completion_notification_seconds * 1000
        self.ai_completion_notification.enqueue(job_id, candidates, duration_ms)

    def _open_completed_ai_review(self, job_id: str) -> None:
        if not self.intake_page.show_ai_review(job_id):
            self.statusBar().showMessage("该 AI 批次已无待确认题目", 3500)
            return
        self._show_navigation_page(_PAGE_INTAKE)
        self.activateWindow()
        self.raise_()

    def _show_status_toast(self, message: str) -> None:
        self.statusBar().showMessage(message, 3500)
        self._show_toast(message)

    def _show_operation_result(
        self,
        title: str,
        summary: str,
        *,
        details: str = "",
        retry: Callable[[], None] | None = None,
        is_error: bool = False,
    ) -> None:
        dialog = OperationResultDialog(
            title,
            summary,
            details=details,
            is_error=is_error,
            retry_text="重新尝试" if retry is not None else "",
            parent=self,
        )
        if dialog.exec() == OperationResultDialog.RetryCode and retry is not None:
            QTimer.singleShot(0, retry)

    def _build_shortcuts(self) -> None:
        shortcuts = (
            ("打开工作台", "Ctrl+1", _PAGE_DASHBOARD),
            ("打开题库", "Ctrl+2", _PAGE_LIBRARY),
            ("打开笔记", "Ctrl+3", _PAGE_NOTES),
            ("打开复习", "Ctrl+4", _PAGE_REVIEW),
            ("打开设置", "Ctrl+,", _PAGE_SETTINGS),
        )
        self.navigation_shortcuts: list[QShortcut] = []
        for label, sequence, page in shortcuts:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setObjectName(label)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(
                lambda target=page: self._show_navigation_page(target)
            )
            self.navigation_shortcuts.append(shortcut)
        self.focus_search_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.focus_search_shortcut.setObjectName("聚焦当前搜索")
        self.focus_search_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.focus_search_shortcut.activated.connect(self._focus_current_search)

    def _focus_current_search(self) -> None:
        current = self.stack.currentIndex()
        if current == _PAGE_LIBRARY:
            self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        elif current == _PAGE_NOTES:
            self.note_page.note_search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        else:
            self._show_status_toast("当前页面没有可聚焦的搜索框")

    # —— 工作台 ——

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        layout.addWidget(PageHeader("工作台", "从一项明确任务开始，未完成的工作会在这里继续。"))

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
        continue_intake.clicked.connect(self._show_library)
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
        self._show_navigation_page(_PAGE_DASHBOARD)

    def _show_library(self) -> None:
        self._show_navigation_page(_PAGE_LIBRARY)

    def _show_manual_intake(self) -> None:
        self._show_library()
        self.stack.setCurrentIndex(_PAGE_INTAKE)
        self.intake_page.show_manual()

    def _show_ai_intake(self) -> None:
        self._show_library()
        self.stack.setCurrentIndex(_PAGE_INTAKE)
        self.intake_page.show_ai()

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
        self._show_library()
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

    def _build_library_page_legacy(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(12)

        btn_import = primary_button("AI 图片录题")
        btn_import.clicked.connect(self._show_ai_intake)
        btn_new = QPushButton("手动录题")
        btn_new.clicked.connect(self._show_manual_intake)
        btn_more = IconButton("more-horizontal", "更多题库操作")
        btn_more.clicked.connect(self._library_more_menu)
        header = PageHeader("题库", "浏览知识目录、整理题目并进入复习计划。")
        header.add_action(btn_new)
        header.add_action(btn_import)
        header.add_action(btn_more)
        outer.addWidget(header)

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
        self.search_scope_combo.setAccessibleName("题库搜索范围")
        self.search_scope_combo.setObjectName("SearchScopeCombo")
        self.search_scope_combo.addItem("当前范围", "current")
        self.search_scope_combo.addItem("全部正式题目", "all_active")
        self.search_scope_combo.setMinimumWidth(190)
        self.search_scope_combo.currentIndexChanged.connect(
            self._on_search_scope_changed
        )
        search_row.addWidget(self.search_scope_combo)

        self.search_edit = SearchInput("搜索题目、答案、解析、标签、备注或来源")
        self.search_edit.setAccessibleName("搜索题库")
        self.search_edit.setAccessibleDescription("输入关键词后按回车搜索题目")
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
        self.knowledge_tree.setAccessibleName("知识目录")
        self.knowledge_tree.setUniformRowHeights(True)
        self.knowledge_tree.setHeaderHidden(True)
        self.knowledge_tree.setIndentation(16)
        apply_themed_tree_branches(self.knowledge_tree)
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
        self.catalog_menu_button = ghost_button("目录操作")
        bind_icon(self.catalog_menu_button, "chevron-down", size=16)
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
            ("加入复习计划", self._schedule_review, "normal"),
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

    # The compact library workspace supersedes the legacy card-per-column layout
    # above. Keeping the behaviour methods below intact avoids changing data flows.
    def _build_library_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(20, 20, 20, 16)
        outer.setSpacing(12)

        header = PageHeader("\u9898\u5e93 / \u5168\u90e8\u6b63\u5f0f\u9898\u76ee", "")
        self.library_page_title = header.title
        manual = QPushButton("\u624b\u52a8\u5f55\u9898")
        manual.clicked.connect(self._show_manual_intake)
        ai_intake = primary_button("AI \u56fe\u7247\u5f55\u9898")
        ai_intake.clicked.connect(self._show_ai_intake)
        imports = QPushButton("\u66f4\u591a")
        bind_icon(imports, "more-horizontal")
        imports.setToolTip("\u5bfc\u5165\u3001\u5bfc\u51fa\u548c\u6279\u91cf\u9898\u5e93\u64cd\u4f5c")
        imports.clicked.connect(self._library_more_menu)
        for button in (ai_intake, manual, imports):
            button.setMinimumHeight(36)
            button.setMinimumWidth(button.sizeHint().width() + 12)
            header.add_action(button)
        outer.addWidget(header)

        search_bar = QFrame()
        search_bar.setObjectName("SearchToolbar")
        search_row = QHBoxLayout(search_bar)
        search_bar.setFixedHeight(44)
        search_row.setContentsMargins(8, 4, 8, 4)
        search_row.setSpacing(8)
        self.search_mode_group = QButtonGroup(self)
        self.search_mode_group.setExclusive(True)
        self.local_search_button = QPushButton("\u666e\u901a\u641c\u7d22")
        self.ai_search_button = QPushButton("AI \u641c\u7d22")
        self.ai_search_button.setToolTip("AI \u641c\u7d22\u4ec5\u4f1a\u53d1\u9001\u6709\u9650\u5019\u9009\u7684\u7ed3\u6784\u5316\u4fe1\u606f\u3002")
        for button in (self.local_search_button, self.ai_search_button):
            button.setObjectName("SearchModeButton")
            button.setCheckable(True)
            button.setFixedHeight(36)
            self.search_mode_group.addButton(button)
            button.clicked.connect(self._on_search_mode_changed)
            search_row.addWidget(button)
        self.local_search_button.setChecked(True)
        self.search_scope_combo = QComboBox()
        self.search_scope_combo.setObjectName("SearchScopeCombo")
        self.search_scope_combo.addItem("\u5f53\u524d\u8303\u56f4", "current")
        self.search_scope_combo.addItem("\u5168\u90e8\u6b63\u5f0f\u9898\u76ee", "all_active")
        self.search_scope_combo.setMaxVisibleItems(2)
        self.search_scope_combo.setMinimumWidth(150)
        self.search_scope_combo.setFixedHeight(36)
        self.search_scope_combo.currentIndexChanged.connect(self._on_search_scope_changed)
        search_row.addWidget(self.search_scope_combo)
        self.search_edit = SearchInput("\u641c\u7d22\u9898\u76ee\u3001\u7b54\u6848\u3001\u89e3\u6790\u3001\u6807\u7b7e\u3001\u5907\u6ce8\u6216\u6765\u6e90")
        self.search_edit.setAccessibleName("搜索题库")
        self.search_edit.setAccessibleDescription("输入关键词后按回车搜索题目")
        self.search_edit.setFixedHeight(36)
        self.search_edit.returnPressed.connect(self._submit_library_search)
        self.search_edit.textEdited.connect(self._on_search_text_edited)
        search_row.addWidget(self.search_edit, 1)
        self.search_button = primary_button("\u641c\u7d22")
        self.search_button.clicked.connect(self._submit_library_search)
        self.clear_search_button = ghost_button("\u6e05\u7a7a")
        self.clear_search_button.clicked.connect(self._clear_library_search)
        self.clear_search_button.setVisible(False)
        self.search_edit.textChanged.connect(
            lambda value: self.clear_search_button.setVisible(bool(value.strip()))
        )
        for button in (self.search_button, self.clear_search_button):
            button.setFixedHeight(36)
            button.setMinimumWidth(button.sizeHint().width() + 8)
            search_row.addWidget(button)
        set_tab_order_chain(
            self.local_search_button,
            self.ai_search_button,
            self.search_scope_combo,
            self.search_edit,
            self.search_button,
            self.clear_search_button,
        )
        search_bar.setMinimumWidth(0)
        # Kept as a non-visual compatibility field for search-state updates.
        self.search_privacy_hint = QLabel("\u666e\u901a\u641c\u7d22\u5b8c\u5168\u79bb\u7ebf\uff0c\u53ea\u67e5\u8be2\u672c\u5730\u7d22\u5f15\uff1bAI \u641c\u7d22\u4e0d\u4f1a\u53d1\u9001\u9898\u76ee\u6b63\u6587\u3002")
        # Kept as a non-visual compatibility field for the existing refresh flow.
        self.library_view_hint = QLabel()

        tabs = QHBoxLayout()
        tabs.setSpacing(10)
        self.library_view_group = QButtonGroup(self)
        self.library_view_group.setExclusive(True)
        view_switch = QFrame()
        view_switch.setObjectName("LibraryViewSwitch")
        view_switch.setFixedHeight(44)
        view_switch_layout = QHBoxLayout(view_switch)
        view_switch_layout.setContentsMargins(4, 4, 4, 4)
        view_switch_layout.setSpacing(2)
        self.library_browse_button = QPushButton("\u6d4f\u89c8\u9898\u5e93")
        self.library_process_button = QPushButton("\u5904\u7406\u4e2d\u5fc3")
        for button, view in ((self.library_browse_button, "browse"), (self.library_process_button, "process")):
            button.setObjectName("LibraryViewButton")
            button.setCheckable(True)
            button.setFixedHeight(34)
            button.clicked.connect(lambda _checked=False, target=view: self._set_library_view(target))
            self.library_view_group.addButton(button)
            view_switch_layout.addWidget(button)
        self.library_browse_button.setChecked(True)
        tabs.addWidget(view_switch)
        tabs.addWidget(search_bar, 1)
        outer.addLayout(tabs)

        workspace = QFrame()
        workspace.setObjectName("LibraryWorkspace")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(8, 8, 8, 8)
        self.library_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.library_splitter.setObjectName("LibraryWorkspaceSplitter")
        self.library_splitter.setHandleWidth(10)
        workspace_layout.addWidget(self.library_splitter)

        navigation = QFrame()
        navigation.setObjectName("LibraryNavigationPanel")
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)
        nav_header = self._library_panel_header("\u77e5\u8bc6\u6d4f\u89c8", "")
        nav_header.setFixedHeight(48)
        self.library_nav_title = nav_header.findChild(QLabel, "PanelTitle")
        nav_layout.addWidget(nav_header)
        self.library_nav_stack = QStackedWidget()
        self.knowledge_tree = QTreeWidget()
        self.knowledge_tree.setObjectName("KnowledgeTree")
        self.knowledge_tree.setAccessibleName("知识目录")
        self.knowledge_tree.setAccessibleDescription("使用方向键展开科目和章节")
        self.knowledge_tree.setUniformRowHeights(True)
        self.knowledge_tree.setHeaderHidden(True)
        self.knowledge_tree.setIndentation(16)
        apply_themed_tree_branches(self.knowledge_tree)
        tree_palette = self.knowledge_tree.palette()
        for color_group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
        ):
            tree_palette.setColor(
                color_group,
                QPalette.ColorRole.Highlight,
                QColor(0, 0, 0, 0),
            )
        self.knowledge_tree.setPalette(tree_palette)
        self.knowledge_tree.setMouseTracking(True)
        self.knowledge_tree.setItemDelegate(
            SoftItemDelegate(
                self.knowledge_tree,
                radius=9,
                horizontal_margin=4,
                vertical_margin=2,
                minimum_height=38,
            )
        )
        self.knowledge_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.knowledge_tree.customContextMenuRequested.connect(self._show_catalog_context_menu)
        self.knowledge_tree.currentItemChanged.connect(self._on_knowledge_nav_changed)
        self.process_nav = QListWidget()
        self.process_nav.setObjectName("FilterNav")
        self.process_nav.setAccessibleName("题库处理视图")
        self.process_nav.setUniformItemSizes(True)
        self.process_nav.setMouseTracking(True)
        self.process_nav.setItemDelegate(
            SoftItemDelegate(
                self.process_nav,
                radius=9,
                horizontal_margin=4,
                vertical_margin=2,
                minimum_height=38,
            )
        )
        self.process_nav.currentItemChanged.connect(self._on_process_nav_changed)
        self.library_nav_stack.addWidget(self.knowledge_tree)
        self.library_nav_stack.addWidget(self.process_nav)
        nav_layout.addWidget(self.library_nav_stack, 1)
        nav_footer = QFrame()
        nav_footer.setObjectName("LibraryPanelFooter")
        nav_footer.setFixedHeight(56)
        nav_actions = QHBoxLayout(nav_footer)
        nav_actions.setContentsMargins(12, 8, 12, 8)
        self.new_subject_button = QPushButton("\uff0b \u65b0\u5efa")
        self.new_subject_button.setToolTip("\u65b0\u5efa\u79d1\u76ee\u3001\u7ae0\u8282\u6216\u6807\u7b7e")
        self.new_subject_button.clicked.connect(self._show_catalog_create_menu)
        self.new_tag_button = QPushButton("\u7ba1\u7406")
        bind_icon(self.new_tag_button, "chevron-down", size=16)
        self.new_tag_button.setToolTip("\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u79d1\u76ee\u6216\u7ae0\u8282")
        self.new_tag_button.clicked.connect(self._show_catalog_menu)
        self.catalog_menu_button = self.new_tag_button
        nav_actions.addWidget(self.new_subject_button)
        nav_actions.addWidget(self.new_tag_button)
        nav_actions.addStretch(1)
        nav_layout.addWidget(nav_footer)
        navigation.setMinimumWidth(190)
        self.library_navigation_panel = navigation
        self.library_splitter.addWidget(navigation)

        center = QFrame()
        center.setObjectName("LibraryListPanel")
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        list_header = self._library_panel_header("\u9898\u76ee\u5217\u8868", "")
        list_header.setFixedHeight(48)
        self.library_list_header = list_header
        list_header_layout = list_header.layout()
        self.library_breadcrumb = QLabel("\u9898\u5e93 / \u5168\u90e8\u6b63\u5f0f\u9898\u76ee")
        self.library_breadcrumb.setObjectName("LibraryBreadcrumb")
        self.library_breadcrumb.setVisible(False)
        self.library_count_label = QLabel("\u5171 0 \u9898")
        self.library_count_label.setObjectName("MutedLabel")
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.library_count_label)
        self.library_list_hint = QLabel("\u6b63\u5f0f\u9898\u76ee \u00b7 \u53cc\u51fb\u6253\u5f00\u8be6\u60c5")
        self.library_list_hint.setObjectName("MutedLabel")
        self.library_list_hint.setVisible(False)
        list_header_layout.addLayout(row)
        list_header_layout.addWidget(self.library_list_hint)
        center_layout.addWidget(list_header)
        self.problem_list = QListWidget()
        self.problem_list.setObjectName("ProblemList")
        self.problem_list.setAccessibleName("题目列表")
        self.problem_list.setAccessibleDescription("使用方向键选择题目，按回车打开详情")
        self.problem_list.setMouseTracking(True)
        self.problem_list.setItemDelegate(
            SoftItemDelegate(
                self.problem_list,
                radius=10,
                horizontal_margin=4,
                vertical_margin=3,
            )
        )
        self.problem_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.problem_list.itemSelectionChanged.connect(self._on_problem_selected)
        self.problem_list.itemActivated.connect(self._open_selected_detail)
        self.problem_list.verticalScrollBar().valueChanged.connect(
            self._queue_problem_widget_materialization
        )
        center_layout.addWidget(self.problem_list, 1)

        self.question_action_bar = QFrame()
        self.question_action_bar.setObjectName("QuestionActionBar")
        self.question_action_bar.setFixedHeight(56)
        action_layout = QHBoxLayout(self.question_action_bar)
        action_layout.setContentsMargins(16, 8, 16, 8)
        action_layout.setSpacing(8)
        self.question_detail_button = primary_button("打开详情")
        self.question_detail_button.clicked.connect(self._open_selected_detail)
        self.question_edit_button = QPushButton("编辑")
        self.question_edit_button.clicked.connect(self._edit_selected)
        self.question_delete_button = danger_button("删除")
        self.question_delete_button.clicked.connect(self._trash_selected)
        self.question_review_button = QPushButton("加入复习计划")
        self.question_review_button.clicked.connect(self._schedule_review)
        self.question_more_button = QPushButton("更多")
        bind_icon(self.question_more_button, "more-horizontal")
        self.question_more_button.clicked.connect(
            lambda: self._show_question_more_menu(self.question_more_button)
        )
        self._question_action_buttons = (
            self.question_detail_button,
            self.question_edit_button,
            self.question_delete_button,
            self.question_review_button,
            self.question_more_button,
        )
        for button in self._question_action_buttons:
            button.setFixedHeight(32)
            action_layout.addWidget(button)
        action_layout.addStretch(1)
        center_layout.addWidget(self.question_action_bar)
        self.question_action_bar.setVisible(False)
        center.setMinimumWidth(650)
        self.library_splitter.addWidget(center)
        self.library_splitter.setStretchFactor(0, 0)
        self.library_splitter.setStretchFactor(1, 1)
        self.library_splitter.setSizes([230, 900])
        outer.addWidget(workspace, 1)
        return page

    def _library_panel_header(self, title: str, hint: str) -> QFrame:
        header = QFrame()
        header.setObjectName("LibraryPanelHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(2)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("PanelTitle")
            layout.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("PanelHint")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)
        return header

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
        self.note_page.reload()

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

    def _show_catalog_create_menu(self) -> None:
        menu = self._build_catalog_action_menu(self.get_create_actions())
        menu.exec(self.new_subject_button.mapToGlobal(self.new_subject_button.rect().bottomLeft()))

    def _build_question_more_menu(self) -> QMenu:
        menu = QMenu(self)
        selected = bool(self._selected_ids())
        active = selected and self._nav_mode != "trashed"
        promote = menu.addAction("\u8bbe\u4e3a\u6b63\u5f0f\u9898", self._promote_selected)
        promote.setEnabled(active)
        restore = menu.addAction("\u6062\u590d", self._restore_selected)
        restore.setEnabled(selected and self._nav_mode == "trashed")
        menu.addSeparator()
        ai = menu.addAction("AI \u8865\u5168", self._ai_recognize)
        ai.setEnabled(active)
        undo = menu.addAction("\u64a4\u9500 AI \u4fee\u6539", self._undo_ai)
        undo.setEnabled(active)
        menu.addSeparator()
        move = menu.addAction("\u79fb\u52a8\u5206\u7c7b", self._move_selected_category)
        move.setEnabled(active)
        if self._nav_mode == "trashed":
            menu.addSeparator()
            purge = menu.addAction("\u6e05\u7a7a\u56de\u6536\u7ad9", self._purge_trash)
            purge.setEnabled(True)
        return menu

    def _show_question_more_menu(self, sender: QPushButton | None = None) -> None:
        menu = self._build_question_more_menu()
        anchor = sender
        if anchor is not None:
            menu.exec(
                anchor.mapToGlobal(
                    QPoint(0, -menu.sizeHint().height() - 8)
                )
            )
        else:
            menu.exec(self.cursor().pos())

    def _copy_selected_problem_id(self) -> None:
        problem_id = self._require_one()
        if problem_id:
            QApplication.clipboard().setText(problem_id)
            self.status.showMessage("\u5df2\u590d\u5236\u9898\u76ee ID", 2500)

    def _update_context_bar(self, has_selection: bool) -> None:
        for button in getattr(self, "_question_action_buttons", ()):
            button.setEnabled(has_selection)
        if hasattr(self, "question_delete_button"):
            active = has_selection and self._nav_mode != "trashed"
            self.question_delete_button.setVisible(self._nav_mode != "trashed")
            self.question_delete_button.setEnabled(active)
        if hasattr(self, "question_action_bar"):
            self.question_action_bar.setVisible(has_selection)
        if hasattr(self, "copy_problem_id_button"):
            self.copy_problem_id_button.setEnabled(has_selection)

    # —— 复习 / AI / 数据 / 设置页 ——

    def _build_review_page(self) -> QWidget:
        self.review_page = ReviewPage(self.services, self.notes)
        self.review_page.status_message.connect(
            lambda message: self.statusBar().showMessage(message)
        )
        self.review_page.open_problem_requested.connect(self._open_problem_detail)
        self.review_page.queue_changed.connect(self._refresh_focus_pages)
        return self.review_page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        layout.addWidget(PageHeader("设置", "管理本机资料、AI、显示方式与云端同步。"))

        content = QHBoxLayout()
        content.setSpacing(20)
        self.settings_nav = QListWidget()
        self.settings_nav.setObjectName("SettingsNavigation")
        self.settings_nav.setFixedWidth(204)
        self.settings_nav.setAccessibleName("设置栏目")
        self.settings_pages = QStackedWidget()
        self.settings_pages.setObjectName("SettingsContentPages")
        for label, settings_page in (
            ("账户与设备", self._build_account_page()),
            ("AI 服务", ServiceSettingsPage(self.runtime, "ai")),
            ("外观与显示", ServiceSettingsPage(self.runtime, "appearance")),
            ("本地数据", self._build_data_page()),
            ("云端同步", self._build_cloud_sync_page()),
        ):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, self.settings_pages.count())
            self.settings_nav.addItem(item)
            self.settings_pages.addWidget(settings_page)
        self.ai_settings_page = self.settings_pages.widget(1)
        self.appearance_settings_page = self.settings_pages.widget(2)
        self.cloud_settings_page = self.settings_pages.widget(4)
        for service_page in (self.ai_settings_page, self.appearance_settings_page, self.cloud_settings_page):
            service_page.status_message.connect(self._show_status_toast)
        self.settings_nav.currentRowChanged.connect(self.settings_pages.setCurrentIndex)
        self.settings_nav.setCurrentRow(0)
        content.addWidget(self.settings_nav)
        content.addWidget(self.settings_pages, stretch=1)
        layout.addLayout(content, stretch=1)
        return page

    def _build_data_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        lay.addWidget(PageHeader("本地数据", "管理本机存储、备份、导入导出与数据维护。"))
        workspace = QGridLayout()
        workspace.setHorizontalSpacing(16)
        workspace.setVerticalSpacing(16)
        workspace.setColumnStretch(0, 1)
        workspace.setColumnStretch(1, 1)

        pack = CardFrame()
        pack.setProperty("surfaceRole", "data")
        pack.add_title("备份与恢复")
        pack.add_hint("完整备份格式：.ebpack；ZIP 仅用于旧版兼容。")
        self.local_backup_summary = QLabel("尚未在本次会话中创建完整备份。")
        self.local_backup_summary.setObjectName("MutedLabel")
        self.local_backup_summary.setWordWrap(True)
        pack.body.addWidget(self.local_backup_summary)
        self.backup_menu_button = primary_button("备份与恢复")
        self.backup_menu_button.setAccessibleName("备份与恢复操作")
        self.backup_menu_button.setAccessibleDescription(
            "打开菜单，选择完整备份、恢复或旧版 ZIP 操作"
        )
        backup_menu = QMenu(self.backup_menu_button)
        backup_menu.addAction("导出完整备份", self._export_ebpack)
        backup_menu.addAction("导入完整备份", self._import_ebpack)
        backup_menu.addSeparator()
        backup_menu.addAction("创建 ZIP 备份（旧版兼容）", self._backup)
        backup_menu.addAction("从 ZIP 恢复（旧版兼容）", self._restore_backup)
        self.backup_menu_button.setMenu(backup_menu)
        pack.body.addLayout(button_row(self.backup_menu_button))
        workspace.addWidget(pack, 0, 0, 1, 2)

        share = CardFrame()
        share.setProperty("surfaceRole", "data")
        share.add_title("导入与导出")
        share.add_hint("分享包适合发送给其他用户；工作区适合使用 Markdown 外部编辑。")
        self.transfer_menu_button = QPushButton("导入与导出")
        self.transfer_menu_button.setAccessibleName("导入与导出操作")
        self.transfer_menu_button.setAccessibleDescription(
            "打开菜单，选择分享包或工作区的导入与导出操作"
        )
        transfer_menu = QMenu(self.transfer_menu_button)
        transfer_menu.addAction("导出分享包", self._export_gmshare)
        transfer_menu.addAction("导出工作区", self._export_workspace)
        transfer_menu.addSeparator()
        transfer_menu.addAction("导入分享包", self._import_gmshare)
        transfer_menu.addAction("导入工作区", self._import_workspace)
        self.transfer_menu_button.setMenu(transfer_menu)
        share.body.addLayout(button_row(self.transfer_menu_button))

        workspace.addWidget(share, 1, 0)

        path_card = CardFrame()
        path_card.setProperty("surfaceRole", "data")
        path_card.add_title("本机存储")
        path_card.add_hint("本机资料默认离线保存；移动或清理前请先完成备份。")
        local_form = QGridLayout()
        local_form.addWidget(QLabel("语言"), 0, 0)
        local_form.addWidget(QLabel(self.runtime.settings.application.language), 0, 1)
        local_form.addWidget(QLabel("数据根目录"), 1, 0)
        self.data_path_label = QLabel(str(self.runtime.paths.root))
        self.data_path_label.setToolTip(str(self.runtime.paths.root))
        self.data_path_label.setMaximumWidth(680)
        local_form.addWidget(self.data_path_label, 1, 1)
        local_form.addWidget(QLabel("数据库"), 2, 0)
        database_path = QLabel(str(self.runtime.paths.database))
        database_path.setToolTip(str(self.runtime.paths.database))
        database_path.setMaximumWidth(680)
        local_form.addWidget(database_path, 2, 1)
        path_card.body.addLayout(local_form)
        open_data = ghost_button("打开数据目录")
        open_data.clicked.connect(self._open_data_root)
        path_card.body.addLayout(button_row(open_data))

        search_card = CardFrame()
        search_card.setProperty("surfaceRole", "data")
        search_card.add_title("数据维护")
        self.search_index_summary = QLabel()
        self.search_index_summary.setObjectName("MutedLabel")
        self.search_index_summary.setWordWrap(True)
        search_card.body.addWidget(self.search_index_summary)
        self._refresh_search_index_summary()
        self.check_search_button = ghost_button("检查索引")
        self.check_search_button.clicked.connect(self._check_search_index)
        self.rebuild_search_button = ghost_button("检查并重建索引")
        self.rebuild_search_button.clicked.connect(self._rebuild_search_index)
        search_card.body.addLayout(
            button_row(self.check_search_button, self.rebuild_search_button)
        )
        workspace.addWidget(search_card, 1, 1)
        workspace.addWidget(path_card, 2, 0, 1, 2)
        lay.addLayout(workspace)
        lay.addStretch(1)
        return page

    def _build_cloud_sync_page(self) -> QWidget:
        page = ServiceSettingsPage(self.runtime, "cloud")
        page.status_message.connect(self._show_status_toast)
        profiles = CardFrame()
        profiles.setProperty("surfaceRole", "settings")
        profiles.add_title("云端资料")
        profiles.add_hint("查看、恢复或合并自己的远端资料；不会创建研错库在线账户。")
        self.account_remote_summary = QLabel("尚未检查云端资料")
        self.account_remote_summary.setObjectName("MutedLabel")
        self.account_remote_summary.setWordWrap(True)
        profiles.body.addWidget(self.account_remote_summary)
        self.inspect_cloud_profiles_button = QPushButton("查看云端资料")
        self.inspect_cloud_profiles_button.clicked.connect(self._inspect_cloud_profiles)
        restore_profile = QPushButton("恢复指定资料…")
        restore_profile.clicked.connect(self._restore_cloud_profile)
        preview_merge = QPushButton("预检资料合并…")
        preview_merge.clicked.connect(self._preview_cloud_profile_merge)
        merge_profile = QPushButton("合并指定资料…")
        merge_profile.clicked.connect(self._merge_cloud_profile)
        profiles.body.addLayout(
            button_row(self.inspect_cloud_profiles_button, restore_profile, preview_merge, merge_profile)
        )
        operations = CardFrame()
        operations.setProperty("surfaceRole", "settings")
        operations.add_title("备份与同步操作")
        operations.add_hint("云备份创建完整快照；增量推拉不会逐题实时上传。")
        self.cloud_backup_button = primary_button("云备份")
        self.cloud_backup_button.clicked.connect(self._cloud_backup)
        self.cloud_restore_button = danger_button("云恢复")
        self.cloud_restore_button.clicked.connect(self._cloud_restore)
        self.sync_push_button = QPushButton("推送增量")
        self.sync_push_button.clicked.connect(self._sync_push)
        self.sync_pull_button = QPushButton("拉取合并")
        self.sync_pull_button.clicked.connect(self._sync_pull)
        operations.body.addLayout(button_row(self.cloud_backup_button, self.cloud_restore_button, self.sync_push_button, self.sync_pull_button))
        insert_at = max(0, page.content_layout.count() - 1)
        page.content_layout.insertWidget(insert_at, profiles)
        page.content_layout.insertWidget(insert_at + 1, operations)
        return page

    def _build_account_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("PageRoot")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("账户与设备")
        title.setObjectName("PageTitle")
        hint = QLabel("当前以本地资料离线使用；不需要登录账户。")
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        lay.addWidget(title)
        lay.addWidget(hint)

        identity = CardFrame()
        identity.add_title("本地资料")
        self.account_identity_summary = QLabel()
        self.account_identity_summary.setObjectName("MutedLabel")
        self.account_identity_summary.setWordWrap(True)
        identity.body.addWidget(self.account_identity_summary)
        open_data = ghost_button("打开数据目录")
        open_data.clicked.connect(self._open_data_root)
        identity.body.addLayout(button_row(open_data))
        lay.addWidget(identity)

        diagnostics = CardFrame()
        diagnostics.add_title("高级诊断信息")
        diagnostics.add_hint("资料和设备标识仅用于排查本机资料问题。")
        self.account_diagnostics = QLabel()
        self.account_diagnostics.setObjectName("MutedLabel")
        self.account_diagnostics.setWordWrap(True)
        diagnostics.body.addWidget(self.account_diagnostics)
        diagnostics.setVisible(False)
        self.account_diagnostics_card = diagnostics
        diagnostics_toggle = ghost_button("显示高级诊断信息")
        diagnostics_toggle.setCheckable(True)
        diagnostics_toggle.toggled.connect(
            lambda visible: (
                self.account_diagnostics_card.setVisible(visible),
                diagnostics_toggle.setText("隐藏高级诊断信息" if visible else "显示高级诊断信息"),
            )
        )
        lay.addWidget(diagnostics_toggle)
        lay.addWidget(diagnostics)

        cloud_profiles = CardFrame()
        cloud_profiles.add_title("云端资料")
        cloud_profiles.add_hint("连接自己的私有仓库以查看、恢复或接管资料；不会创建研错库在线账号。")
        self.account_remote_summary = QLabel("尚未检查云端资料")
        self.account_remote_summary.setObjectName("MutedLabel")
        self.account_remote_summary.setWordWrap(True)
        cloud_profiles.body.addWidget(self.account_remote_summary)
        inspect_profiles = primary_button("查看云端资料")
        inspect_profiles.clicked.connect(self._inspect_cloud_profiles)
        restore_profile = QPushButton("恢复指定资料…")
        restore_profile.clicked.connect(self._restore_cloud_profile)
        preview_merge = ghost_button("预检资料合并…")
        preview_merge.clicked.connect(self._preview_cloud_profile_merge)
        merge_profile = ghost_button("合并指定资料…")
        merge_profile.clicked.connect(self._merge_cloud_profile)
        cloud_profiles.body.addLayout(
            button_row(inspect_profiles, restore_profile, preview_merge, merge_profile)
        )
        lay.addWidget(cloud_profiles)
        cloud_profiles.setVisible(False)
        lay.addStretch(1)
        self._refresh_account_page()
        return page

    @staticmethod
    def _credential_available(credential_key: str, environment_key: str = "") -> bool:
        if environment_key and os.environ.get(environment_key, "").strip():
            return True
        try:
            return bool(get_secret(credential_key))
        except DomainError:
            return False

    def _refresh_account_page(self) -> None:
        identity = self.runtime.identity
        self.account_identity_summary.setText(
            "离线模式\n"
            f"显示名称：{identity.display_name}\n"
            "当前设备：Windows 设备\n"
            "数据状态：数据保存在本机"
        )
        self.account_diagnostics.setText(
            f"资料 ID：{identity.profile_id}\n"
            f"本地身份：{identity.user_id}\n"
            f"设备 ID：{identity.device_id}\n"
            f"数据目录：{self.runtime.paths.root}"
        )
        if not hasattr(self, "account_connection_summary"):
            return
        settings = self.runtime.settings
        ai_config = settings.ai.providers.get(settings.ai.default_provider)
        ai_ready = bool(settings.ai.enabled and ai_config) and self._credential_available(
            ai_config.credential_key if ai_config else "",
            ai_config.api_key_env if ai_config else "",
        )
        cloud_endpoint = getattr(settings.cloud, settings.cloud.default_provider, None)
        cloud_ready = bool(settings.cloud.enabled and cloud_endpoint) and self._credential_available(
            cloud_endpoint.credential_key if cloud_endpoint else ""
        )
        self.account_connection_summary.setText(
            f"AI 凭据：{'已配置' if ai_ready else '未配置或未启用'}\n"
            f"云服务：{'已连接配置' if cloud_ready else '未连接'}"
        )

    def _open_data_root(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.runtime.paths.root)))

    def _inspect_cloud_profiles(self) -> None:
        if self._cloud_profile_worker is not None:
            return
        self._set_settings_action_busy(self.inspect_cloud_profiles_button, "正在检查…")
        try:
            cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
        except DomainError as exc:
            self._set_settings_action_idle(self.inspect_cloud_profiles_button, "查看云端资料")
            self.account_remote_summary.setText(f"无法读取云端资料：{exc}")
            return
        self._cloud_profile_worker = CallableWorker(cloud.profile_connection_state, self)
        self._cloud_profile_worker.finished_ok.connect(self._on_cloud_profiles_loaded)
        self._cloud_profile_worker.failed.connect(self._on_cloud_profiles_failed)
        self._cloud_profile_worker.finished.connect(self._on_cloud_profile_worker_finished)
        self._cloud_profile_worker.start()

    def _on_cloud_profiles_loaded(self, state: object) -> None:
        if not isinstance(state, dict):
            self._on_cloud_profiles_failed("云端资料状态格式无效")
            return
        profiles = state.get("remote_profiles", [])
        lines = [f"本地资料：{state.get('local_profile_id', '未知')}"]
        lines.extend(
            f"云端资料：{item['profile_id']} · {item.get('tag', '无快照')}"
            for item in profiles
            if isinstance(item, dict) and item.get("profile_id")
        )
        if state.get("requires_takeover"):
            lines.append("发现其他资料：恢复或合并前需明确确认，不会自动覆盖本地数据。")
        if state.get("branch_detected"):
            lines.append("检测到另一设备已更新当前资料：已暂停上传，需恢复或合并确认。")
        if not profiles:
            lines.append("云端尚无资料快照。")
        self.account_remote_summary.setText(
            "\n".join(lines) + "\n最后检查："
            + QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm")
        )

    def _on_cloud_profiles_failed(self, error: str) -> None:
        self.account_remote_summary.setText(f"无法读取云端资料：{error}")

    def _on_cloud_profile_worker_finished(self) -> None:
        worker = self._cloud_profile_worker
        self._cloud_profile_worker = None
        self._set_settings_action_idle(self.inspect_cloud_profiles_button, "查看云端资料")
        if worker is not None:
            worker.deleteLater()

    def _restore_cloud_profile(self) -> None:
        try:
            self.cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            profiles = self.cloud.discover_profiles()
            if not profiles:
                self._show_status_toast("云端尚无可恢复的资料快照")
                return
            labels = [
                f"{item['profile_id']} · {item.get('tag', '无快照')}"
                for item in profiles
            ]
            choice, accepted = QInputDialog.getItem(
                self, "选择云端资料", "资料快照", labels, 0, False
            )
            if not accepted:
                return
            selected = profiles[labels.index(choice)]
            target = QFileDialog.getExistingDirectory(
                self, "选择恢复到的数据目录（建议空目录）"
            )
            if not target:
                return
            if (
                QMessageBox.question(
                    self,
                    "确认恢复资料",
                    "恢复不会替换当前数据目录。\n"
                    f"资料：{selected['profile_id']}\n目标：{target}\n\n继续？",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            result = self.cloud.restore_profile_to(selected["profile_id"], Path(target))
            self._show_operation_result(
                "云端资料已恢复",
                "所选云端资料已恢复到独立目录，当前资料没有被覆盖。",
                details=(
                    f"资料：{selected['profile_id']}\n"
                    f"恢复位置：{result['target_root']}\n"
                    "下一步：设置 YANCUO_DATA_ROOT 后重启。"
                ),
            )
        except DomainError as exc:
            self._show_operation_result(
                "云端资料恢复失败",
                "所选资料未能恢复。",
                details=str(exc),
                retry=self._restore_cloud_profile,
                is_error=True,
            )

    def _preview_cloud_profile_merge(self) -> None:
        try:
            self.cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            profiles = [
                item
                for item in self.cloud.discover_profiles()
                if item["profile_id"] != self.runtime.identity.profile_id
            ]
            if not profiles:
                self._show_status_toast("没有其他云端资料可比较")
                return
            labels = [f"{item['profile_id']} · {item.get('tag', '无快照')}" for item in profiles]
            choice, accepted = QInputDialog.getItem(
                self, "选择资料", "只比较，不写入当前资料", labels, 0, False
            )
            if not accepted:
                return
            preview = self.cloud.preview_profile_merge(profiles[labels.index(choice)]["profile_id"])
            lines = [f"资料：{preview['profile_id']}"]
            for table, item in preview["tables"].items():
                lines.append(
                    f"{table}: 远端新增 {item['new_remote']}，相同 {item['identical']}，冲突 {item['conflicts']}"
                )
            lines.append("未写入当前资料。" if not preview["has_conflicts"] else "存在冲突，不能自动合并。")
            self._show_operation_result(
                "资料合并预检",
                (
                    "预检完成，未写入当前资料。"
                    if not preview["has_conflicts"]
                    else "预检发现冲突，当前资料未被修改。"
                ),
                details="\n".join(lines),
            )
        except DomainError as exc:
            self._show_operation_result(
                "资料合并预检失败",
                "无法比较本地与云端资料。",
                details=str(exc),
                retry=self._preview_cloud_profile_merge,
                is_error=True,
            )

    def _merge_cloud_profile(self) -> None:
        """Run the deliberately explicit profile merge confirmation flow."""

        try:
            self.cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            profiles = [
                item
                for item in self.cloud.discover_profiles()
                if item["profile_id"] != self.runtime.identity.profile_id
            ]
            if not profiles:
                self._show_status_toast("没有其他云端资料可合并")
                return
            labels = [f"{item['profile_id']} · {item.get('tag', '无快照')}" for item in profiles]
            choice, accepted = QInputDialog.getItem(
                self, "选择资料", "选择要合并的云端资料", labels, 0, False
            )
            if not accepted:
                return
            remote_profile_id = profiles[labels.index(choice)]["profile_id"]
            preview = self.cloud.preview_profile_merge(remote_profile_id)
            conflicts = [
                (table, row_id, field)
                for table, item in preview["tables"].items()
                for row_id, fields in item.get("conflict_fields", {}).items()
                for field in fields
            ]
            choices: dict[str, str] = {}
            for table, row_id, field in conflicts:
                selection, selected = QInputDialog.getItem(
                    self,
                    "选择冲突字段",
                    f"{table} / {row_id} / {field}\n选择合并后保留的值：",
                    ["保留本地值", "采用云端值"],
                    0,
                    False,
                )
                if not selected:
                    return
                if selection == "采用云端值":
                    choices[f"{table}:{row_id}:{field}"] = "remote"

            primary_labels = [
                f"当前本地资料（{self.runtime.identity.profile_id}）",
                f"云端资料（{remote_profile_id}）",
            ]
            primary_choice, selected = QInputDialog.getItem(
                self, "选择主资料", "合并成功后，另一资料将写为主资料别名：", primary_labels, 0, False
            )
            if not selected:
                return
            primary_profile_id = (
                self.runtime.identity.profile_id
                if primary_choice == primary_labels[0]
                else remote_profile_id
            )
            if (
                QMessageBox.question(
                    self,
                    "确认合并资料",
                    "将先为当前本地资料上传不可变快照，再写入已确认的合并结果。\n"
                    "不会自动上传合并结果，也不会覆盖历史快照。继续？",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            result = self.cloud.merge_profile(
                remote_profile_id,
                primary_profile_id=primary_profile_id,
                field_choices=choices,
            )
            self._refresh_account_page()
            self._inspect_cloud_profiles()
            self._show_operation_result(
                "资料合并完成",
                (
                    f"已合并 {result['inserted_rows']} 条新增记录，"
                    f"采用 {result['remote_fields_applied']} 个云端字段。"
                ),
                details=(
                    f"主资料：{result['primary_profile_id']}\n"
                    "下一步：确认结果后手动执行下一次云备份。"
                ),
            )
        except DomainError as exc:
            self._show_operation_result(
                "资料合并失败",
                "本地资料没有完成合并。",
                details=str(exc),
                retry=self._merge_cloud_profile,
                is_error=True,
            )

    def _goto_due_in_library(self) -> None:
        self._show_library()
        if self._library_view != "process":
            self._set_library_view("process")
        for index in range(self.process_nav.count()):
            item = self.process_nav.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == "due":
                self.process_nav.setCurrentRow(index)
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
            self.library_list_hint.setVisible(False)
            self.library_list_header.setFixedHeight(48)
            self.library_nav_title.setText("知识浏览")
            self.library_view_hint.setText("按科目与知识结构浏览正式题目。")
            self.library_list_hint.setText("正式题目 · 双击打开详情")
            self.new_subject_button.setVisible(True)
            self.new_tag_button.setVisible(True)
            self.catalog_menu_button.setVisible(True)
            self.library_nav_stack.setCurrentIndex(0)
            self._refresh_knowledge_tree(current_mode)
            self._update_catalog_action_buttons()
        else:
            self.library_list_hint.setVisible(True)
            self.library_list_header.setFixedHeight(80)
            self.library_nav_title.setText("处理中心")
            self.library_view_hint.setText(
                "集中查看常用题目视图，以及待整理、已归档和回收站题目。"
            )
            self.library_list_hint.setText("处理中心题目 · 双击打开详情")
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
        with deferred_view_updates(self.knowledge_tree):
            self.knowledge_tree.clear()

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
                if uncategorized_count:
                    uncategorized = QTreeWidgetItem(
                        [f"未指定章节 · {uncategorized_count}"]
                    )
                    uncategorized.setToolTip(
                        0,
                        "这是尚未指定章节的题目筛选项，不是实际章节；"
                        "为这些题目选择章节后会自动消失。",
                    )
                    self._set_tree_item_data(
                        uncategorized,
                        mode=f"uncategorized:{subject.id}",
                        path=f"题库 / {subject.name} / 未指定章节",
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
            current = self.knowledge_tree.topLevelItem(0)
            self._nav_mode = (
                str(current.data(0, Qt.ItemDataRole.UserRole) or "active")
                if current is not None
                else "active"
            )
        if current is not None:
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
            (f"全部正式题目 · {self.services.count_problems('active')}", "active"),
            (
                f"今日待复习 · {len(self.services.list_problems(ProblemFilter(status='active', due_for_review=True)))}",
                "due",
            ),
            (
                f"我的收藏 · {len(self.services.list_problems(ProblemFilter(status='active', favorite_only=True)))}",
                "favorite",
            ),
            (
                f"最近入库 · {len(self.services.list_problems(ProblemFilter(status='active', created_within_days=30)))}",
                "recent",
            ),
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
            self._nav_mode = "active"
        self.process_nav.blockSignals(False)

    def _update_library_breadcrumb(self) -> None:
        if self._library_view == "browse":
            item = self.knowledge_tree.currentItem()
            path = item.data(0, _NAV_PATH_ROLE) if item else None
        else:
            item = self.process_nav.currentItem()
            path = item.data(_NAV_PATH_ROLE) if item else None
        self.library_breadcrumb.setText(str(path or "题库"))
        if hasattr(self, "library_page_title"):
            self.library_page_title.setText(self.library_breadcrumb.text())
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
                "处理中心搜索固定在当前视图范围，避免混入其他题目"
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
            statuses = (
                ("active",)
                if self._nav_mode in {"active", "due", "favorite", "recent"}
                else (self._nav_mode,)
            )
            scope = None
            if self._nav_mode in {"due", "favorite", "recent"}:
                allowed_problem_ids = frozenset(
                    problem.id
                    for problem in self.services.list_problems(
                        self._filter_from_nav(include_query=False)
                    )
                )
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
            statuses = (
                ("active",)
                if self._nav_mode in {"active", "due", "favorite", "recent"}
                else (self._nav_mode,)
            )
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
            and self._library_view in {"browse", "process"}
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
        is_empty = result_count == 0
        self.library_list_hint.setVisible(
            is_empty or bool(query) or self._library_view != "browse"
        )
        self.library_list_header.setFixedHeight(
            80 if is_empty or query or self._library_view != "browse" else 48
        )
        if is_empty:
            self.library_list_hint.setText(
                "当前范围暂无题目（0 条结果）；可切换目录、筛选条件或新建题目。"
            )
        elif query and result_count is not None:
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
            self.library_list_hint.setText("处理中心题目 · 双击打开详情")

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
        item = QListWidgetItem(self._problem_item_text(problem))
        item.setData(Qt.ItemDataRole.UserRole, problem.id)
        item.setToolTip(self._problem_item_tooltip(problem))
        item.setSizeHint(QSize(0, 58))
        return item

    def _problem_item_tooltip(self, problem: Problem) -> str:
        text = self._problem_item_text(problem)
        match = self._ai_search_matches.get(problem.id)
        if match is not None and self._is_ai_search_mode():
            text += f"\nAI {match.score:.0%} · {match.reason}"
        return text

    def _make_inline_question_widget(self, problem: Problem) -> _InlineQuestionItem:
        return _InlineQuestionItem(
            problem,
            expanded=problem.id == self._expanded_question_id,
            on_toggle=lambda problem_id=problem.id: self._toggle_question_by_id(problem_id),
            on_open=lambda problem_id=problem.id: self._open_problem_detail(problem_id),
        )

    def _set_inline_question_widget(
        self, item: QListWidgetItem, problem: Problem
    ) -> None:
        old_widget = self.problem_list.itemWidget(item)
        if old_widget is not None:
            self.problem_list.removeItemWidget(item)
            old_widget.hide()
            old_widget.setParent(None)
            old_widget.deleteLater()
        widget = self._make_inline_question_widget(problem)
        reader = widget.findChild(MathContentView)
        if reader is not None and hasattr(reader, "content_height_changed"):
            reader.content_height_changed.connect(
                lambda problem_id=problem.id, source=widget: self._sync_inline_question_size(
                    problem_id, source
                )
            )
        item.setSizeHint(widget.sizeHint())
        item.setText("")
        self.problem_list.setItemWidget(item, widget)

    def _release_inline_question_widget(self, row: int) -> None:
        item = self.problem_list.item(row)
        if item is None:
            return
        widget = self.problem_list.itemWidget(item)
        if widget is not None:
            self.problem_list.removeItemWidget(item)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        problem_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        problem = self._problem_rows.get(problem_id)
        if problem is not None:
            item.setText(self._problem_item_text(problem))
            item.setSizeHint(QSize(0, 58))

    def _queue_problem_widget_materialization(self) -> None:
        self._problem_widget_timer.start(0)

    def _reindex_problem_widget_rows(self) -> None:
        visible_ids = {
            str(self.problem_list.item(row).data(Qt.ItemDataRole.UserRole))
            for row in range(self.problem_list.count())
        }
        self._problem_rows = {
            problem_id: problem
            for problem_id, problem in self._problem_rows.items()
            if problem_id in visible_ids
        }
        self._materialized_problem_rows = {
            row
            for row in range(self.problem_list.count())
            if self.problem_list.itemWidget(self.problem_list.item(row)) is not None
        }

    def _materialize_visible_problem_widgets(self) -> None:
        count = self.problem_list.count()
        if count == 0:
            self._materialized_problem_rows.clear()
            return
        viewport = self.problem_list.viewport()
        first_index = self.problem_list.indexAt(QPoint(4, 0))
        last_index = self.problem_list.indexAt(
            QPoint(4, max(0, viewport.height() - 1))
        )
        first = first_index.row() if first_index.isValid() else 0
        last = (
            last_index.row()
            if last_index.isValid()
            else min(count - 1, first + 24)
        )
        desired = set(range(max(0, first - 4), min(count, last + 5)))
        if self._expanded_question_id:
            expanded_row, expanded_item = self._find_problem_item(
                self._expanded_question_id
            )
            if expanded_item is not None:
                desired.add(expanded_row)

        for row in self._materialized_problem_rows - desired:
            self._release_inline_question_widget(row)
        for row in desired - self._materialized_problem_rows:
            item = self.problem_list.item(row)
            if item is None:
                continue
            problem_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
            problem = self._problem_rows.get(problem_id)
            if problem is not None:
                self._set_inline_question_widget(item, problem)
        self._materialized_problem_rows = desired

    def _sync_inline_question_size(
        self, problem_id: str, widget: _InlineQuestionItem
    ) -> None:
        _row, item = self._find_problem_item(problem_id)
        if item is None:
            return
        if self.problem_list.itemWidget(item) is not widget:
            return
        item.setSizeHint(widget.sizeHint())
        self.problem_list.doItemsLayout()

    def _select_problem_id(self, problem_id: str) -> None:
        _row, item = self._find_problem_item(problem_id)
        if item is None:
            return
        self.problem_list.setCurrentItem(item)
        item.setSelected(True)
        self._selected_problem_id = problem_id

    def _open_problem_detail(self, problem_id: str) -> None:
        self._select_problem_id(problem_id)
        self._open_selected_detail()

    def _toggle_question_expansion(self, item: QListWidgetItem) -> None:
        problem_id = str(item.data(Qt.ItemDataRole.UserRole))
        self._toggle_question_by_id(problem_id)

    def _toggle_question_by_id(self, problem_id: str) -> None:
        self._expanded_question_id = (
            None if self._expanded_question_id == problem_id else problem_id
        )
        self._select_problem_id(problem_id)
        self.refresh_problems(preserve_view=True)
        _row, refreshed = self._find_problem_item(problem_id)
        if refreshed is not None:
            self.problem_list.scrollToItem(
                refreshed, QListWidget.ScrollHint.EnsureVisible
            )

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
        visible_ids = {problem.id for problem in problems}
        self._problem_rows = {problem.id: problem for problem in problems}
        if self._expanded_question_id not in visible_ids:
            self._expanded_question_id = None
        self.problem_list.blockSignals(True)
        with deferred_view_updates(self.problem_list):
            self.problem_list.clear()
            self._materialized_problem_rows.clear()
            for p in problems:
                item = self._make_problem_item(p)
                self.problem_list.addItem(item)
                if p.id in selected_ids:
                    item.setSelected(True)
                if p.id == current_id:
                    self.problem_list.setCurrentItem(item)
            self._materialize_visible_problem_widgets()
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
        self.library_count_label.setText(f"\u5171 {len(problems)} \u9898")
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
            self._reindex_problem_widget_rows()
            if was_current and self.problem_list.count():
                self.problem_list.setCurrentRow(min(row, self.problem_list.count() - 1))
        elif matching is not None and item is None:
            self._problem_rows[matching.id] = matching
            item = self._make_problem_item(matching)
            self.problem_list.insertItem(0, item)
            self._set_inline_question_widget(item, matching)
            self._materialized_problem_rows = {
                row + 1 for row in self._materialized_problem_rows
            }
            self._materialized_problem_rows.add(0)
        elif matching is not None and item is not None:
            self._problem_rows[matching.id] = matching
            item.setToolTip(self._problem_item_tooltip(matching))
            self._set_inline_question_widget(item, matching)
            self._materialized_problem_rows.add(row)
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
        if self._expanded_question_id in problem_ids:
            self._expanded_question_id = None
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
        self._reindex_problem_widget_rows()
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
        self.status.showMessage(summary)
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
            self._update_catalog_action_buttons()
            return
        self._apply_nav_mode(
            str(current.data(0, Qt.ItemDataRole.UserRole) or "active")
        )
        self._update_catalog_action_buttons()

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
            return
        pid = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_problem_id = pid

    def _selected_ids(self) -> list[str]:
        return [
            it.data(Qt.ItemDataRole.UserRole)
            for it in self.problem_list.selectedItems()
        ]

    def _require_one(self) -> str | None:
        ids = self._selected_ids()
        if not ids:
            self._show_status_toast("请先选择一道题")
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
            self._show_status_toast("题目不存在或已被删除")
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
            _PAGE_DASHBOARD: "返回工作台",
            _PAGE_LIBRARY: "返回题库",
            _PAGE_REVIEW: "返回复习",
            _PAGE_SETTINGS: "返回设置",
        }
        self.problem_detail_page.set_back_text(
            return_labels.get(self._detail_return_page, "返回")
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
        self._show_navigation_page(target)

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
            self.services.add_to_daily_review_plan(
                "problem", problem_id, self.services.review_plan_date()
            )
            self._open_problem_detail(problem_id)
            self._refresh_focus_pages()
            self._show_toast("已加入当日题目复习计划")
            self.statusBar().showMessage("已加入当日题目复习计划")
        except DomainError as exc:
            QMessageBox.warning(self, "无法加入复习", str(exc))

    def _add_note_to_daily_review(self, note_id: str) -> None:
        try:
            self.services.add_to_daily_review_plan(
                "note", note_id, self.services.review_plan_date()
            )
            self._show_toast("已加入当日笔记复习计划")
            self.statusBar().showMessage("已加入当日笔记复习计划")
        except DomainError as exc:
            QMessageBox.warning(self, "无法加入复习计划", str(exc))

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
            self._show_status_toast("请先选择题目")
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
        self._show_status_toast(f"已永久删除 {n} 道题")
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
            self._show_operation_result(
                "图片导入完成",
                f"新建 {len(result['created'])} 道题，跳过重复 {len(result['skipped'])} 个。",
                details=tip,
            )
            self.refresh_nav()
            for problem_id in result["created"]:
                self._refresh_problem_item(problem_id, update_summary=False)
            self._update_status()
            self._refresh_focus_pages()
        except DomainError as exc:
            self._show_operation_result(
                "图片导入失败",
                "所选图片未能完成导入。",
                details=str(exc),
                retry=self._import_images,
                is_error=True,
            )

    def _import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not folder:
            return
        try:
            result = self.services.import_folder(Path(folder))
            self._show_operation_result(
                "文件夹导入完成",
                f"新建 {len(result['created'])} 道题，跳过重复 {len(result['skipped'])} 个。",
            )
            self.refresh_nav()
            for problem_id in result["created"]:
                self._refresh_problem_item(problem_id, update_summary=False)
            self._update_status()
            self._refresh_focus_pages()
        except DomainError as exc:
            self._show_operation_result(
                "文件夹导入失败",
                "所选文件夹未能完成导入。",
                details=str(exc),
                retry=self._import_folder,
                is_error=True,
            )

    def _export_word(self) -> None:
        ids = self._selected_ids()
        if not ids:
            ids = [
                self.problem_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.problem_list.count())
            ]
        if not ids:
            self._show_status_toast("没有可导出的题目")
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
            self._show_operation_result(
                "Word 导出完成",
                "题目文档已生成。",
                details=f"保存位置：{dest}",
            )
        except DomainError as exc:
            self._show_operation_result(
                "Word 导出失败",
                "题目文档未能生成。",
                details=str(exc),
                retry=self._export_word,
                is_error=True,
            )

    def _backup(self) -> None:
        if self._local_backup_worker is not None:
            self._show_status_toast("已有本机备份任务正在运行")
            return
        self._local_backup_worker = CallableWorker(self.services.create_backup, self)
        self._local_backup_worker.finished_ok.connect(self._on_zip_backup_done)
        self._local_backup_worker.failed.connect(self._show_zip_backup_failed)
        self._local_backup_worker.finished.connect(self._on_local_backup_worker_finished)
        self._local_backup_worker.start()

    def _on_zip_backup_done(self, value: object) -> None:
        path = Path(str(value))
        self._set_local_backup_summary(path, "ZIP 本机备份")
        self._show_operation_result("备份完成", "本机备份已生成。", details=f"保存位置：{path}")

    def _show_zip_backup_failed(self, error: str) -> None:
        self._show_operation_result("备份失败", "本机备份未能生成。", details=error, retry=self._backup, is_error=True)

    def _export_ebpack(self) -> None:
        if self._local_backup_worker is not None:
            self._show_status_toast("已有本机备份任务正在运行")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 ebpack",
            str(self.runtime.paths.backup_dir / "yancuo.ebpack"),
            "Yancuo Pack (*.ebpack)",
        )
        if not path:
            return
        self._local_backup_worker = CallableWorker(
            lambda: self.ebpack.export_ebpack(Path(path)), self
        )
        self._local_backup_worker.finished_ok.connect(self._on_ebpack_backup_done)
        self._local_backup_worker.failed.connect(self._show_ebpack_backup_failed)
        self._local_backup_worker.finished.connect(self._on_local_backup_worker_finished)
        self._local_backup_worker.start()

    def _on_ebpack_backup_done(self, value: object) -> None:
        path = Path(str(value))
        self._set_local_backup_summary(path, "完整 .ebpack 备份")
        self._show_operation_result("完整备份包导出完成", "ebpack 已生成。", details=f"保存位置：{path}")

    def _show_ebpack_backup_failed(self, error: str) -> None:
        self._show_operation_result("完整备份包导出失败", "ebpack 未能生成。", details=error, retry=self._export_ebpack, is_error=True)

    def _on_local_backup_worker_finished(self) -> None:
        worker = self._local_backup_worker
        self._local_backup_worker = None
        if worker is not None:
            worker.deleteLater()

    def _set_local_backup_summary(self, path: Path, kind: str) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self.local_backup_summary.setText(f"最近备份：{kind}（文件大小暂不可读取）")
            return
        self.local_backup_summary.setText(
            f"最近备份：{kind} · {size / 1024 / 1024:.1f} MB · "
            f"{QDateTime.currentDateTime().toString('yyyy-MM-dd HH:mm')}\n"
            "包含本地数据库、附件与可恢复元数据。"
        )

    def _import_ebpack(self) -> None:
        if self._local_restore_worker is not None:
            self._show_status_toast("已有本机恢复任务正在运行")
            return
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
        self._local_restore_worker = CallableWorker(
            lambda: self.ebpack.restore_ebpack(Path(path), Path(target)), self
        )
        self._local_restore_worker.finished_ok.connect(self._on_ebpack_restore_done)
        self._local_restore_worker.failed.connect(self._show_ebpack_restore_failed)
        self._local_restore_worker.finished.connect(self._on_local_restore_worker_finished)
        self._local_restore_worker.start()

    def _on_ebpack_restore_done(self, value: object) -> None:
        result = value if isinstance(value, dict) else {}
        self._show_operation_result("恢复完成", "备份包已恢复到独立目录，当前资料没有被覆盖。", details=f"恢复位置：{result.get('target_root', '未知')}\n数据库版本：schema v{result.get('schema_version', '未知')}\n下一步：将 YANCUO_DATA_ROOT 指向该目录后重启。")

    def _show_ebpack_restore_failed(self, error: str) -> None:
        self._show_operation_result("恢复失败", "完整备份包未能恢复。", details=error, retry=self._import_ebpack, is_error=True)

    def _on_local_restore_worker_finished(self) -> None:
        worker = self._local_restore_worker
        self._local_restore_worker = None
        if worker is not None:
            worker.deleteLater()

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
            self._show_operation_result(
                "分享包已导出",
                f"已导出 {result.problem_count} 道题和 {result.asset_count} 张图片。",
                details=(
                    f"保存位置：{result.path}\n"
                    "隐私处理：已排除手写作答、私人备注与复习史。"
                ),
            )
        except DomainError as exc:
            self._show_operation_result(
                "分享包导出失败",
                "脱敏分享包未能生成。",
                details=str(exc),
                retry=self._export_gmshare,
                is_error=True,
            )

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
            self._show_operation_result(
                "导入完成",
                f"新建 {result.created} 道题，跳过重复 {result.skipped_duplicates} 道。",
                details=f"分享包标识：{result.package_id}",
            )
            self.refresh_problems()
        except DomainError as exc:
            self._show_operation_result(
                "分享包导入失败",
                "分享包未能导入。",
                details=str(exc),
                retry=self._import_gmshare,
                is_error=True,
            )

    def _cloud_backup(self) -> None:
        if self._cloud_operation_worker is not None:
            return
        self._set_settings_action_busy(self.cloud_backup_button, "正在备份…")
        try:
            cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
        except DomainError as exc:
            self._set_settings_action_idle(self.cloud_backup_button, "云备份")
            self._show_cloud_backup_failed(str(exc))
            return
        def backup() -> dict[str, object]:
            cloud.ensure_repository()
            return cloud.upload_backup()
        self._cloud_operation_worker = CallableWorker(backup, self)
        self._cloud_operation_worker.finished_ok.connect(self._on_cloud_backup_done)
        self._cloud_operation_worker.failed.connect(self._show_cloud_backup_failed)
        self._cloud_operation_worker.finished.connect(self._on_cloud_backup_worker_finished)
        self._cloud_operation_worker.start()

    def _on_cloud_backup_done(self, result: object) -> None:
        if not isinstance(result, dict):
            self._show_cloud_backup_failed("云备份结果格式无效")
            return
        self._show_operation_result(
            "云备份完成", "不可变完整快照已上传，并已更新当前资料指针。",
            details=(f"备份标签：{result.get('tag', '未知')}\n"
                     f"SHA-256：{str(result.get('sha256', ''))[:16]}…\n"
                     "说明：这是备份操作，不是实时同步。"),
        )

    def _show_cloud_backup_failed(self, error: str) -> None:
        self._show_operation_result("云备份失败", "当前资料未能完成云备份。", details=error, retry=self._cloud_backup, is_error=True)

    def _on_cloud_backup_worker_finished(self) -> None:
        worker = self._cloud_operation_worker
        self._cloud_operation_worker = None
        self._set_settings_action_idle(self.cloud_backup_button, "云备份")
        if worker is not None:
            worker.deleteLater()

    def _cloud_restore(self) -> None:
        if self._cloud_operation_worker is not None:
            return
        target = QFileDialog.getExistingDirectory(
            self, "选择恢复到的数据目录（建议空目录）"
        )
        if not target:
            return
        self._set_settings_action_busy(self.cloud_restore_button, "正在恢复…")
        try:
            cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
        except DomainError as exc:
            self._set_settings_action_idle(self.cloud_restore_button, "云恢复")
            self._show_cloud_restore_failed(str(exc))
            return
        self._cloud_operation_worker = CallableWorker(cloud.list_backups, self)
        self._cloud_operation_worker.finished_ok.connect(
            lambda backups: self._on_cloud_restore_listed(cloud, Path(target), backups)
        )
        self._cloud_operation_worker.failed.connect(self._show_cloud_restore_failed)
        self._cloud_operation_worker.finished.connect(self._on_cloud_restore_list_worker_finished)
        self._cloud_operation_worker.start()

    def _on_cloud_restore_listed(self, cloud: CloudBackupService, target: Path, value: object) -> None:
        backups = value if isinstance(value, list) else []
        if not backups:
            self._show_status_toast("云端没有可恢复的备份")
            return
        latest = next((item for item in backups if item.get("is_latest")), None)
        summary = "云端备份列表：\n" + "\n".join(
            f"- {item.get('profile_id') or '旧格式'} · {item['tag']}"
            f"{' (资料最新)' if item.get('is_latest') else ''}"
            for item in backups[:20]
        )
        if QMessageBox.question(self, "确认恢复", summary + "\n\n将恢复当前本地资料对应的最新快照到所选目录。继续？") != QMessageBox.StandardButton.Yes:
            return
        tag = latest["tag"] if latest else "未知"
        self._pending_cloud_restore = (cloud, target, tag)

    def _on_cloud_restore_list_worker_finished(self) -> None:
        worker = self._cloud_operation_worker
        self._cloud_operation_worker = None
        if worker is not None:
            worker.deleteLater()
        pending = self._pending_cloud_restore
        self._pending_cloud_restore = None
        if pending is None:
            self._set_settings_action_idle(self.cloud_restore_button, "云恢复")
            return
        cloud, target, tag = pending
        self._cloud_operation_worker = CallableWorker(lambda: cloud.restore_latest_to(target), self)
        self._cloud_operation_worker.finished_ok.connect(lambda result: self._on_cloud_restore_done(tag, result))
        self._cloud_operation_worker.failed.connect(self._show_cloud_restore_failed)
        self._cloud_operation_worker.finished.connect(self._on_cloud_restore_worker_finished)
        self._cloud_operation_worker.start()

    def _on_cloud_restore_done(self, tag: str, value: object) -> None:
        result = value if isinstance(value, dict) else {}
        self._show_operation_result("云恢复完成", "云端快照已恢复到独立目录，当前资料没有被覆盖。", details=f"恢复位置：{result.get('target_root', '未知')}\n快照：{tag}\n下一步：设置 YANCUO_DATA_ROOT 后重启。")

    def _show_cloud_restore_failed(self, error: str) -> None:
        self._show_operation_result("云恢复失败", "云端快照未能恢复。", details=error, retry=self._cloud_restore, is_error=True)

    def _on_cloud_restore_worker_finished(self) -> None:
        worker = self._cloud_operation_worker
        self._cloud_operation_worker = None
        self._set_settings_action_idle(self.cloud_restore_button, "云恢复")
        if worker is not None:
            worker.deleteLater()

    def _sync_push(self) -> None:
        if self._cloud_operation_worker is not None:
            return
        self._set_settings_action_busy(self.sync_push_button, "正在推送…")
        try:
            sync = SyncService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
        except DomainError as exc:
            self._set_settings_action_idle(self.sync_push_button, "推送增量")
            self._show_sync_push_failed(str(exc))
            return
        self._cloud_operation_worker = CallableWorker(sync.push_operations, self)
        self._cloud_operation_worker.finished_ok.connect(self._on_sync_push_done)
        self._cloud_operation_worker.failed.connect(self._show_sync_push_failed)
        self._cloud_operation_worker.finished.connect(self._on_sync_push_worker_finished)
        self._cloud_operation_worker.start()

    def _on_sync_push_done(self, result: object) -> None:
        pushed = result.get("pushed", 0) if isinstance(result, dict) else 0
        self._show_operation_result("推送增量", f"已推送 {pushed} 条增量记录。", details="说明：这是手动增量同步，不是实时同步；当前通道需要 local_folder。")

    def _show_sync_push_failed(self, error: str) -> None:
        self._show_operation_result("推送失败", "增量记录未能推送。", details=error, retry=self._sync_push, is_error=True)

    def _on_sync_push_worker_finished(self) -> None:
        worker = self._cloud_operation_worker
        self._cloud_operation_worker = None
        self._set_settings_action_idle(self.sync_push_button, "推送增量")
        if worker is not None:
            worker.deleteLater()

    def _sync_pull(self) -> None:
        if self._cloud_operation_worker is not None:
            return
        self._set_settings_action_busy(self.sync_pull_button, "正在拉取…")
        try:
            sync = SyncService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
        except DomainError as exc:
            self._set_settings_action_idle(self.sync_pull_button, "拉取合并")
            self._show_sync_pull_failed(str(exc))
            return
        self._cloud_operation_worker = CallableWorker(sync.pull_and_merge, self)
        self._cloud_operation_worker.finished_ok.connect(self._on_sync_pull_done)
        self._cloud_operation_worker.failed.connect(self._show_sync_pull_failed)
        self._cloud_operation_worker.finished.connect(self._on_sync_pull_worker_finished)
        self._cloud_operation_worker.start()

    def _on_sync_pull_done(self, value: object) -> None:
        result = value if isinstance(value, dict) else {}
        msg = (f"应用 {result.get('applied', 0)} 条\n"
               f"自动合并字段约 {result.get('auto_merged_fields', 0)}\n"
               f"冲突字段 {result.get('conflicts', 0)}\n")
        if result.get("snapshot"):
            msg += f"合并前快照：{result['snapshot']}\n"
        if result.get("review_session_id"):
            msg += "请在工作台打开「待确认变更」处理同步冲突。"
        self._show_operation_result("拉取合并完成", f"已应用 {result.get('applied', 0)} 条记录，发现 {result.get('conflicts', 0)} 个冲突字段。", details=msg)
        if result.get("review_session_id"):
            ReviewDialog(self.ai, self.services, self).exec()
        self.refresh_all()

    def _show_sync_pull_failed(self, error: str) -> None:
        self._show_operation_result("拉取合并失败", "云端增量未能拉取或合并。", details=error, retry=self._sync_pull, is_error=True)

    def _on_sync_pull_worker_finished(self) -> None:
        worker = self._cloud_operation_worker
        self._cloud_operation_worker = None
        self._set_settings_action_idle(self.sync_pull_button, "拉取合并")
        if worker is not None:
            worker.deleteLater()

    def _restore_backup(self) -> None:
        if self._local_restore_worker is not None:
            self._show_status_toast("已有本机恢复任务正在运行")
            return
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
        self._local_restore_worker = CallableWorker(
            lambda: self.services.restore_backup(Path(path), Path(target)), self
        )
        self._local_restore_worker.finished_ok.connect(self._on_zip_restore_done)
        self._local_restore_worker.failed.connect(self._show_zip_restore_failed)
        self._local_restore_worker.finished.connect(self._on_local_restore_worker_finished)
        self._local_restore_worker.start()

    def _on_zip_restore_done(self, root: object) -> None:
        self._show_operation_result("恢复完成", "旧版 zip 已恢复到独立目录，当前资料没有被覆盖。", details=f"恢复位置：{root}\n下一步：将 YANCUO_DATA_ROOT 指向该目录后重启。")

    def _show_zip_restore_failed(self, error: str) -> None:
        self._show_operation_result("恢复失败", "旧版 zip 未能恢复。", details=error, retry=self._restore_backup, is_error=True)
    def _open_settings(self) -> None:
        self._show_navigation_page(_PAGE_SETTINGS)
        self.settings_nav.setCurrentRow(1)
        self._refresh_focus_pages()

    def _rebuild_search_index(self) -> None:
        if self._search_index_worker is not None:
            return
        self._set_settings_action_busy(self.rebuild_search_button, "正在重建…")
        self.search_index_summary.setText("正在检查并重建本地索引…")
        self.status.showMessage("正在重建本地搜索索引")
        self._search_index_worker = CallableWorker(self.search.rebuild, self)
        self._search_index_worker.finished_ok.connect(self._on_search_rebuild_done)
        self._search_index_worker.failed.connect(self._on_search_index_failed)
        self._search_index_worker.finished.connect(self._on_search_index_worker_finished)
        self._search_index_worker.start()

    def _check_search_index(self) -> None:
        if self._search_index_worker is not None:
            return
        self._set_settings_action_busy(self.check_search_button, "正在检查…")
        self._search_index_worker = CallableWorker(self.search.check_consistency, self)
        self._search_index_worker.finished_ok.connect(self._on_search_check_done)
        self._search_index_worker.failed.connect(self._on_search_index_failed)
        self._search_index_worker.finished.connect(self._on_search_index_worker_finished)
        self._search_index_worker.start()

    def _on_search_rebuild_done(self, count: object) -> None:
        health = self._refresh_search_index_summary()
        if self.search_edit.text().strip():
            self.refresh_problems()
        self._show_status_toast(f"本地搜索索引已重建：{count} 道题 · {health.summary}")

    def _on_search_check_done(self, health: object) -> None:
        if isinstance(health, SearchIndexHealth):
            self.search_index_summary.setText(
                f"索引状态：{health.summary}\n最后检查："
                + QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm")
            )
        self._show_status_toast("本地搜索索引检查完成")

    def _on_search_index_failed(self, error: str) -> None:
        self.search_index_summary.setText(f"索引操作失败：{error}")
        self.status.showMessage("本地搜索索引操作失败", 5000)

    def _on_search_index_worker_finished(self) -> None:
        worker = self._search_index_worker
        self._search_index_worker = None
        self._set_settings_action_idle(self.rebuild_search_button, "检查并重建索引")
        self._set_settings_action_idle(self.check_search_button, "检查索引")
        if worker is not None:
            worker.deleteLater()

    def _refresh_search_index_summary(self) -> SearchIndexHealth:
        health = self.search.check_consistency()
        self.search_index_summary.setText(
            f"索引状态：{health.summary}\n"
            "最后检查：" + QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm")
        )
        return health

    @staticmethod
    def _set_settings_action_busy(button: QPushButton, text: str) -> None:
        button.setEnabled(False)
        button.setText(text)
        QApplication.processEvents()

    @staticmethod
    def _set_settings_action_idle(button: QPushButton, text: str) -> None:
        button.setEnabled(True)
        button.setText(text)

    def _ai_recognize(self) -> None:
        ids = self._selected_ids()
        if not ids:
            self._show_status_toast("请先选择带原图的题目")
            return
        if self._ai_worker and self._ai_worker.isRunning():
            self._show_status_toast("已有 AI 任务在后台运行")
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
            self._show_status_toast("已恢复到接受 AI 结果之前的内容")
            self._refresh_problem_item(pid, select=True)
        except DomainError as exc:
            QMessageBox.warning(self, "无法撤销", str(exc))

    def _export_workspace(self) -> None:
        ids = self._selected_ids()
        if not ids:
            self._show_status_toast("请先选择要导出的题目")
            return
        try:
            dest = self.workspace.export_workspace(ids)
            self._show_operation_result(
                "导出完成",
                "外部编辑工作区已生成。",
                details=(
                    f"工作区位置：{dest}\n"
                    "请只编辑工作区内的 Markdown/JSON，不要直接修改数据库。"
                ),
            )
        except DomainError as exc:
            self._show_operation_result(
                "工作区导出失败",
                "外部编辑工作区未能生成。",
                details=str(exc),
                retry=self._export_workspace,
                is_error=True,
            )

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
            self._show_operation_result(
                "工作区导入完成",
                (
                    f"已生成 {len(result['items'])} 个待审核变更，"
                    f"其中 {len(result['conflicts'])} 个冲突。"
                ),
                details=msg,
            )
            self._open_review()
        except DomainError as exc:
            self._show_operation_result(
                "工作区导入失败",
                "外部编辑结果未能导入。",
                details=str(exc),
                retry=self._import_workspace,
                is_error=True,
            )

    def _today_review(self) -> None:
        self._show_navigation_page(_PAGE_REVIEW)
        self.review_page.show_home()

    def _schedule_review(self) -> None:
        ids = self._selected_ids()
        if not ids:
            self._show_status_toast("请先选择题目")
            return
        try:
            for pid in ids:
                self.services.add_to_daily_review_plan(
                    "problem", pid, self.services.review_plan_date()
                )
            self._show_status_toast(f"已将 {len(ids)} 题加入当日题目复习计划")
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
            self._show_status_toast("请先选择题目")
            return
        value, ok = QInputDialog.getInt(self, "批量优先级", "优先级 1–5：", 3, 1, 5)
        if not ok:
            return
        try:
            n = self.services.batch_update_problems(ids, priority=value)
            self._show_status_toast(f"已更新 {n} 题的优先级")
            for pid in ids:
                self._refresh_problem_item(pid, update_summary=False)
            self._update_status()
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _show_catalog_context_menu(self, position) -> None:  # noqa: ANN001
        item = self.knowledge_tree.itemAt(position)
        if item is not None:
            self.knowledge_tree.setCurrentItem(item)
        menu = self._build_catalog_action_menu(self.get_manage_actions())
        menu.exec(self.knowledge_tree.viewport().mapToGlobal(position))

    def _show_catalog_menu(self) -> None:
        menu = self._build_catalog_action_menu(self.get_manage_actions())
        menu.exec(
            self.catalog_menu_button.mapToGlobal(
                self.catalog_menu_button.rect().bottomLeft()
            )
        )

    def _catalog_node_context(self) -> CatalogNodeContext:
        """Normalize selection once so both menus use the same node type."""
        if self._library_view != "browse":
            return CatalogNodeContext("system")
        item = self.knowledge_tree.currentItem()
        if item is None:
            return CatalogNodeContext("root")
        mode = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if mode.startswith("subject:"):
            return CatalogNodeContext("subject", subject_id=mode.split(":", 1)[1])
        if mode.startswith("chapter:"):
            _, subject_id, chapter_id = mode.split(":", 2)
            return CatalogNodeContext("chapter", subject_id, chapter_id)
        if mode.startswith("uncategorized:"):
            return CatalogNodeContext(
                "uncategorized",
                subject_id=mode.split(":", 1)[1],
            )
        return CatalogNodeContext("system")

    def get_create_actions(self) -> tuple[CatalogAction, ...]:
        """Return creation actions only; each action ID has one menu entry."""
        context = self._catalog_node_context()
        actions: list[CatalogAction] = []
        if context.node_type == "root":
            actions.append(CatalogAction("create_subject", "新建科目", self._new_subject))
        elif context.node_type == "subject" and context.subject_id:
            actions.append(CatalogAction(
                "create_chapter", "新建一级章节",
                lambda subject_id=context.subject_id: self._new_chapter(subject_id, None),
            ))
        elif context.node_type == "chapter" and context.subject_id and context.chapter_id:
            actions.append(CatalogAction(
                "create_chapter", "新建子章节",
                lambda subject_id=context.subject_id, chapter_id=context.chapter_id: self._new_chapter(subject_id, chapter_id),
            ))
        actions.append(CatalogAction(
            "create_tag", "新建标签", self._new_tag,
            separator_before=bool(actions),
        ))
        return tuple(actions)

    def get_manage_actions(self) -> tuple[CatalogAction, ...]:
        """Return management actions only for a selected mutable directory node."""
        context = self._catalog_node_context()
        if context.node_type == "subject" and context.subject_id:
            subject_id = context.subject_id
            position, count = self._subject_position(subject_id)
            deletable, delete_hint = self._subject_delete_state(subject_id)
            return (
                CatalogAction("rename_node", "重命名科目", lambda: self._rename_subject(subject_id)),
                CatalogAction("move_node_up", "科目上移", lambda: self._reorder_subject(subject_id, -1), position > 0),
                CatalogAction("move_node_down", "科目下移", lambda: self._reorder_subject(subject_id, 1), position >= 0 and position < count - 1),
                CatalogAction("delete_node", "删除科目", lambda: self._delete_subject(subject_id), deletable, True, True, delete_hint),
            )
        if context.node_type == "chapter" and context.chapter_id:
            chapter_id = context.chapter_id
            position, count = self._chapter_position(chapter_id)
            deletable, delete_hint = self._chapter_delete_state(chapter_id)
            return (
                CatalogAction("rename_node", "重命名章节", lambda: self._rename_chapter(chapter_id)),
                CatalogAction("move_node_up", "章节上移", lambda: self._reorder_chapter(chapter_id, -1), position > 0),
                CatalogAction("move_node_down", "章节下移", lambda: self._reorder_chapter(chapter_id, 1), position >= 0 and position < count - 1),
                CatalogAction("delete_node", "删除章节", lambda: self._delete_chapter(context.subject_id or "", chapter_id), deletable, True, True, delete_hint),
            )
        return ()

    def _build_catalog_action_menu(self, actions: tuple[CatalogAction, ...]) -> QMenu:
        menu = QMenu(self)
        for spec in actions:
            if spec.separator_before:
                menu.addSeparator()
            action = menu.addAction(spec.label, spec.callback)
            action.setData(spec.action_id)
            action.setEnabled(spec.enabled)
            action.setProperty("danger", spec.danger)
            if spec.disabled_hint:
                action.setToolTip(spec.disabled_hint)
        return menu

    def _update_catalog_action_buttons(self) -> None:
        actions = self.get_manage_actions()
        self.catalog_menu_button.setEnabled(bool(actions))
        context = self._catalog_node_context()
        if context.node_type == "uncategorized":
            tooltip = (
                "这是未指定章节题目的筛选项，不是实际章节；"
                "为题目选择章节后会自动消失"
            )
        else:
            tooltip = "管理当前目录" if actions else "请先选择一个科目或章节"
        self.catalog_menu_button.setToolTip(
            tooltip
        )

    def _subject_position(self, subject_id: str) -> tuple[int, int]:
        subjects = self.services.list_subjects()
        return next((i for i, item in enumerate(subjects) if item.id == subject_id), -1), len(subjects)

    def _find_chapter(self, chapter_id: str):
        return next((
            item for subject in self.services.list_subjects()
            for item in self.services.list_chapters(subject.id)
            if item.id == chapter_id
        ), None)

    def _chapter_position(self, chapter_id: str) -> tuple[int, int]:
        chapter = self._find_chapter(chapter_id)
        if chapter is None:
            return -1, 0
        siblings = [item for item in self.services.list_chapters(chapter.subject_id) if item.parent_id == chapter.parent_id]
        return next((i for i, item in enumerate(siblings) if item.id == chapter_id), -1), len(siblings)

    def _subject_delete_state(self, subject_id: str) -> tuple[bool, str]:
        chapter_count = len(self.services.list_chapters(subject_id))
        problem_count = len(self.services.list_problems(ProblemFilter(subject_id=subject_id)))
        if chapter_count or problem_count:
            return False, f"包含 {chapter_count} 个章节和 {problem_count} 道题目，当前删除规则仅允许删除空科目"
        return True, ""

    def _chapter_delete_state(self, chapter_id: str) -> tuple[bool, str]:
        chapter = self._find_chapter(chapter_id)
        if chapter is None:
            return False, "章节不存在"
        chapters = self.services.list_chapters(chapter.subject_id)
        child_count = sum(item.parent_id == chapter_id for item in chapters)
        problem_count = len(self.services.list_problems(ProblemFilter(chapter_id=chapter_id)))
        if child_count or problem_count:
            return False, f"包含 {child_count} 个子章节和 {problem_count} 道题目，当前删除规则仅允许删除空章节"
        return True, ""

    def _build_catalog_menu(self) -> QMenu:
        return self._build_catalog_action_menu(self.get_manage_actions())

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
        subject = next((item for item in self.services.list_subjects() if item.id == subject_id), None)
        if subject is None:
            return
        deletable, _hint = self._subject_delete_state(subject_id)
        if not deletable:
            QMessageBox.warning(self, "无法删除科目", "当前删除规则仅允许删除不含章节或题目的科目。")
            return
        chapter_count = len(self.services.list_chapters(subject_id))
        problem_count = len(self.services.list_problems(ProblemFilter(subject_id=subject_id)))
        if not ConfirmDialog.ask(
            self,
            f'删除科目“{subject.name}”？',
            f"该科目包含：\n- {chapter_count} 个章节\n- {problem_count} 道题目\n\n"
            "删除后不会影响任何题目。",
            "确认删除",
        ):
            return
        try:
            self.services.delete_subject(subject_id)
            self._refresh_catalog_to("active")
        except DomainError as exc:
            QMessageBox.warning(self, "无法删除科目", str(exc))

    def _delete_chapter(self, subject_id: str, chapter_id: str) -> None:
        chapter = self._find_chapter(chapter_id)
        if chapter is None:
            return
        deletable, _hint = self._chapter_delete_state(chapter_id)
        if not deletable:
            QMessageBox.warning(self, "无法删除章节", "当前删除规则仅允许删除不含子章节或题目的章节。")
            return
        chapters = self.services.list_chapters(chapter.subject_id)
        child_count = sum(item.parent_id == chapter_id for item in chapters)
        problem_count = len(self.services.list_problems(ProblemFilter(chapter_id=chapter_id)))
        if not ConfirmDialog.ask(
            self,
            f'删除章节“{chapter.name}”？',
            f"该章节包含：\n- {child_count} 个子章节\n- {problem_count} 道题目\n\n"
            "删除后不会影响任何题目。",
            "确认删除",
        ):
            return
        try:
            self.services.delete_chapter(chapter_id)
            self._refresh_catalog_to(f"subject:{subject_id}")
        except DomainError as exc:
            QMessageBox.warning(self, "无法删除章节", str(exc))

    def _move_selected_category(self) -> None:
        ids = self._selected_ids()
        if not ids:
            self._show_status_toast("请先选择题目")
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
            self._show_status_toast(f"已移动 {count} 道题")
        except DomainError as exc:
            QMessageBox.warning(self, "无法移动分类", str(exc))

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "新建标签", "标签名称：")
        if not ok or not name.strip():
            return
        try:
            self.services.create_tag(name.strip())
            self._show_status_toast(f"已创建标签：{name.strip()}")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))
