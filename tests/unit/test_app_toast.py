"""AppToast / ToastStack behavior tests."""

from __future__ import annotations

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from yancuo_win.ui.widgets import ToastStack


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_error_toast_shows_title_body_and_anchors_top_right() -> None:
    app = _app()
    host = QWidget()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    stack = ToastStack(host)
    stack.show_error("创建失败", "请输入复习计划名称")
    QTest.qWait(320)
    app.processEvents()

    assert len(stack._toasts) == 1
    toast = stack._toasts[0]
    assert toast.title_label.text() == "创建失败"
    assert toast.body_label.text() == "请输入复习计划名称"
    assert toast.icon_label is not None  # red error icon present
    # 340px wide, 24px from right, 80px from top
    assert toast.width() == 340
    assert toast.x() == host.width() - 340 - 24
    assert toast.y() == 80
    assert 0 < toast.progress.value() <= toast.progress.maximum()

    toast.dismiss()
    QTest.qWait(320)
    app.processEvents()
    assert len(stack._toasts) == 0
    host.close()


def test_toasts_stack_with_gap_without_overlap() -> None:
    app = _app()
    host = QWidget()
    host.resize(900, 700)
    host.show()
    app.processEvents()
    stack = ToastStack(host)
    stack.show_error("第一条", "内容一")
    stack.show_error("第二条", "内容二")
    app.processEvents()

    assert len(stack._toasts) == 2
    first, second = stack._toasts
    assert second.y() >= first.y() + first.height() + 12
    assert first.x() == second.x()

    first.dismiss()
    QTest.qWait(320)
    app.processEvents()
    assert len(stack._toasts) == 1
    assert stack._toasts[0].y() == 80
    host.close()


def test_countdown_progress_pauses_when_paused() -> None:
    app = _app()
    host = QWidget()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    stack = ToastStack(host)
    stack.show_error("创建失败", "正文", duration_ms=1000)
    app.processEvents()
    toast = stack._toasts[0]

    initial = toast.progress.value()
    toast._advance()
    assert toast.progress.value() < initial

    paused_value = toast.progress.value()
    toast._paused = True
    toast._advance()
    assert toast.progress.value() == paused_value

    toast._paused = False
    toast._advance()
    assert toast.progress.value() < paused_value

    toast.dismiss()
    QTest.qWait(320)
    app.processEvents()
    host.close()


def test_generic_show_message_keeps_compatibility() -> None:
    app = _app()
    host = QWidget()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    stack = ToastStack(host)
    stack.show_message("云端连接测试成功")
    app.processEvents()

    assert len(stack._toasts) == 1
    toast = stack._toasts[0]
    assert toast.body_label.text() == "云端连接测试成功"
    assert toast.icon_label is None  # no red icon for generic notices

    toast.dismiss()
    QTest.qWait(320)
    app.processEvents()
    host.close()
