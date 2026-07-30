"""通用变更确认对话框：字段级差异、接受/拒绝。"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
)

from yancuo_win.application.ai_service import AIService
from yancuo_win.application.services import AppServices
from yancuo_win.domain.rules import DomainError
from yancuo_win.ui.widgets import (
    ConfirmDialog,
    PageHeader,
    SoftItemDelegate,
    ToastMessage,
    danger_button,
    default_button,
    primary_button,
    set_tab_order_chain,
)


class ReviewDialog(QDialog):
    def __init__(self, ai: AIService, app: AppServices, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ReviewDialog")
        self.ai = ai
        self.app = app
        self.setWindowTitle("待确认变更")
        self.resize(960, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(
            PageHeader("待确认变更", "逐项核对 AI 或外部导入建议，再决定接受、覆盖或保留本地内容。")
        )
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("DialogWorkspace")

        left_box = QFrame()
        left_box.setObjectName("DialogSidePane")
        left = QVBoxLayout(left_box)
        left_title = QLabel("待审核")
        left_title.setObjectName("SectionTitle")
        left.addWidget(left_title)
        self.list = QListWidget()
        self.list.setObjectName("DialogItemList")
        self.list.setAccessibleName("待确认变更列表")
        self.list.setAccessibleDescription("使用方向键选择一项，右侧会显示来源、字段差异和不确定项")
        self.list.setUniformItemSizes(True)
        self.list.setMouseTracking(True)
        self.list.setItemDelegate(
            SoftItemDelegate(self.list, minimum_height=40)
        )
        self.list.currentItemChanged.connect(self._on_select)
        left.addWidget(self.list)

        right_box = QFrame()
        right_box.setObjectName("DialogDetailPane")
        right = QVBoxLayout(right_box)
        meta_title = QLabel("题目来源与信息")
        meta_title.setObjectName("SectionTitle")
        right.addWidget(meta_title)
        self.meta = QLabel("")
        self.meta.setWordWrap(True)
        self.meta.setAccessibleName("变更来源与题目信息")
        right.addWidget(self.meta)
        diff_title = QLabel("字段差异")
        diff_title.setObjectName("SectionTitle")
        right.addWidget(diff_title)
        self.diff_view = QTextEdit()
        self.diff_view.setObjectName("DialogTextSurface")
        self.diff_view.setReadOnly(True)
        self.diff_view.setAccessibleName("字段差异")
        self.diff_view.setAccessibleDescription("只读内容，可使用方向键浏览并复制")
        right.addWidget(self.diff_view)
        uncertain_title = QLabel("不确定字段")
        uncertain_title.setObjectName("SectionTitle")
        right.addWidget(uncertain_title)
        self.uncertain = QTextEdit()
        self.uncertain.setObjectName("DialogTextSurface")
        self.uncertain.setReadOnly(True)
        self.uncertain.setAccessibleName("不确定字段")
        self.uncertain.setAccessibleDescription("只读内容，可使用方向键浏览并复制")
        self.uncertain.setMaximumHeight(120)
        right.addWidget(self.uncertain)

        splitter.addWidget(left_box)
        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        actions = QFrame()
        actions.setObjectName("DialogActionBar")
        row = QHBoxLayout(actions)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)
        self.accept_button = primary_button("接受变更")
        self.accept_button.clicked.connect(self._accept)
        self.force_button = danger_button("强制采用外部")
        self.force_button.clicked.connect(self._force_accept)
        self.reject_button = default_button("保留本地内容")
        self.reject_button.clicked.connect(self._reject)
        self.refresh_button = default_button("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        for btn in (
            self.accept_button,
            self.force_button,
            self.reject_button,
            self.refresh_button,
        ):
            row.addWidget(btn)
        row.addStretch(1)
        layout.addWidget(actions)

        tip = QLabel(
            "冲突项须用「强制采用外部」或「保留内部」。"
            "撤销请在题库选中题目后使用「撤销 AI 修改」（亦适用于工作区接受）。"
            "请勿直接修改 SQLite。"
        )
        tip.setObjectName("MutedLabel")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("关闭")
        close_button.clicked.connect(self.accept)
        layout.addWidget(buttons)
        self.toast = ToastMessage(self)
        self.close_button = close_button
        set_tab_order_chain(
            self.list,
            self.diff_view,
            self.uncertain,
            self.accept_button,
            self.force_button,
            self.reject_button,
            self.refresh_button,
            self.close_button,
        )
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for item in self.ai.list_open_review_items():
            proposed = json.loads(item.proposed_json)
            title = proposed.get("title") or item.problem_id[:16]
            mark = "⚠冲突" if item.status == "conflict" else "待审"
            row = QListWidgetItem(
                f"[{mark}] {title} · r{item.base_revision} · {item.id[:14]}"
            )
            row.setData(Qt.ItemDataRole.UserRole, item.id)
            self.list.addItem(row)
        if not self.list.count():
            empty = QListWidgetItem("暂无待确认变更")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(empty)
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
            if item and item.status == "conflict":
                QMessageBox.information(
                    self, "冲突", "请使用「强制采用外部」或「保留内部/拒绝」。"
                )
                return
            self.ai.accept_review_item(rid)
            if item:
                self.ai.assert_original_untouched(item.problem_id)
            self.toast.show_message("变更已写入题库，并生成版本记录")
            self.refresh()
        except DomainError as exc:
            QMessageBox.warning(self, "无法接受", str(exc))

    def _force_accept(self) -> None:
        rid = self._current_id()
        if not rid:
            return
        if not ConfirmDialog.ask(
            self,
            "确认强制采用外部版本",
            "将覆盖题库中的当前内容，并保留版本记录。",
            "强制采用",
        ):
            return
        try:
            item = self.ai.get_review_item(rid)
            self.ai.accept_review_item(rid, force=True)
            if item:
                self.ai.assert_original_untouched(item.problem_id)
            self.toast.show_message("外部版本已写入题库，并生成版本记录")
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
