from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

import yancuo_win.ui.problem_detail as problem_detail_module
from yancuo_win.data.models import Problem
from yancuo_win.ui.operation_results import (
    infer_transfer_operation,
    TransferOperation,
    TransferResultDialog,
)
from yancuo_win.ui.widgets import OperationResultDialog


class _ReaderStub(QWidget):
    def set_fit_content_height(self, *_args, **_kwargs) -> None:
        return None

    def set_accessible_content(self, name: str, description: str = "") -> None:
        self.setAccessibleName(name)
        self.setAccessibleDescription(description)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_problem(self, *_args, **_kwargs) -> None:
        return None

    def set_message(self, *_args, **_kwargs) -> None:
        return None


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_operation_result_exposes_summary_details_and_recovery_focus() -> None:
    app = _application()
    dialog = OperationResultDialog(
        "导入失败",
        "工作区未能导入。",
        details="manifest.json 缺失",
        is_error=True,
        retry_text="重新尝试",
    )
    dialog.show()
    app.processEvents()

    interface = QAccessible.queryAccessibleInterface(dialog)
    assert interface is not None
    assert interface.text(QAccessible.Text.Name) == "导入失败"
    assert dialog.summary_label.accessibleName() == "操作结果摘要"
    assert dialog.details_view.accessibleName() == "操作结果详情"
    assert dialog.details_view.isReadOnly()
    assert dialog.retry_button is not None
    assert dialog.retry_button.nextInFocusChain() is dialog.close_button

    dialog.retry_button.click()
    assert dialog.result() == OperationResultDialog.RetryCode


def test_transfer_result_copies_details_with_accessible_action() -> None:
    app = _application()
    dialog = TransferResultDialog(
        "云端恢复失败",
        "快照未能恢复。",
        details="网络连接已中断",
        retry_text="重新尝试",
        operation=TransferOperation.CLOUD,
    )
    dialog.show()
    app.processEvents()

    assert dialog.copy_button is not None
    assert dialog.accessibleName() == "云端操作结果：云端恢复失败"
    assert dialog.summary_label.accessibleName() == "云端操作结果摘要"
    assert dialog.details_view.accessibleName() == "云端操作结果详情"
    assert dialog.copy_button.accessibleName() == "复制云端操作结果详情"
    dialog.copy_button.click()
    assert QApplication.clipboard().text() == "网络连接已中断"
    dialog.details_view.setFocus()
    QTest.keyClick(dialog, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is dialog.copy_button
    dialog.close()


def test_transfer_operation_labels_cover_all_migrated_result_categories() -> None:
    assert [operation.label for operation in TransferOperation] == [
        "导入",
        "导出",
        "恢复",
        "云端",
        "同步",
    ]
    assert infer_transfer_operation("图片导入完成") is TransferOperation.IMPORT
    assert infer_transfer_operation("Word 导出失败") is TransferOperation.EXPORT
    assert infer_transfer_operation("完整备份包恢复完成") is TransferOperation.RESTORE
    assert infer_transfer_operation("云端资料合并完成") is TransferOperation.CLOUD
    assert infer_transfer_operation("拉取合并失败") is TransferOperation.SYNC


def test_transfer_result_restores_focus_to_its_invoker() -> None:
    app = _application()
    parent = QWidget()
    invoker = QPushButton("导入", parent)
    parent.show()
    invoker.setFocus()
    app.processEvents()
    dialog = TransferResultDialog("导入完成", "资料已导入。", parent=parent)

    dialog.done(0)
    app.processEvents()

    assert QApplication.focusWidget() is invoker
    dialog.close()
    parent.close()


def test_transfer_result_skips_a_disabled_invoker_when_restoring_focus() -> None:
    app = _application()
    parent = QWidget()
    disabled = QPushButton("正在导出", parent)
    fallback = QPushButton("其他操作", parent)
    fallback.move(0, 40)
    parent.show()
    disabled.setFocus()
    disabled.setEnabled(False)
    app.processEvents()
    dialog = TransferResultDialog("导出完成", "资料已导出。", parent=parent)

    dialog.done(0)
    app.processEvents()

    assert QApplication.focusWidget() is fallback
    dialog.close()
    parent.close()


def test_problem_detail_keyboard_path_and_accessible_names(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app = _application()
    monkeypatch.setattr(problem_detail_module, "MathContentView", _ReaderStub)
    page = problem_detail_module.ProblemDetailPage()
    image = tmp_path / "question.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\naccessible-detail")
    problem = Problem(
        id="problem_accessibility",
        title="可访问性题目",
        status="active",
        priority=3,
        review_count=0,
        tags=[],
    )
    page.set_problem(problem, image_path=image)
    page.show()
    app.processEvents()

    assert page.back_button.accessibleName() == "返回题库"
    assert "Alt+Left" in page.back_button.accessibleDescription()
    assert page.reader.accessibleName() == "题目正文与解析"
    assert page.chat_input.accessibleName() == "AI 讨论问题"
    assert page.back_button.nextInFocusChain() is page.view_image_button

    spy = QSignalSpy(page.back_requested)
    QTest.keyClick(page, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert spy.count() == 1
    page.close()
