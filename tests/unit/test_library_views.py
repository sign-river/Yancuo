"""Library browse and processing views keep separate navigation state."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

import yancuo_win.ui.intake_page as intake_page_module
import yancuo_win.ui.problem_detail as problem_detail_module
import yancuo_win.ui.review_page as review_page_module
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.ui.main_window import MainWindow
from yancuo_win.ui.problem_editor import ProblemEditorDialog


class _ReaderStub(QWidget):
    def set_problem(self, *_args, **_kwargs) -> None:
        pass

    def set_message(self, *_args, **_kwargs) -> None:
        pass


@pytest.fixture()
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setenv("YANCUO_AI__DEFAULT_PROVIDER", "mock")
    monkeypatch.setattr(intake_page_module, "MathContentView", _ReaderStub)
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
    services.create_problem(
        title="二重积分题",
        status="active",
        subject_id=subject.id,
        chapter_id=double.id,
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
