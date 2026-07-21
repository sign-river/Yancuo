"""主窗口壳（阶段 A：布局框架，无业务功能）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.bootstrap import RuntimeContext


class MainWindow(QMainWindow):
    def __init__(self, runtime: RuntimeContext) -> None:
        super().__init__()
        self._runtime = runtime
        self.setWindowTitle("研错库")
        self.resize(1280, 800)

        self._build_toolbar()
        self._build_central()
        self._build_status()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for label in ("搜索", "AI 任务", "导入", "打印"):
            action = toolbar.addAction(label)
            action.setEnabled(False)
            action.setToolTip("阶段 B 及之后实现")

    def _build_central(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QFrame()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("科目 / 章节"))
        nav = QListWidget()
        nav.addItems(["全部题目", "收件箱", "智能列表（即将推出）", "标签（即将推出）"])
        left_layout.addWidget(nav)

        center = QFrame()
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(QLabel("错题列表"))
        empty = QLabel(
            "阶段 A：骨架已就绪。\n"
            "本地数据库与资源目录已初始化。\n"
            "请进入阶段 B 实现录入、整理与导出。"
        )
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        center_layout.addWidget(empty, stretch=1)

        right = QFrame()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("属性与标签"))
        right_layout.addWidget(
            QLabel("选中题目后将显示优先级、标签与复习状态。"),
            stretch=1,
        )

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 1)

        layout.addWidget(splitter)
        self.setCentralWidget(root)

    def _build_status(self) -> None:
        bar = QStatusBar()
        self.setStatusBar(bar)
        rt = self._runtime
        bar.showMessage(
            f"题目 0 · schema v{rt.schema_version} · "
            f"db={rt.identity.database_id[:16]}… · {rt.paths.root}"
        )
