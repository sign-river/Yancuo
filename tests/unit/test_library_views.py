"""Library browse and processing views keep separate navigation state."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

import yancuo_win.ui.intake_page as intake_page_module
import yancuo_win.ui.note_page as note_page_module
import yancuo_win.ui.problem_detail as problem_detail_module
import yancuo_win.ui.review_page as review_page_module
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.main_window import MainWindow
from yancuo_win.ui.problem_editor import ProblemEditorDialog


class _ReaderStub(QWidget):
    def set_problem(self, *_args, **_kwargs) -> None:
        pass

    def set_message(self, *_args, **_kwargs) -> None:
        pass

    def set_note(self, *_args, **_kwargs) -> None:
        pass


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
    return [
        window.problem_list.item(index).text().splitlines()[0]
        for index in range(window.problem_list.count())
    ]


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
    assert _nav_modes(window)[:2] == ["active", "due"]
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
    assert _nav_modes(window) == ["inbox", "archived", "trashed"]
    assert _problem_titles(window) == ["待整理题"]
    assert window.new_subject_button.isHidden()

    _select_mode(window, "archived")
    assert _problem_titles(window) == ["归档题"]
    window._set_library_view("browse")
    _select_mode(window, next(mode for mode in _nav_modes(window) if mode.startswith("subject:")))
    assert set(_problem_titles(window)) == {
        "未分类极限题",
        "积分基础题",
        "二重积分题",
    }

    window._set_library_view("process")
    assert window._nav_mode == "archived"
    assert _problem_titles(window) == ["归档题"]


def test_due_navigation_returns_to_browse_view(window: MainWindow) -> None:
    window._set_library_view("process")
    _select_mode(window, "trashed")

    window._goto_due_in_library()

    assert window._library_view == "browse"
    assert window._nav_mode == "due"
    assert window.library_browse_button.isChecked()


def test_knowledge_tree_aggregates_descendants_and_preserves_expansion(
    window: MainWindow,
) -> None:
    subject_mode = next(
        mode for mode in _nav_modes(window) if mode.startswith("subject:")
    )
    chapter_modes = [
        mode for mode in _nav_modes(window) if mode.startswith("chapter:")
    ]
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
        mode
        for mode in _nav_modes(window)
        if mode.startswith("chapter:")
        and window._find_knowledge_item(mode).text(0).startswith("二重积分")
    )
    _select_mode(window, child_mode)
    actions = [action.text() for action in window._build_catalog_menu().actions()]
    assert {
        "新建子章节",
        "重命名章节",
        "移动到其他上级",
        "章节上移",
        "章节下移",
        "删除章节",
    }.issubset(actions)
    assert any(button.text() == "移动分类" for button in window._ctx_buttons)

    problem = next(
        problem
        for problem in window.services.list_problems()
        if problem.title == "二重积分题"
    )
    dialog = ProblemEditorDialog(window.services, problem, window)
    assert "积分 / 二重积分" in [
        dialog.chapter.itemText(index)
        for index in range(dialog.chapter.count())
    ]
    dialog.close()


def test_smart_views_and_search_scopes_are_stable(window: MainWindow) -> None:
    assert {"favorite", "recent"}.issubset(_nav_modes(window))

    _select_mode(window, "favorite")
    assert _problem_titles(window) == ["二重积分题"]
    assert window.library_breadcrumb.text() == "题库 / 我的收藏"

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
    assert window.library_list_hint.text() == "待处理题目 · 双击打开详情"


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
    assert "Mock：本地候选" in window.problem_list.item(0).text()
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
