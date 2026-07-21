"""重复题提示（不自动删除）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextEdit,
    QVBoxLayout,
)

from yancuo_win.application.services import AppServices


class DuplicateDialog(QDialog):
    def __init__(
        self,
        services: AppServices,
        *,
        focus_problem_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("重复题检测（仅提示）")
        self.resize(640, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("疑似重复不会自动删除，请人工决定保留/合并。"))
        view = QTextEdit()
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
            lines.append("\n未发现重复提示。")
        view.setPlainText("\n".join(lines))
        layout.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)
