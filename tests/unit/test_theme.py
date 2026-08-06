"""Theme resolution and stylesheet regression tests."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

import yancuo_win.ui.widgets as widgets_module
from yancuo_win.ui.theme import (
    DARK_THEME,
    LIGHT_THEME,
    ThemeManager,
    UI_METRICS,
    _check_mark_url,
    app_stylesheet,
    normalize_theme_mode,
    resolve_theme_mode,
)


def test_page_header_does_not_show_children_during_construction(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    visibility_requests: list[bool] = []

    class TrackingLabel(QLabel):
        def setVisible(self, visible: bool) -> None:  # noqa: N802
            visibility_requests.append(visible)
            super().setVisible(visible)

    monkeypatch.setattr(widgets_module, "QLabel", TrackingLabel)
    header = widgets_module.PageHeader("标题", "说明")

    assert True not in visibility_requests
    header.close()
    app.processEvents()


def test_page_header_allocates_remaining_width_to_description() -> None:
    app = QApplication.instance() or QApplication([])
    header = widgets_module.PageHeader("标题", "一行说明文字应在宽窗口保持单行。")
    header.resize(900, 100)
    header.show()
    app.processEvents()

    assert header.description.width() > 500
    assert header.description.height() <= header.description.fontMetrics().height() + 4
    header.close()


def test_workflow_step_bar_exposes_current_completed_and_upcoming_states() -> None:
    app = QApplication.instance() or QApplication([])
    bar = widgets_module.WorkflowStepBar(("上传", "处理", "确认"), 1)

    assert bar.current_step == 1
    assert [label.property("state") for label in bar.labels] == [
        "completed",
        "current",
        "upcoming",
    ]
    assert [connector.property("state") for connector in bar.connectors] == [
        "completed",
        "upcoming",
    ]
    with pytest.raises(ValueError):
        bar.set_current_step(3)
    bar.close()
    app.processEvents()


@pytest.mark.parametrize("width", [760, 1366, 1920])
def test_workflow_step_bar_keeps_full_labels_at_target_widths(width: int) -> None:
    app = QApplication.instance() or QApplication([])
    names = ("上传图片", "后台处理", "确认结果")
    bar = widgets_module.WorkflowStepBar(names, 1)
    bar.resize(width, bar.sizeHint().height())
    bar.show()
    app.processEvents()

    for index, label in enumerate(bar.labels):
        assert label.text() == f"{index + 1}  {names[index]}"
        assert label.width() >= label.minimumWidth()
        assert label.accessibleName().endswith(names[index])
    assert all(connector.width() >= 8 for connector in bar.connectors)
    assert sum(connector.width() for connector in bar.connectors) > sum(
        label.width() for label in bar.labels
    ) / 4
    bar.close()
    app.processEvents()


def test_deferred_view_updates_restores_repaint_state_after_failure() -> None:
    app = QApplication.instance() or QApplication([])
    view = QListWidget()

    with pytest.raises(RuntimeError):
        with widgets_module.deferred_view_updates(view):
            assert not view.updatesEnabled()
            raise RuntimeError("refresh failed")

    assert view.updatesEnabled()
    view.close()
    app.processEvents()


def test_form_helpers_expose_labels_and_predictable_focus_order() -> None:
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    layout = QVBoxLayout(host)
    first = QLineEdit()
    second = QLineEdit()
    third = QLineEdit()
    for field in (first, second, third):
        layout.addWidget(field)

    widgets_module.describe_field(first, "标题", "输入笔记标题")
    widgets_module.set_tab_order_chain(first, second, third)

    assert first.accessibleName() == "标题"
    assert first.accessibleDescription() == "输入笔记标题"
    assert first.nextInFocusChain() is second
    assert second.nextInFocusChain() is third
    host.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("variant", "accessible_name"),
    [
        ("loading", "正在加载"),
        ("success", "操作成功"),
        ("error", "操作失败"),
        ("disabled", "功能不可用"),
        ("permission", "需要配置或权限"),
    ],
)
def test_state_notice_exposes_semantic_state(
    variant: str,
    accessible_name: str,
) -> None:
    app = QApplication.instance() or QApplication([])
    notice = widgets_module.StateNotice("状态说明", variant)

    assert notice.property("state") == variant
    assert notice.text() == "状态说明"
    assert notice.accessibleName() == accessible_name
    assert notice.accessibleDescription() == "状态说明"
    notice.close()
    app.processEvents()


def test_explicit_theme_ignores_system_color_scheme() -> None:
    assert resolve_theme_mode("light", Qt.ColorScheme.Dark) == "light"
    assert resolve_theme_mode("dark", Qt.ColorScheme.Light) == "dark"


def test_system_theme_resolves_dark_and_defaults_unknown_to_light() -> None:
    assert resolve_theme_mode("system", Qt.ColorScheme.Dark) == "dark"
    assert resolve_theme_mode("system", Qt.ColorScheme.Unknown) == "light"


def test_unknown_theme_is_rejected() -> None:
    with pytest.raises(ValueError):
        normalize_theme_mode("sepia")


def test_stylesheet_covers_dark_tabs_inputs_and_cards() -> None:
    rendered = app_stylesheet("dark")

    assert DARK_THEME.bg in rendered
    assert DARK_THEME.card in rendered
    assert "QTabBar::tab:selected" in rendered
    assert "QPushButton#LibraryViewButton:checked" in rendered
    assert "QTreeWidget#KnowledgeTree::item:selected" in rendered
    assert "QComboBox QAbstractItemView" in rendered
    assert LIGHT_THEME.bg not in rendered


def test_check_mark_asset_is_loadable_as_local_path() -> None:
    # file:/// URI 在 Qt 下无法被 QPixmap 加载，导致选中框只剩蓝底；必须返回本地正斜杠路径。
    path = _check_mark_url()
    assert path
    assert not path.startswith("file://")
    assert QPixmap(path).isNull() is False


@pytest.mark.parametrize(
    ("mode", "tokens"),
    [("light", LIGHT_THEME), ("dark", DARK_THEME)],
)
def test_semantic_state_notices_use_each_theme_palette(mode, tokens) -> None:
    rendered = app_stylesheet(mode)

    assert 'QFrame#StateNotice[state="loading"]' in rendered
    assert 'QFrame#StateNotice[state="error"]' in rendered
    assert 'QFrame#StateNotice[state="disabled"]' in rendered
    assert 'QFrame#StateNotice[state="permission"]' in rendered
    assert tokens.danger_bg in rendered
    assert tokens.input_disabled in rendered
    assert tokens.fallback_bg in rendered


def test_soft_visual_tokens_and_library_surfaces_are_rendered() -> None:
    rendered = app_stylesheet("light")

    assert LIGHT_THEME.canvas == LIGHT_THEME.bg
    assert LIGHT_THEME.surface == LIGHT_THEME.card
    assert LIGHT_THEME.divider in rendered
    assert LIGHT_THEME.focus_ring in rendered
    assert f"border-radius: {UI_METRICS.radius_workspace}px" in rendered
    assert "QFrame#LibraryViewSwitch" in rendered
    assert "QFrame#LibraryNavigationPanel" in rendered
    assert "QFrame#LibraryListPanel" in rendered
    assert "QFrame#ReadingCanvas QScrollBar:horizontal" in rendered
    assert 'QLabel#WorkflowStep[state="current"]' in rendered
    assert "QFrame#IntakeConfirmationSurface" in rendered
    assert "QFrame#ReviewGradeSurface" in rendered
    assert 'QFrame#CardFrame[surfaceRole="settings"]' in rendered
    assert "QSplitter#DialogWorkspace" in rendered
    assert "QScrollArea#ImageViewerCanvas" in rendered
    assert "QListWidget#MainNav:focus" in rendered
    assert "QTreeWidget#KnowledgeTree::branch:selected" not in rendered
    assert "QListWidget#AnswerImageList" in rendered
    assert 'QMenu::item[danger="true"]' in rendered
    assert 'QFrame#StateNotice[state="loading"]' in rendered
    assert 'QFrame#StateNotice[state="error"]' in rendered
    assert 'QFrame#StateNotice[state="permission"]' in rendered


@pytest.mark.parametrize(("mode", "tokens"), [("light", LIGHT_THEME), ("dark", DARK_THEME)])
def test_dropdown_surfaces_share_spacious_floating_panel_states(mode, tokens) -> None:
    rendered = app_stylesheet(mode)

    assert "QMenu::item" in rendered
    assert "min-height: 40px" in rendered
    assert "padding: 0 16px" in rendered
    assert "margin: 8px 10px" in rendered
    assert "QMenu::item:selected" in rendered
    assert "QComboBox:on" in rendered
    assert "QComboBox:on::drop-down" in rendered
    assert "QComboBox QAbstractItemView::item:disabled" in rendered
    assert "padding: 0 14px" in rendered
    assert tokens.border_strong in rendered
    assert tokens.list_hover in rendered


def test_combo_popup_clips_native_shell_and_uses_shared_item_delegate() -> None:
    app = QApplication.instance() or QApplication([])
    original_stylesheet = app.styleSheet()
    original_palette = app.palette()
    manager = ThemeManager(app, "light")
    combo = QComboBox()
    combo.addItems(["正式笔记", "待整理", "归档"])
    combo.show()
    combo.showPopup()
    app.processEvents()

    popup = combo.view().window()
    assert popup.metaObject().className() == "QComboBoxPrivateContainer"
    assert not popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not popup.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
    assert popup.autoFillBackground()
    assert popup.frameShape() == QFrame.Shape.NoFrame
    assert not popup.mask().isEmpty()
    assert popup.mask().boundingRect() == combo.view().geometry()
    assert popup.mask().boundingRect() != popup.rect()
    assert isinstance(combo.view().itemDelegate(), widgets_module.SoftItemDelegate)
    assert combo.view().property("yancuoDropdownDelegate") is True
    assert popup.property("yancuoRoundedPopupShell") is True
    # 弹层列表视图移至触发框下方约 6px，不再覆盖触发框
    combo_bottom = combo.mapToGlobal(combo.rect().bottomLeft()).y()
    view_top = combo.view().mapToGlobal(combo.view().rect().topLeft()).y()
    assert view_top >= combo_bottom + 4

    combo.hidePopup()
    combo.close()
    app.removeEventFilter(manager)
    manager.deleteLater()
    app.setStyleSheet(original_stylesheet)
    app.setPalette(original_palette)
    app.processEvents()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_theme_mode_buttons_have_a_distinct_selected_state(mode: str) -> None:
    rendered = app_stylesheet(mode)

    assert 'QPushButton#ThemeModeButton[themeSelected="true"]' in rendered
    assert 'QPushButton#ThemeModeButton[themeSelected="false"]:hover' in rendered
    assert "QPushButton#ThemeModeButton:focus" in rendered
    assert "QPushButton#ThemeModeButton:disabled" in rendered
    assert "QLabel#ThemeModeStatus" in rendered


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_library_and_search_switches_share_all_interaction_states(mode: str) -> None:
    rendered = app_stylesheet(mode)

    assert "QPushButton#SearchModeButton, QPushButton#LibraryViewButton" in rendered
    assert "QPushButton#SearchModeButton:hover:!checked:!disabled" in rendered
    assert "QPushButton#SearchModeButton:checked, QPushButton#LibraryViewButton:checked" in rendered
    assert "QPushButton#SearchModeButton:focus, QPushButton#LibraryViewButton:focus" in rendered
    assert "QPushButton#SearchModeButton:disabled, QPushButton#LibraryViewButton:disabled" in rendered


def test_tree_branch_style_is_shared_and_keeps_branch_surface_transparent() -> None:
    app = QApplication.instance() or QApplication([])
    tree = QTreeWidget()
    widgets_module.apply_themed_tree_branches(tree)

    assert isinstance(tree.style(), widgets_module.ThemedTreeBranchStyle)
    assert "QTreeWidget#KnowledgeTree::branch:selected" not in app_stylesheet("light")
    tree.close()
    app.processEvents()



def test_combo_popup_mask_tracks_view_after_popup_resize() -> None:
    """
    下拉弹层变高后，圆角遮罩必须跟随内部列表的最终尺寸。

    QComboBoxPrivateContainer 在内部列表完成布局之前就会发出 Resize 事件，
    如果只根据容器事件计算遮罩，_fit_combo_popup 把弹层变高后遮罩仍停留在旧的
    （往往只有一半）高度，弹层下半部分会被裁掉；窗口最小化再恢复会触发重新布局，
    所以看起来像是“恢复后正常”。本用例确保遮罩在列表视图上报最终尺寸后被刷新。
    """
    app = QApplication.instance() or QApplication([])
    original_stylesheet = app.styleSheet()
    original_palette = app.palette()
    manager = ThemeManager(app, "light")
    combo = QComboBox()
    for index in range(30):
        combo.addItem(f"项目 {index + 1}")
    combo.show()
    combo.showPopup()
    app.processEvents()

    popup = combo.view().window()
    assert popup.metaObject().className() == "QComboBoxPrivateContainer"
    initial_height = popup.height()
    initial_mask = popup.mask().boundingRect()

    # 与 _fit_combo_popup 相同：把弹层加高，内部列表随后异步重新布局。
    popup.resize(popup.width(), initial_height + 240)
    app.processEvents()

    view = combo.view()
    assert view.height() > initial_mask.height()
    final_mask = popup.mask().boundingRect()
    assert final_mask.height() >= view.height() - 1
    assert final_mask.bottom() >= view.geometry().bottom() - 1

    combo.hidePopup()
    combo.close()
    app.removeEventFilter(manager)
    manager.deleteLater()
    app.setStyleSheet(original_stylesheet)
    app.setPalette(original_palette)
    app.processEvents()
