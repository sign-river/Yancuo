"""Show real Yancuo surfaces for a bounded Windows UI Automation probe."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SOURCE = REPOSITORY_ROOT / "apps" / "windows" / "src"


def _configure_imports() -> None:
    source = str(WINDOWS_SOURCE)
    if source not in sys.path:
        sys.path.insert(0, source)


def build_surface(name: str):
    _configure_imports()
    if name == "detail":
        from yancuo_win.data.models import Problem
        from yancuo_win.ui.problem_detail import ProblemDetailPage

        surface = ProblemDetailPage()
        surface.setWindowTitle("题目详情可访问性抽查")
        surface.set_problem(
            Problem(
                id="problem_accessibility_probe",
                title="可访问性抽查题目",
                status="active",
                priority=3,
                review_count=0,
                question_markdown="求函数 $f(x)=x^2$ 的导数。",
                correct_answer="$2x$",
                solution_markdown="使用幂函数求导公式。",
                tags=[],
            ),
            subject_name="高等数学",
            chapter_name="导数",
        )
        surface.resize(960, 720)
        return surface

    from yancuo_win.ui.widgets import OperationResultDialog

    return OperationResultDialog(
        "导入结果可访问性抽查",
        "已生成 2 个待审核变更，其中 1 个冲突。",
        details=(
            "工作区：C:\\isolated-accessibility-probe\n"
            "下一步：打开“待确认变更”检查冲突。"
        ),
        retry_text="重新尝试",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", choices=("detail", "result"), required=True)
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    application = QApplication.instance() or QApplication([])
    surface = build_surface(args.surface)
    surface.show()
    QTimer.singleShot(max(1, args.seconds) * 1000, application.quit)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
