"""Specialized, accessible result windows for import, export and cloud actions."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QPushButton, QWidget

from yancuo_win.ui.widgets import OperationResultDialog, set_tab_order_chain


class TransferOperation(StrEnum):
    """User-facing operation categories handled by the transfer result surface."""

    IMPORT = "import"
    EXPORT = "export"
    RESTORE = "restore"
    CLOUD = "cloud"
    SYNC = "sync"

    @property
    def label(self) -> str:
        return {
            self.IMPORT: "导入",
            self.EXPORT: "导出",
            self.RESTORE: "恢复",
            self.CLOUD: "云端",
            self.SYNC: "同步",
        }[self]


class TransferResultDialog(OperationResultDialog):
    """Operation result with an explicit detail-copy action for transfer workflows."""

    def __init__(
        self,
        title: str,
        summary: str,
        *,
        details: str = "",
        is_error: bool = False,
        retry_text: str = "",
        operation: TransferOperation = TransferOperation.IMPORT,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title,
            summary,
            details=details,
            is_error=is_error,
            retry_text=retry_text,
            parent=parent,
        )
        self.operation = operation
        self._previous_focus = parent.focusWidget() if parent is not None else None
        self.setObjectName(f"{operation.label}结果窗口")
        self.setAccessibleName(f"{operation.label}操作结果：{title}")
        self.summary_label.setAccessibleName(f"{operation.label}操作结果摘要")
        self.details_view.setAccessibleName(f"{operation.label}操作结果详情")
        self.finished.connect(self._restore_previous_focus)
        self.copy_button: QPushButton | None = None
        if not details:
            return
        self.copy_button = QPushButton("复制详情")
        self.copy_button.setAccessibleName(f"复制{operation.label}操作结果详情")
        self.copy_button.setAccessibleDescription(
            f"将完整{operation.label}操作结果详情复制到剪贴板"
        )
        self.copy_button.setToolTip("复制详情")
        self.copy_button.clicked.connect(self._copy_details)
        layout = self.layout()
        assert layout is not None
        layout.insertWidget(layout.count() - 1, self.copy_button)
        if self.retry_button is not None:
            set_tab_order_chain(
                self.details_view, self.copy_button, self.retry_button, self.close_button
            )
        else:
            set_tab_order_chain(self.details_view, self.copy_button, self.close_button)

    def _copy_details(self) -> None:
        QGuiApplication.clipboard().setText(self.details_view.toPlainText())
        self.copy_button.setText("已复制详情")

    def _restore_previous_focus(self, _result: int) -> None:
        if self._previous_focus is not None and self._previous_focus.isVisible():
            QTimer.singleShot(0, self._previous_focus.setFocus)


def show_transfer_result(
    parent: QWidget,
    title: str,
    summary: str,
    *,
    operation: TransferOperation,
    details: str = "",
    is_error: bool = False,
    retry: Callable[[], None] | None = None,
) -> int:
    """Show a transfer result and defer an optional retry until the dialog closes."""

    dialog = TransferResultDialog(
        title,
        summary,
        details=details,
        is_error=is_error,
        retry_text="重新尝试" if retry is not None else "",
        operation=operation,
        parent=parent,
    )
    result = dialog.exec()
    if result == OperationResultDialog.RetryCode and retry is not None:
        QTimer.singleShot(0, retry)
    return result
