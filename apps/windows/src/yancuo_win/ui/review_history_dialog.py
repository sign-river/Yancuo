"""Dedicated review-plan history dialog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.ui.widgets import PageHeader, SoftItemDelegate, set_tab_order_chain


@dataclass(frozen=True)
class ReviewHistoryEntry:
    occurred_at: datetime
    title: str
    details: str


class ReviewHistoryDialog(QDialog):
    """Browse and copy review history without changing any study records."""

    def __init__(
        self,
        plan_name: str,
        entries: list[ReviewHistoryEntry],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ReviewHistoryDialog")
        self.setWindowTitle("复习历史")
        self.setAccessibleName(f"{plan_name}的复习历史")
        self.setAccessibleDescription("选择一条历史记录后，可在详情区域阅读或复制。")
        self.resize(720, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(PageHeader("复习历史", f"{plan_name} · 共 {len(entries)} 条记录"))
        self.history_list = QListWidget()
        self.history_list.setObjectName("ReviewHistoryList")
        self.history_list.setAccessibleName("复习历史记录")
        self.history_list.setAccessibleDescription("使用方向键选择记录，详情会显示在下方。")
        self.history_list.setUniformItemSizes(True)
        self.history_list.setItemDelegate(SoftItemDelegate(self.history_list, minimum_height=40))
        self.history_list.currentItemChanged.connect(self._show_entry)
        layout.addWidget(self.history_list, stretch=1)
        self.details_view = QTextEdit()
        self.details_view.setObjectName("DialogTextSurface")
        self.details_view.setReadOnly(True)
        self.details_view.setAccessibleName("复习历史详情")
        self.details_view.setAccessibleDescription("只读详情，可使用方向键浏览并复制")
        self.details_view.setMaximumHeight(130)
        layout.addWidget(self.details_view)
        self.copy_button = QPushButton("复制详情")
        self.copy_button.setAccessibleName("复制复习历史详情")
        self.copy_button.clicked.connect(self._copy_details)
        layout.addWidget(self.copy_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setText("关闭")
        self.close_button.setAccessibleName("关闭复习历史")
        self.close_button.clicked.connect(self.accept)
        layout.addWidget(buttons)
        set_tab_order_chain(
            self.history_list, self.details_view, self.copy_button, self.close_button
        )
        self._populate(entries)

    def _populate(self, entries: list[ReviewHistoryEntry]) -> None:
        if not entries:
            empty = QListWidgetItem("暂无复习历史")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.history_list.addItem(empty)
            self.copy_button.setEnabled(False)
            self.details_view.setPlainText("当前计划尚未产生复习记录。")
            return
        for entry in entries:
            timestamp = entry.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M")
            item = QListWidgetItem(f"{timestamp} · {entry.title}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.history_list.addItem(item)
        self.history_list.setCurrentRow(0)

    def _show_entry(self, item: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(entry, ReviewHistoryEntry):
            self.details_view.setPlainText(entry.details)

    def _copy_details(self) -> None:
        QGuiApplication.clipboard().setText(self.details_view.toPlainText())
        self.copy_button.setText("已复制详情")
