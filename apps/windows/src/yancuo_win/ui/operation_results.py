"""Specialized, accessible result windows for import, export and cloud actions."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QPushButton, QWidget

from yancuo_win.ui.widgets import OperationResultDialog, set_tab_order_chain


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
        self.copy_button: QPushButton | None = None
        if not details:
            return
        self.copy_button = QPushButton("复制详情")
        self.copy_button.setAccessibleName("复制操作结果详情")
        self.copy_button.setAccessibleDescription("将完整操作结果详情复制到剪贴板")
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
