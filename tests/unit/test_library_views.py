"""Library browse and processing views keep separate navigation state."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QWidget,
)

import yancuo_win.ui.intake_page as intake_page_module
import yancuo_win.ui.note_page as note_page_module
import yancuo_win.ui.problem_detail as problem_detail_module
import yancuo_win.ui.review_page as review_page_module
import yancuo_win.ui.review_plan_builder as review_plan_builder_module
import yancuo_win.ui.settings_dialog as settings_dialog_module
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.main_window import MainWindow
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.task_center import TaskCenterDialog
from yancuo_win.ui.widgets import ChevronComboBox, SoftItemDelegate, ThemedTreeBranchStyle


class _ReaderStub(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scroll_position = 0

    def set_accessible_content(self, name, description="") -> None:
        self.setAccessibleName(name)
        self.setAccessibleDescription(description)

    def set_fit_content_height(self, *_args, **_kwargs) -> None:
        self.fit_content_height_args = _args
        self.fit_content_height_kwargs = _kwargs

    def set_adaptive_content_height(self, maximum_height, **_kwargs) -> None:
        self.adaptive_height_limit = maximum_height

    def fit_to_width(self) -> None:
        pass

    def set_zoom_scale(self, *_args, **_kwargs) -> None:
        pass

    def set_fragment(self, *_args, **_kwargs) -> None:
        pass

    def set_problem(self, *_args, **_kwargs) -> None:
        pass

    def set_message(self, *_args, **_kwargs) -> None:
        pass

    def set_note(self, *_args, **_kwargs) -> None:
        pass

    def scroll_position(self) -> int:
        return self._scroll_position

    def restore_scroll_position(self, value: int) -> None:
        self._scroll_position = value

    def scroll_to_bottom(self) -> None:
        self._scroll_position = 999


@pytest.fixture()
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setenv("YANCUO_AI__DEFAULT_PROVIDER", "mock")
    monkeypatch.setattr(intake_page_module, "MathContentView", _ReaderStub)
    monkeypatch.setattr(note_page_module, "MathContentView", _ReaderStub)
    monkeypatch.setattr(problem_detail_module, "MathContentView", _ReaderStub)
    monkeypatch.setattr(review_page_module, "MathContentView", _ReaderStub)
    app = QApplication.instance() or QApplication([])

    runtime = bootstrap_runtime()
    services = AppServices(runtime)
    subject = services.create_subject("高等数学")
    integral = services.create_chapter(subject.id, "积分")
    double = services.create_chapter(subject.id, "二重积分", parent_id=integral.id)
    services.create_problem(
        title="未分类极限题",
        status="active",
        subject_id=subject.id,
    )
    services.create_problem(
        title="积分基础题",
        status="active",
        subject_id=subject.id,
        chapter_id=integral.id,
    )
    favorite = services.create_problem(
        title="二重积分题",
        status="active",
        subject_id=subject.id,
        chapter_id=double.id,
    )
    services.update_problem(
        favorite.id,
        {
            "is_favorite": True,
            "solution_markdown": "使用格林公式完成区域转换",
        },
    )
    services.create_problem(title="待整理题", status="inbox")
    services.create_problem(title="归档题", status="archived")
    services.create_problem(title="回收站题", status="trashed")

    main = MainWindow(runtime)
    app.processEvents()
    yield main
    main.close()


def _nav_modes(window: MainWindow) -> list[str]:
    if window._library_view == "browse":
        return [
            str(item.data(0, Qt.ItemDataRole.UserRole))
            for item in window._iter_knowledge_items()
        ]
    return [
        str(window.process_nav.item(index).data(Qt.ItemDataRole.UserRole))
        for index in range(window.process_nav.count())
    ]


def test_settings_page_consolidates_account_services_and_data(
    window: MainWindow,
) -> None:
    labels = [
        window.main_nav.item(row).text() for row in range(window.main_nav.count())
    ]
    assert labels == ["工作台", "题库", "笔记", "复习", "设置"]

    items = window.main_nav.findItems("设置", Qt.MatchFlag.MatchExactly)
    assert len(items) == 1

    window.main_nav.setCurrentItem(items[0])
    assert [
        window.settings_nav.item(index).text()
        for index in range(window.settings_nav.count())
    ] == ["账户与设备", "AI 服务", "外观与显示", "本地数据", "云端同步"]

    window.settings_nav.setCurrentRow(0)

    assert "离线模式" in window.account_identity_summary.text()
    assert window.runtime.identity.user_id in window.account_diagnostics.text()

    window._open_settings()
    assert window.settings_pages.currentIndex() == 1


def test_data_transfer_actions_are_grouped_in_accessible_dropdowns(
    window: MainWindow,
) -> None:
    settings = window.main_nav.findItems("设置", Qt.MatchFlag.MatchExactly)
    window.main_nav.setCurrentItem(settings[0])
    window.settings_nav.setCurrentRow(3)

    backup_actions = [
        window.backup_action_combo.itemText(index)
        for index in range(window.backup_action_combo.count())
    ]
    transfer_actions = [
        window.transfer_action_combo.itemText(index)
        for index in range(window.transfer_action_combo.count())
    ]

    assert backup_actions == [
        "导出完整备份",
        "导入完整备份",
        "",
        "创建 ZIP 备份（旧版兼容）",
        "从 ZIP 恢复（旧版兼容）",
    ]
    assert transfer_actions == [
        "导出分享包",
        "导出工作区",
        "",
        "导入分享包",
        "导入工作区",
    ]
    assert window.backup_action_combo.accessibleName() == "备份与恢复操作"
    assert window.transfer_action_combo.accessibleName() == "导入与导出操作"


def test_primary_navigation_and_large_views_are_keyboard_accessible(
    window: MainWindow,
) -> None:
    assert window.main_nav.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert window.main_nav.accessibleName() == "主导航"
    assert window.knowledge_tree.accessibleName() == "知识目录"
    assert window.knowledge_tree.uniformRowHeights()
    assert window.process_nav.uniformItemSizes()
    assert window.problem_list.accessibleName() == "题目列表"

    window.main_nav.setCurrentRow(0)
    window.main_nav.setFocus()
    QTest.keyClick(window.main_nav, Qt.Key.Key_Down)
    assert window.main_nav.currentRow() == 1

    window.search_edit.setText("不存在的题目")
    window.refresh_problems()
    assert window.problem_list.count() == 0
    assert not window.library_list_hint.isHidden()
    assert "暂无题目" in window.library_list_hint.text()


def test_navigation_shortcuts_and_search_focus_are_discoverable(
    window: MainWindow,
) -> None:
    app = QApplication.instance()
    assert app is not None
    window.show()
    app.processEvents()
    shortcuts = {
        shortcut.key().toString(): shortcut for shortcut in window.navigation_shortcuts
    }
    assert set(shortcuts) == {"Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+,"}
    assert window.main_nav.item(1).toolTip() == "题库（Ctrl+2）"

    QTest.keyClick(window, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.stack.currentIndex() == 4
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.note_page.note_search_edit.hasFocus()

    QTest.keyClick(window, Qt.Key.Key_2, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.search_edit.hasFocus()


def test_settings_success_uses_non_blocking_status_signal(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    page = window.ai_settings_page
    page.status_message.connect(messages.append)
    monkeypatch.setattr(settings_dialog_module, "set_secret", lambda *_args: None)
    monkeypatch.setattr(settings_dialog_module, "get_secret", lambda *_args: "secret")

    def fail_information(*_args, **_kwargs) -> None:
        raise AssertionError("低风险成功反馈不应打开阻塞式信息框")

    monkeypatch.setattr(
        settings_dialog_module.QMessageBox,
        "information",
        fail_information,
    )
    page.ai_token_edit.setText("test-token")
    page._save_ai_token()

    assert messages == ["AI 密钥已保存到系统凭据"]
    assert page.ai_token_edit.text() == ""


def test_narrow_window_hides_sidebar_and_switches_plan_draft_view(
    window: MainWindow,
) -> None:
    app = QApplication.instance()
    assert app is not None

    window.resize(860, 700)
    window._apply_sidebar_visibility()
    app.processEvents()
    assert window.minimumWidth() == 760
    assert (
        window.intake_page.ai_upload_content_layout.direction()
        == QBoxLayout.Direction.TopToBottom
    )
    assert (
        window.intake_page.answer_image_row_layout.direction()
        == QBoxLayout.Direction.TopToBottom
    )
    assert window.intake_page.answer_image_list.height() == 156
    assert window.intake_page.answer_image_list.iconSize().width() == 144
    assert window.sidebar.isHidden()
    assert not window.library_navigation_panel.isHidden()

    window._toggle_sidebar()
    app.processEvents()
    assert not window.sidebar.isHidden()
    assert window.sidebar_rail.isHidden()

    notes_item = window.main_nav.item(2)
    window._on_main_nav_clicked(notes_item)
    app.processEvents()
    assert window.sidebar.isHidden()
    assert not window.sidebar_rail.isHidden()

    builder = window.review_page.plan_builder_page
    assert builder.workspace.handleWidth() == 10
    assert builder.workspace.contentsMargins().left() == 8
    assert builder.browse_workspace.handleWidth() == 10
    assert isinstance(builder.folder_tree.itemDelegate(), SoftItemDelegate)
    assert isinstance(builder.source_list.itemDelegate(), SoftItemDelegate)
    assert isinstance(builder.queue_list.itemDelegate(), SoftItemDelegate)
    assert builder.folder_tree.uniformRowHeights()
    assert isinstance(window.knowledge_tree.style(), ThemedTreeBranchStyle)
    assert isinstance(builder.folder_tree.style(), ThemedTreeBranchStyle)
    assert builder.source_list.uniformItemSizes()
    assert builder.queue_list.uniformItemSizes()
    assert isinstance(builder.queue_pane, QFrame)
    builder._set_narrow_layout(True)
    app.processEvents()
    assert not builder.draft_toggle.isHidden()
    assert builder.queue_pane.isHidden()

    builder._show_draft_view()
    assert builder.browse_workspace.isHidden()
    assert not builder.queue_pane.isHidden()

    builder._show_browse_view()
    assert not builder.browse_workspace.isHidden()
    assert builder.queue_pane.isHidden()

    window.resize(1320, 840)
    window._apply_sidebar_visibility()
    app.processEvents()
    assert not window.sidebar.isHidden()
    assert not window.library_navigation_panel.isHidden()


def test_problem_detail_toolbar_groups_actions_and_keeps_priority_controls(
    window: MainWindow,
) -> None:
    app = QApplication.instance()
    assert app is not None
    page = window.problem_detail_page

    assert page.edit_button.objectName() == "PrimaryButton"
    assert page.header.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed
    assert [action.text() for action in page.more_menu.actions()] == [
        "归档",
        "移入回收站",
        "恢复到正式题库",
    ]
    assert page.trash_action.property("danger") is True

    page.resize(1200, 720)
    page._update_toolbar_layout(force=True)
    app.processEvents()
    assert page.toolbar_actions.direction() == QBoxLayout.Direction.LeftToRight
    assert all(not divider.isHidden() for divider in page._toolbar_dividers)

    page.resize(900, 720)
    page._update_toolbar_layout(force=True)
    app.processEvents()
    assert page.toolbar_actions.direction() == QBoxLayout.Direction.TopToBottom
    assert page._toolbar_priority_layout.itemAt(0).widget() is page.switch_group
    assert page._toolbar_priority_layout.itemAt(2).widget() is page.management_group


def test_problem_detail_chat_prefers_reader_width_and_image_reference_canvas(
    window: MainWindow,
) -> None:
    app = QApplication.instance()
    assert app is not None
    page = window.problem_detail_page
    page.resize(1200, 720)
    page.workspace.setGeometry(0, 0, 1100, 600)
    page._set_chat_split_sizes()
    app.processEvents()

    left, right = page.workspace.sizes()
    assert left > right
    assert page.chat_card.minimumWidth() == 360

    assert page.reader_stack.count() == 2
    assert page.reader_stack.currentWidget() is page.reader
    assert page.reference_canvas.parentWidget() is page.reader_stack


def test_problem_detail_chat_preserves_focus_layout_scroll_and_conversation(
    window: MainWindow,
) -> None:
    app = QApplication.instance()
    assert app is not None
    window.show()
    app.processEvents()
    problem = next(
        item for item in window.services.list_problems() if item.status == "active"
    )
    first = window.problem_chat.create_conversation(problem.id, title="第一段讨论")
    second = window.problem_chat.create_conversation(problem.id, title="当前讨论")
    window._open_problem_detail(problem.id)
    page = window.problem_detail_page
    window.resize(1366, 760)
    app.processEvents()
    page.chat_button.setFocus()
    page._toggle_chat()
    app.processEvents()

    assert page.workspace.orientation() == Qt.Orientation.Horizontal
    assert page.reader.isVisible()
    assert page.chat_card.isVisible()
    assert app.focusWidget() is page.chat_input
    page.conversation_combo.setCurrentIndex(page.conversation_combo.findData(second.id))
    page.reader._scroll_position = 47

    window.resize(760, 760)
    app.processEvents()
    assert page.workspace.orientation() == Qt.Orientation.Vertical
    assert page.reader.isHidden()
    assert page.chat_button.text() == "查看题目"
    page._toggle_chat()
    app.processEvents()
    assert page.chat_card.isHidden()
    assert page.reader.isVisible()
    assert app.focusWidget() is page.reader

    window._close_problem_detail()
    window._open_problem_detail(problem.id)
    page._restore_reader_scroll()
    assert page.conversation_combo.currentData() == second.id
    assert page.reader.scroll_position() == 47

    window.resize(1920, 900)
    page._toggle_chat()
    app.processEvents()
    assert page.workspace.orientation() == Qt.Orientation.Horizontal
    assert page.reader.isVisible()
    assert page.chat_card.isVisible()
    assert page.conversation_combo.findData(first.id) >= 0


def test_problem_detail_reference_canvas_stays_embedded_and_keeps_normalized_regions(
    window: MainWindow,
    tmp_path: Path,
) -> None:
    page = window.problem_detail_page
    image_path = tmp_path / "reference.png"
    image = QImage(20, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("#FFFFFF"))
    assert image.save(str(image_path), "PNG")

    page.reference_canvas.set_source("asset_reference", 0, image_path)

    page.reader_stack.setCurrentWidget(page.reference_canvas)
    page.reference_canvas.add_normalized_region(0.1, 0.2, 0.3, 0.4)
    before = page.reference_canvas.references()
    page.reference_canvas.resize(640, 480)

    assert page.reference_canvas.parentWidget() is page.reader_stack
    assert page.reader_stack.currentWidget() is page.reference_canvas
    assert page.reference_canvas.references() == before
    assert page.reference_previews.count() == 1
    assert page.reference_summary.text() == "本次引用 1 个区域"
    assert not page.reference_canvas.isWindow()


def test_ai_completion_entry_opens_page_preparation_without_starting_job(
    window: MainWindow,
) -> None:
    item = window.problem_list.item(0)
    assert item is not None
    window.problem_list.setCurrentItem(item)
    before = len(window.ai.list_jobs(limit=100))

    window._ai_recognize()

    page = window.ai_completion_page
    assert window.stack.currentWidget() is page
    assert page.stack.currentWidget() is page.prepare_page
    assert page.new_task_panel.isVisibleTo(page)
    assert page.start_analysis_button.text() == "开始分析"
    assert len(window.ai.list_jobs(limit=100)) == before
    assert page.review_back_button.accessibleName() == "返回上一页"


def test_service_settings_use_a_compact_aligned_form_surface(
    window: MainWindow,
) -> None:
    ai_settings = window.ai_settings_page
    appearance_settings = window.appearance_settings_page
    cloud_settings = window.cloud_settings_page

    for page in (ai_settings, appearance_settings, cloud_settings):
        assert page.settings_content.maximumWidth() == 800

    assert all(
        button.isCheckable() for button in appearance_settings.theme_buttons.values()
    )
    assert (
        ai_settings.fetch_ai_models.parentWidget()
        is ai_settings.ai_model.parentWidget()
    )
    assert isinstance(ai_settings.ai_model, ChevronComboBox)
    assert ai_settings.ai_model.isEditable()
    assert ai_settings.ai_model.property("visibleChevron") is True
    assert ai_settings.fetch_ai_models.text() == "获取可用模型名称"
    assert ai_settings.clear_ai_button.objectName() == "DangerButton"
    assert cloud_settings.clear_cloud_token_button.objectName() == "DangerButton"


def test_system_theme_choice_remains_highlighted_when_system_is_dark(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appearance_settings = window.appearance_settings_page
    monkeypatch.setattr(settings_dialog_module, "current_theme_name", lambda: "dark")

    appearance_settings.theme_mode.setCurrentIndex(
        appearance_settings.theme_mode.findData("system")
    )
    assert appearance_settings.theme_mode.currentData() == "system"
    appearance_settings._refresh_theme_status()

    assert appearance_settings.theme_buttons["system"].property("themeSelected") is True
    assert appearance_settings.theme_buttons["light"].property("themeSelected") is False
    assert appearance_settings.theme_buttons["dark"].property("themeSelected") is False
    assert "深色" in appearance_settings.theme_status.text()


def test_review_plan_builder_warns_before_creating_an_empty_plan(
    window: MainWindow,
) -> None:
    builder = window.review_page.plan_builder_page
    builder.services.clear_review_waiting_queue(builder.content_type)
    builder._refresh_queue()
    messages: list[tuple[str, str]] = []
    builder.status_message.connect(messages.append)
    builder._confirm_create()

    assert messages == ["请先从左侧选择题目或笔记，并加入计划草稿。"]
    assert not builder.toast.isHidden()
    assert builder.toast.label.text() == messages[0]
    assert builder.draft_back_button.text() == ""
    assert builder.draft_back_button.accessibleName() == "返回资料"


def test_review_plan_builder_materializes_sources_and_queue_in_batches(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = window.review_page.plan_builder_page
    monkeypatch.setattr(review_plan_builder_module, "_LIST_BATCH_SIZE", 2)
    builder.content_type = "note"
    builder._source_entries = [
        ("note-1", "第一篇", False),
        ("note-2", "第二篇", True),
        ("note-3", "第三篇", False),
    ]
    builder._source_visible_count = 0
    builder.source_list.clear()

    builder._append_source_batch()

    assert builder.source_list.count() == 2
    assert builder.source_list.item(1).data(Qt.ItemDataRole.UserRole + 1) is True
    builder._append_source_batch()
    assert builder.source_list.count() == 3

    builder._queue_ids = ["note-1", "note-2", "note-3"]
    builder._queue_labels = {source_id: source_id for source_id in builder._queue_ids}
    builder._queue_visible_count = 0
    builder.queue_list.clear()
    builder._append_queue_batch()
    assert builder.queue_list.count() == 2
    builder._append_queue_batch()
    assert builder.queue_list.count() == 3


def test_intake_workflow_uses_steps_surfaces_and_inset_file_selection(
    window: MainWindow,
) -> None:
    page = window.intake_page

    assert page.ai_upload_steps.current_step == 0
    assert page.ai_processing_steps_bar.current_step == 0
    assert page.ai_confirmation_steps.current_step == 1
    assert page.ai_confirmation_surface.objectName() == "IntakeConfirmationSurface"
    processing_buttons = page.stack.widget(2).findChildren(QPushButton)
    assert any(button.accessibleName() == "返回上传页" for button in processing_buttons)
    assert not any(button.text() == "返回上传页" for button in processing_buttons)
    assert page.ai_confirmation_action_bar.objectName() == "IntakeActionBar"
    assert isinstance(page.ai_file_list.itemDelegate(), SoftItemDelegate)
    assert page.ai_upload_content_host.minimumHeight() == 0
    assert page.ai_upload_file_actions.count() == 4
    assert page.ai_upload_file_actions.itemAt(0).spacerItem() is not None
    assert page.ai_upload_file_actions.itemAt(3).spacerItem() is not None
    assert (
        page.ai_upload_content_layout.itemAt(1).alignment() == Qt.AlignmentFlag.AlignTop
    )


def test_first_ai_submission_is_not_mistaken_for_an_active_none_job(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    page = window.intake_page
    started_jobs: list[str] = []

    class Started:
        job_id = "job_first_submission"

    monkeypatch.setattr(page.intake, "start_ai", lambda *_args, **_kwargs: Started())
    monkeypatch.setattr(page, "_start_worker", started_jobs.append)
    page.ai_job_id = None
    page.ai_files = [Path("question.png")]
    page.show_ai_upload()

    page._start_ai()

    assert started_jobs == ["job_first_submission"]
    assert page.stack.currentIndex() == 1
    assert not page.ai_task_surface.isHidden()


def _select_mode(window: MainWindow, mode: str) -> None:
    if window._library_view == "browse":
        item = window._find_knowledge_item(mode)
        if item is not None:
            window.knowledge_tree.setCurrentItem(item)
            return
    else:
        for index in range(window.process_nav.count()):
            item = window.process_nav.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == mode:
                window.process_nav.setCurrentRow(index)
                return
    raise AssertionError(f"missing navigation mode: {mode}")


def _problem_titles(window: MainWindow) -> list[str]:
    titles: list[str] = []
    for index in range(window.problem_list.count()):
        widget = window.problem_list.itemWidget(window.problem_list.item(index))
        title = widget.findChild(QLabel, "QuestionItemTitle") if widget else None
        assert title is not None
        titles.append(title.toolTip())
    return titles


def _wait_for_ai_search(window: MainWindow, timeout: float = 3.0) -> None:
    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while window._ai_search_worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert window._ai_search_worker is None


def test_library_views_separate_knowledge_and_lifecycle_navigation(
    window: MainWindow,
) -> None:
    assert window._library_view == "browse"
    assert window.library_browse_button.isChecked()
    assert _nav_modes(window)[0].startswith("subject:")
    assert any(mode.startswith("subject:") for mode in _nav_modes(window))
    assert "inbox" not in _nav_modes(window)
    assert set(_problem_titles(window)) == {
        "未分类极限题",
        "积分基础题",
        "二重积分题",
    }
    assert not window.new_subject_button.isHidden()

    window._set_library_view("process")
    assert window.library_process_button.isChecked()
    assert _nav_modes(window) == [
        "active",
        "due",
        "favorite",
        "recent",
        "inbox",
        "archived",
        "trashed",
    ]
    assert _problem_titles(window) == ["待整理题"]
    assert window.new_subject_button.isHidden()

    _select_mode(window, "archived")
    assert _problem_titles(window) == ["归档题"]
    window._set_library_view("browse")
    _select_mode(
        window, next(mode for mode in _nav_modes(window) if mode.startswith("subject:"))
    )
    assert set(_problem_titles(window)) == {
        "未分类极限题",
        "积分基础题",
        "二重积分题",
    }

    window._set_library_view("process")
    assert window._nav_mode == "archived"
    assert _problem_titles(window) == ["归档题"]


def test_library_uses_soft_workspace_components(window: MainWindow) -> None:
    assert window.library_splitter.handleWidth() == 10
    assert isinstance(window.knowledge_tree.itemDelegate(), SoftItemDelegate)
    assert isinstance(window.process_nav.itemDelegate(), SoftItemDelegate)
    assert isinstance(window.problem_list.itemDelegate(), SoftItemDelegate)
    assert window.library_navigation_panel.metaObject().className() == "QFrame"
    assert window.library_splitter.widget(1).objectName() == "LibraryListPanel"
    assert (
        window.knowledge_tree.palette()
        .color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)
        .alpha()
        == 0
    )


def test_question_delete_action_is_next_to_edit_and_not_in_more_menu(
    window: MainWindow,
) -> None:
    layout = window.question_action_bar.layout()

    assert layout.indexOf(window.question_delete_button) == (
        layout.indexOf(window.question_edit_button) + 1
    )
    assert window.question_delete_button.objectName() == "DangerButton"
    assert "删除" not in {
        action.text() for action in window._build_question_more_menu().actions()
    }


def test_large_library_only_materializes_rows_near_viewport(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QApplication.instance()
    assert app is not None
    problem_ids = [
        window.services.create_problem(
            title=f"虚拟化题目 {index:03d}",
            question_markdown=f"题干 {index}",
            status="active",
        ).id
        for index in range(80)
    ]
    problems = window.services.list_problems_by_ids(problem_ids)
    monkeypatch.setattr(window, "_problems_for_current_view", lambda: problems)
    window.show()
    window._show_navigation_page(2)
    window.refresh_problems(preserve_view=False)
    app.processEvents()

    initial_widgets = sum(
        window.problem_list.itemWidget(window.problem_list.item(row)) is not None
        for row in range(window.problem_list.count())
    )
    assert window.problem_list.count() == 80
    assert 0 < initial_widgets < 40
    assert window.problem_list.itemWidget(window.problem_list.item(0)) is not None
    assert window.problem_list.itemWidget(window.problem_list.item(79)) is None

    window.problem_list.scrollToBottom()
    app.processEvents()
    window._materialize_visible_problem_widgets()

    assert window.problem_list.itemWidget(window.problem_list.item(79)) is not None
    assert window.problem_list.itemWidget(window.problem_list.item(0)) is None


def test_releasing_inline_question_widget_does_not_create_window(
    window: MainWindow,
) -> None:
    item = window.problem_list.item(0)
    widget = window.problem_list.itemWidget(item)

    assert widget is not None
    window._release_inline_question_widget(0)

    assert not widget.isWindow()


def test_question_preview_expands_inline_and_remains_single(window: MainWindow) -> None:
    first = window.problem_list.item(0)
    second = window.problem_list.item(1)
    first_id = str(first.data(Qt.ItemDataRole.UserRole))
    second_id = str(second.data(Qt.ItemDataRole.UserRole))

    window._toggle_question_expansion(first)
    assert window._expanded_question_id == first_id
    expanded = window.problem_list.itemWidget(window.problem_list.item(0))
    assert expanded is not None
    assert window.problem_list.item(0).text() == ""
    assert not expanded.findChildren(QLabel, "InlinePreviewTitle")

    window._toggle_question_expansion(window.problem_list.item(1))
    assert window._expanded_question_id == second_id
    assert window._expanded_question_id != first_id

    window._toggle_question_expansion(window.problem_list.item(1))
    assert window._expanded_question_id is None
    collapsed = window.problem_list.itemWidget(window.problem_list.item(1))
    assert collapsed is not None
    assert not collapsed.findChildren(QLabel, "InlinePreviewTitle")


def test_question_preview_toggle_does_not_rebuild_problem_list(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = window.problem_list.item(0)
    problem_id = str(item.data(Qt.ItemDataRole.UserRole))
    refresh_calls = 0

    def record_refresh(*_args, **_kwargs) -> None:
        nonlocal refresh_calls
        refresh_calls += 1

    monkeypatch.setattr(window, "refresh_problems", record_refresh)

    window._toggle_question_by_id(problem_id)

    assert refresh_calls == 0
    expanded = window.problem_list.itemWidget(item)
    assert expanded is not None
    reader = expanded.findChild(MathContentView)
    assert reader is not None
    assert reader.height() == 420


def test_formula_content_surfaces_use_bounded_adaptive_height(
    window: MainWindow,
) -> None:
    first = window.problem_list.item(0)
    window._toggle_question_expansion(first)
    expanded = window.problem_list.itemWidget(window.problem_list.item(0))
    assert expanded is not None
    inline_reader = expanded.findChild(MathContentView)
    assert inline_reader is not None
    assert inline_reader._content_height_limit == 420

    assert window.problem_detail_page.reader.fit_content_height_args == ()
    assert window.problem_detail_page.reader.fit_content_height_kwargs == {
        "expand_widget": False,
    }
    assert window.intake_page.ai_result_preview.adaptive_height_limit == 520
    assert {
        preview.adaptive_height_limit
        for _label, preview in window.intake_page.ai_form._field_previews.values()
    } == {320}


def test_due_navigation_returns_to_processing_center(window: MainWindow) -> None:
    window._set_library_view("process")
    _select_mode(window, "trashed")

    window._goto_due_in_library()

    assert window._library_view == "process"
    assert window._nav_mode == "due"
    assert window.library_process_button.isChecked()


def test_knowledge_tree_aggregates_descendants_and_preserves_expansion(
    window: MainWindow,
) -> None:
    subject_mode = next(
        mode for mode in _nav_modes(window) if mode.startswith("subject:")
    )
    chapter_modes = [mode for mode in _nav_modes(window) if mode.startswith("chapter:")]
    parent_mode = next(
        mode
        for mode in chapter_modes
        if window._find_knowledge_item(mode).text(0).startswith("积分 ·")
    )
    child_mode = next(mode for mode in chapter_modes if mode != parent_mode)
    uncategorized_mode = next(
        mode for mode in _nav_modes(window) if mode.startswith("uncategorized:")
    )

    _select_mode(window, parent_mode)
    assert set(_problem_titles(window)) == {"积分基础题", "二重积分题"}
    assert window.library_breadcrumb.text() == "题库 / 高等数学 / 积分"

    _select_mode(window, child_mode)
    assert _problem_titles(window) == ["二重积分题"]
    assert window.library_breadcrumb.text() == "题库 / 高等数学 / 积分 / 二重积分"

    _select_mode(window, uncategorized_mode)
    assert _problem_titles(window) == ["未分类极限题"]

    subject_item = window._find_knowledge_item(subject_mode)
    parent_item = window._find_knowledge_item(parent_mode)
    assert subject_item is not None
    assert parent_item is not None
    subject_item.setExpanded(True)
    parent_item.setExpanded(True)
    window._set_library_view("process")
    window._set_library_view("browse")

    assert window._find_knowledge_item(subject_mode).isExpanded()
    assert window._find_knowledge_item(parent_mode).isExpanded()
    assert window._nav_mode == uncategorized_mode
    assert _problem_titles(window) == ["未分类极限题"]


def test_catalog_menu_and_editor_use_valid_full_paths(window: MainWindow) -> None:
    child_mode = next(
        mode for mode in _nav_modes(window) if mode.startswith("chapter:")
    )
    _select_mode(window, child_mode)
    assert [spec.action_id for spec in window.get_create_actions()] == [
        "create_chapter",
        "create_tag",
    ]
    assert [spec.action_id for spec in window.get_manage_actions()] == [
        "rename_node",
        "move_node_up",
        "move_node_down",
        "delete_node",
    ]


def test_catalog_actions_follow_selected_node_type_and_position(
    window: MainWindow,
) -> None:
    window.knowledge_tree.setCurrentItem(None)
    window._update_catalog_action_buttons()
    assert [spec.action_id for spec in window.get_create_actions()] == [
        "create_subject",
        "create_tag",
    ]
    assert window.get_manage_actions() == ()
    assert not window.catalog_menu_button.isEnabled()

    subject_mode = next(
        mode for mode in _nav_modes(window) if mode.startswith("subject:")
    )
    _select_mode(window, subject_mode)
    assert [spec.action_id for spec in window.get_create_actions()] == [
        "create_chapter",
        "create_tag",
    ]
    subject_actions = window.get_manage_actions()
    assert [spec.label for spec in subject_actions] == [
        "重命名科目",
        "科目上移",
        "科目下移",
        "删除科目",
    ]
    assert not subject_actions[1].enabled
    assert not subject_actions[-1].enabled

    chapter_mode = next(
        mode for mode in _nav_modes(window) if mode.startswith("chapter:")
    )
    _select_mode(window, chapter_mode)
    assert [spec.action_id for spec in window.get_create_actions()] == [
        "create_chapter",
        "create_tag",
    ]
    assert [spec.label for spec in window.get_manage_actions()] == [
        "重命名章节",
        "章节上移",
        "章节下移",
        "删除章节",
    ]

    uncategorized_mode = next(
        mode for mode in _nav_modes(window) if mode.startswith("uncategorized:")
    )
    _select_mode(window, uncategorized_mode)
    window._update_catalog_action_buttons()
    uncategorized_item = window._find_knowledge_item(uncategorized_mode)
    assert uncategorized_item is not None
    assert uncategorized_item.text(0) == "未指定章节 · 1"
    assert "不是实际章节" in uncategorized_item.toolTip(0)
    assert [spec.action_id for spec in window.get_create_actions()] == ["create_tag"]
    assert window.get_manage_actions() == ()
    assert not window.catalog_menu_button.isEnabled()
    assert "不是实际章节" in window.catalog_menu_button.toolTip()


def test_empty_uncategorized_filter_is_hidden(window: MainWindow) -> None:
    uncategorized = next(
        problem
        for problem in window.services.list_problems()
        if problem.title == "未分类极限题"
    )
    chapter_mode = next(
        mode for mode in _nav_modes(window) if mode.startswith("chapter:")
    )
    _, subject_id, chapter_id = chapter_mode.split(":", 2)

    window.services.move_problems_to_category(
        [uncategorized.id],
        subject_id=subject_id,
        chapter_id=chapter_id,
    )
    window.refresh_nav()

    assert not any(mode.startswith("uncategorized:") for mode in _nav_modes(window))


def test_smart_views_and_search_scopes_are_stable(window: MainWindow) -> None:
    window._set_library_view("process")
    assert {"favorite", "recent"}.issubset(_nav_modes(window))

    _select_mode(window, "favorite")
    assert _problem_titles(window) == ["二重积分题"]
    assert window.library_breadcrumb.text() == "处理中心 / 我的收藏"

    _select_mode(window, "recent")
    assert set(_problem_titles(window)) == {
        "未分类极限题",
        "积分基础题",
        "二重积分题",
    }

    scopes = window.services.list_knowledge_scopes()
    labels = {scope.label for scope in scopes}
    assert "高等数学 / 积分 / 二重积分" in labels
    child_scope = next(
        scope for scope in scopes if scope.label == "高等数学 / 积分 / 二重积分"
    )
    assert child_scope.include_descendants
    assert window.services.filter_for_knowledge_scope(child_scope).chapter_id


def test_local_search_controls_explain_mode_and_privacy(
    window: MainWindow,
) -> None:
    assert window.local_search_button.isChecked()
    assert window.local_search_button.isEnabled()
    assert window.ai_search_button.isEnabled()
    assert "有限候选" in window.ai_search_button.toolTip()
    assert "完全离线" in window.search_privacy_hint.text()
    assert window.search_scope_combo.currentData() == "current"


def test_local_search_uses_index_and_current_knowledge_scope(
    window: MainWindow,
) -> None:
    parent_mode = next(
        mode
        for mode in _nav_modes(window)
        if mode.startswith("chapter:")
        and window._find_knowledge_item(mode).text(0).startswith("积分 ·")
    )
    _select_mode(window, parent_mode)
    window.search_edit.setText("未分类极限题")
    window.refresh_problems()
    assert _problem_titles(window) == []
    assert "0 条结果" in window.library_list_hint.text()

    window.search_scope_combo.setCurrentIndex(1)
    assert _problem_titles(window) == ["未分类极限题"]
    assert "全部正式题目" in window.library_list_hint.text()

    window.search_edit.setText("格林公式")
    window.refresh_problems()
    assert _problem_titles(window) == ["二重积分题"]


def test_processing_search_stays_in_current_lifecycle_status(
    window: MainWindow,
) -> None:
    window._set_library_view("process")
    assert not window.search_scope_combo.isEnabled()
    window.search_edit.setText("题")
    window.refresh_problems()
    assert _problem_titles(window) == ["待整理题"]

    _select_mode(window, "archived")
    assert _problem_titles(window) == ["归档题"]
    assert window.search_scope_combo.currentData() == "current"

    window._clear_library_search()
    assert window.search_edit.text() == ""
    assert _problem_titles(window) == ["归档题"]
    assert window.library_list_hint.text() == "处理中心题目 · 双击打开详情"


def test_ai_search_runs_in_background_and_displays_reason(
    window: MainWindow,
) -> None:
    window.ai_search_button.click()
    assert window.ai_search_button.isChecked()
    assert "描述想找的题目" in window.search_edit.placeholderText()

    window.search_edit.setText("二重积分题")
    window._submit_library_search()
    assert window._ai_search_worker is not None
    assert not window.search_button.isEnabled()
    _wait_for_ai_search(window)

    assert _problem_titles(window) == ["二重积分题"]
    assert "AI 推荐" in window.library_list_hint.text()
    assert "Mock：本地候选" in window.problem_list.item(0).toolTip()
    assert "字段：" in window.search_privacy_hint.text()
    assert "正确答案" not in window.search_privacy_hint.text()

    window.local_search_button.click()
    window.search_edit.setText("格林公式")
    window._submit_library_search()
    assert _problem_titles(window) == ["二重积分题"]
    assert "普通搜索" in window.library_list_hint.text()


def test_ai_search_failure_keeps_query_and_offline_fallback(
    window: MainWindow,
) -> None:
    class FailingSearch:
        def search(self, *_args, progress=None, **_kwargs):
            if progress is not None:
                progress("intent")
            raise DomainError("模拟网络中断")

    window.ai_search = FailingSearch()
    window.ai_search_button.click()
    window.search_edit.setText("保留这段查询")
    window._submit_library_search()
    _wait_for_ai_search(window)

    assert window.search_edit.text() == "保留这段查询"
    assert "模拟网络中断" in window.search_privacy_hint.text()
    assert window.local_search_button.isEnabled()
    window.local_search_button.click()
    assert window.local_search_button.isChecked()
    assert "完全离线" in window.search_privacy_hint.text()


def test_low_risk_selection_and_intake_hints_do_not_open_message_box(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *_args, **_kwargs: pytest.fail("低风险提示不应打开阻塞消息框"),
    )
    window.problem_list.clearSelection()
    assert window._require_one() is None
    assert window.toast.label.text() == "请先选择一道题"

    messages: list[str] = []
    window.intake_page.status_message.connect(messages.append)
    window.intake_page.answer_image = None
    window.intake_page._start_answer_recognition()
    assert (
        window.intake_page.answer_recognition_status.text()
        == "请先选择包含作答的图片。"
    )
    window.intake_page.answer_recognition_result.clear()
    window.intake_page._apply_answer_recognition()
    assert messages[-2:] == ["请先选择包含作答的图片", "没有可填入的作答内容"]


def test_mock_provider_hint_and_task_selection_are_non_blocking(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *_args, **_kwargs: pytest.fail("低风险提示不应打开阻塞消息框"),
    )
    settings = window.ai_settings_page
    settings.ai_provider.setCurrentIndex(settings.ai_provider.findData("mock"))
    messages: list[str] = []
    settings.status_message.connect(messages.append)
    settings._test_ai_connection()
    assert messages[-1] == "Mock 不访问网络；连接测试请先选择 Faro API"

    dialog = TaskCenterDialog(window.ai)
    dialog._run_selected()
    assert dialog.summary.text() == "请先选择一个后台任务"
    dialog.close()


def test_settings_expose_loading_failure_disabled_and_permission_states(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ai_settings = window.ai_settings_page
    ai_settings._on_ai_models_loaded(["vision-model"])
    assert ai_settings.ai_model_status.property("state") == "success"
    assert "1 个可用模型" in ai_settings.ai_model_status.text()
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    ai_settings._on_ai_models_failed("network unavailable")
    assert ai_settings.ai_model_status.property("state") == "error"

    monkeypatch.setattr(settings_dialog_module, "get_secret", lambda _key: None)
    cloud_settings = window.cloud_settings_page
    cloud_settings.provider.setCurrentIndex(cloud_settings.provider.findData("cloudbase"))
    assert cloud_settings.cloud_permission_notice.property("state") == "permission"
    assert "当前不可用" in cloud_settings.cloud_permission_notice.text()
    cloud_settings.provider.setCurrentIndex(
        cloud_settings.provider.findData("local_folder")
    )
    assert cloud_settings.cloud_permission_notice.property("state") == "disabled"
    assert not cloud_settings.token_edit.isEnabled()


def test_settings_dirty_state_only_updates_the_attached_save_button(
    window: MainWindow,
) -> None:
    pages = (
        (window.ai_settings_page, window.ai_settings_page.apply_ai_button),
        (
            window.appearance_settings_page,
            window.appearance_settings_page.apply_theme_button,
        ),
        (window.cloud_settings_page, window.cloud_settings_page.apply_cloud_button),
    )
    for page, save_button in pages:
        page._mark_dirty()
        assert page.has_unsaved_changes
        assert save_button.isEnabled()
        page.discard_unsaved_changes()
        assert not save_button.isEnabled()


def test_settings_show_field_level_validation_errors(window: MainWindow) -> None:
    ai_settings = window.ai_settings_page
    ai_settings.ai_model.setCurrentText("")
    ai_settings._apply_ai_session()
    assert not ai_settings._field_errors["ai_model"].isHidden()

    cloud_settings = window.cloud_settings_page
    cloud_settings.provider.setCurrentIndex(cloud_settings.provider.findData("cloudbase"))
    cloud_settings.cloudbase_environment_edit.setText("test-environment")
    cloud_settings.cloudbase_gateway_edit.setText("https://gateway.example.test")
    cloud_settings.owner_edit.clear()
    cloud_settings.repo_edit.clear()
    cloud_settings._save_cloud_settings()
    assert not cloud_settings._field_errors["cloud_owner"].isHidden()
    cloud_settings.owner_edit.setText("owner")
    cloud_settings._test_cloud()
    assert not cloud_settings._field_errors["cloud_repo"].isHidden()


def test_settings_secrets_are_hidden_by_default_and_can_be_revealed(
    window: MainWindow,
) -> None:
    ai_settings = window.ai_settings_page
    assert ai_settings.ai_token_edit.echoMode() == QLineEdit.EchoMode.Password
    ai_settings.ai_token_visibility_button.click()
    assert ai_settings.ai_token_edit.echoMode() == QLineEdit.EchoMode.Normal
    assert ai_settings.ai_token_visibility_button.text() == "隐藏"

    cloud_settings = window.cloud_settings_page
    cloud_settings.provider.setCurrentIndex(cloud_settings.provider.findData("cloudbase"))
    assert cloud_settings.token_edit.echoMode() == QLineEdit.EchoMode.Password
    cloud_settings.token_visibility_button.click()
    assert cloud_settings.token_edit.echoMode() == QLineEdit.EchoMode.Normal


def test_unsaved_settings_require_confirmation_before_leaving(
    window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window.main_nav.setCurrentRow(4)
    settings = window.ai_settings_page
    assert not settings.apply_ai_button.isEnabled()
    settings.ai_model.setCurrentText("changed-model")
    assert settings.has_unsaved_changes
    assert settings.apply_ai_button.isEnabled()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    window.main_nav.setCurrentRow(0)

    assert window.stack.currentIndex() == 5
    assert settings.has_unsaved_changes
