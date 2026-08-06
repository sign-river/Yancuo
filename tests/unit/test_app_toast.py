"""AppToast / ToastStack behavior tests."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractAnimation
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from yancuo_win.ui.widgets import ToastStack


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_until(app: QApplication, predicate: Callable[[], bool], timeout_ms: int = 5000) -> None:
    """Poll the event loop until ``predicate`` holds.

    GitHub-hosted Windows runners are shared/virtualized and can drive
    QPropertyAnimation noticeably slower than wall-clock time, so a fixed
    ``QTest.qWait`` barely longer than the animation duration is not
    reliable.  Waiting for the actual end state (slide stopped / toast
    removed) keeps these assertions deterministic on any runner.
    """
    import time

    deadline = time.monotonic() + timeout_ms / 1000.0
    while not predicate():
        if time.monotonic() >= deadline:
            break
        QTest.qWait(10)
        app.processEvents()


def test_error_toast_shows_title_body_and_anchors_top_right() -> None:
    app = _app()
    host = QWidget()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    stack = ToastStack(host)
    stack.show_error("创建失败", "请输入复习计划名称")
    _wait_until(
        app,
        lambda: stack._toasts and stack._toasts[0]._slide.state() == QAbstractAnimation.State.Stopped,
    )
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
    _wait_until(app, lambda: len(stack._toasts) == 0)
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
    _wait_until(
        app,
        lambda: stack._toasts
        and all(t._slide.state() == QAbstractAnimation.State.Stopped for t in stack._toasts),
    )
    app.processEvents()

    assert len(stack._toasts) == 2
    first, second = stack._toasts
    assert second.y() >= first.y() + first.height() + 12
    assert first.x() == second.x()

    first.dismiss()
    _wait_until(app, lambda: len(stack._toasts) == 1)
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
    _wait_until(app, lambda: len(stack._toasts) == 0)
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
    assert toast.icon_label is not None  # success tone gets a green check icon
    assert toast.card.property("tone") == "success"
    assert toast.progress.property("tone") == "success"

    toast.dismiss()
    _wait_until(app, lambda: len(stack._toasts) == 0)
    host.close()


def test_show_message_auto_classifies_warning_tone() -> None:
    app = _app()
    host = QWidget()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    stack = ToastStack(host)
    stack.show_message("请先选择题目")
    app.processEvents()

    toast = stack._toasts[0]
    assert toast.card.property("tone") == "warning"
    assert toast.progress.property("tone") == "warning"
    assert toast.icon_label is not None

    toast.dismiss()
    _wait_until(app, lambda: len(stack._toasts) == 0)
    host.close()


def test_show_message_explicit_tone_wins() -> None:
    app = _app()
    host = QWidget()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    stack = ToastStack(host)
    stack.show_message("外观设置已保存并应用", tone="info")
    app.processEvents()

    toast = stack._toasts[0]
    assert toast.card.property("tone") == "info"
    assert toast.progress.property("tone") == "info"

    toast.dismiss()
    _wait_until(app, lambda: len(stack._toasts) == 0)
    host.close()
