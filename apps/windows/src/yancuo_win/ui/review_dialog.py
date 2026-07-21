"""AI 审核对话框：字段级差异、接受/拒绝。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.ai_service import AIService
from yancuo_win.application.services import AppServices
from yancuo_win.domain.rules import DomainError


class ReviewDialog(QDialog):
    def __init__(self, ai: AIService, app: AppServices, parent=None) -> None:
        super().__init__(parent)
        self.ai = ai
        self.app = app
        self.setWindowTitle("AI 审核")
        self.resize(960, 640)

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_box = QWidget()
        left = QVBoxLayout(left_box)
        left.addWidget(QLabel("待审核"))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list)

        right_box = QWidget()
        right = QVBoxLayout(right_box)
        right.addWidget(QLabel("原图路径 / 题目信息"))
        self.meta = QLabel("")
        self.meta.setWordWrap(True)
        right.addWidget(self.meta)
        right.addWidget(QLabel("字段差异（before → after）"))
        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        right.addWidget(self.diff_view)
        right.addWidget(QLabel("不确定字段"))
        self.uncertain = QTextEdit()
        self.uncertain.setReadOnly(True)
        self.uncertain.setMaximumHeight(120)
        right.addWidget(self.uncertain)

        splitter.addWidget(left_box)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        row = QHBoxLayout()
        for text, slot in (
            ("接受", self._accept),
            ("拒绝", self._reject),
            ("刷新", self.refresh),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        layout.addLayout(row)

        tip = QLabel("撤销请在主窗口选中题目后使用「撤销 AI」。拒绝不会写入正式字段。")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for item in self.ai.list_open_review_items():
            proposed = json.loads(item.proposed_json)
            title = proposed.get("title") or item.problem_id[:16]
            row = QListWidgetItem(f"{title} · r{item.base_revision} · {item.id[:14]}")
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            self.list.addItem(row)
        self.diff_view.clear()
        self.uncertain.clear()
        self.meta.setText("选择左侧条目")

    def _current_id(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _on_select(self, current: QListWidgetItem | None, _prev) -> None:
        if not current:
            return
        rid = current.data(Qt.ItemDataRole.UserRole)
        item = self.ai.get_review_item(rid)
        if not item:
            return
        problem = self.app.get_problem(item.problem_id)
        assets = ""
        if problem and problem.assets:
            assets = "\n".join(
                f"{a.role}: {a.relative_path} immutable={a.is_immutable}"
                for a in problem.assets
            )
        self.meta.setText(
            f"problem={item.problem_id}\n"
            f"status={problem.status if problem else '?'}\n"
            f"{assets}"
        )
        diffs = self.ai.review_diffs(rid)
        lines = [
            f"## {d['field']}\n- before: {d['before']!r}\n- after:  {d['after']!r}\n"
            for d in diffs
        ]
        self.diff_view.setPlainText("\n".join(lines) or "（无字段变化）")
        self.uncertain.setPlainText(
            json.dumps(json.loads(item.uncertain_json), ensure_ascii=False, indent=2)
        )

    def _accept(self) -> None:
        rid = self._current_id()
        if not rid:
            return
        try:
            item = self.ai.get_review_item(rid)
            self.ai.accept_review_item(rid)
            if item:
                self.ai.assert_original_untouched(item.problem_id)
            QMessageBox.information(self, "已接受", "已写入题库并生成版本记录。")
            self.refresh()
        except DomainError as exc:
            QMessageBox.warning(self, "无法接受", str(exc))

    def _reject(self) -> None:
        rid = self._current_id()
        if not rid:
            return
        try:
            self.ai.reject_review_item(rid)
            self.refresh()
        except DomainError as exc:
            QMessageBox.warning(self, "无法拒绝", str(exc))
