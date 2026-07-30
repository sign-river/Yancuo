"""重复题提示（不自动删除）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QTextEdit,
    QVBoxLayout,
)

from yancuo_win.application.services import AppServices
from yancuo_win.ui.widgets import EmptyState, PageHeader


class DuplicateDialog(QDialog):
    def __init__(
        self,
        services: AppServices,
        *,
        focus_problem_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("DuplicateDialog")
        self.setWindowTitle("重复题检测（仅提示）")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        layout.addWidget(
            PageHeader("重复题检测", "疑似重复不会自动删除，请人工决定保留或合并。")
        )
        view = QTextEdit()
        view.setObjectName("DialogTextSurface")
        view.setReadOnly(True)
        lines: list[str] = []
        hash_groups = services.find_hash_duplicates()
        lines.append(f"## 原图哈希重复组：{len(hash_groups)}")
        for g in hash_groups:
            lines.append(f"- sha256={g['sha256'][:12]}… count={g['count']}")
            lines.append(f"  problems: {', '.join(g['problem_ids'])}")
        similar: list = []
        if focus_problem_id:
            similar = services.find_text_similar(focus_problem_id)
            lines.append("")
            lines.append(f"## 与当前题文本相似（≥0.85）：{len(similar)}")
            for item in similar:
                lines.append(
                    f"- {item['score']:.2f}  {item['title'] or '(无标题)'}  {item['problem_id']}"
                )
        if not hash_groups and not similar:
            layout.addWidget(
                EmptyState("未发现重复题", "当前范围内没有需要人工确认的重复提示。")
            )
        else:
            view.setPlainText("\n".join(lines))
            content = QFrame()
            content.setObjectName("DialogContentSurface")
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(12, 12, 12, 12)
            content_layout.addWidget(view)
            layout.addWidget(content, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        close_button.setText("关闭")
        close_button.clicked.connect(self.accept)
        layout.addWidget(buttons)
