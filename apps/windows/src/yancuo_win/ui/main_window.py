"""主窗口：列表、筛选、导入、导出、备份（阶段 B）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.services import AppServices, ProblemFilter
from yancuo_win.application.ai_service import AIService
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.worker import AIJobWorker
from yancuo_win.application.cloud_service import CloudBackupService
from yancuo_win.cloud.factory import get_cloud_provider
from yancuo_win.import_export.ebpack import EbpackService
from yancuo_win.import_export.workspace import WorkspaceService
from yancuo_win.ui.duplicate_dialog import DuplicateDialog
from yancuo_win.ui.problem_editor import ProblemEditorDialog
from yancuo_win.ui.review_dialog import ReviewDialog
from yancuo_win.ui.settings_dialog import SettingsDialog
from yancuo_win.ui.task_center import TaskCenterDialog
from yancuo_win.ui.today_review import TodayReviewDialog


class MainWindow(QMainWindow):
    def __init__(self, runtime: RuntimeContext) -> None:
        super().__init__()
        self.runtime = runtime
        self.services = AppServices(runtime)
        self.ai = AIService(runtime)
        self.workspace = WorkspaceService(runtime)
        self.ebpack = EbpackService(runtime)
        self.cloud = CloudBackupService(runtime)
        self._nav_mode = "library"  # library / inbox / active / trashed / subject:<id>
        self._selected_problem_id: str | None = None
        self._ai_worker: AIJobWorker | None = None

        self.setWindowTitle("研错库")
        self.resize(1280, 800)
        self._build_toolbar()
        self._build_central()
        self._build_status()
        self.refresh_all()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索题目 / 答案 / 备注 / 书名…")
        self.search_edit.setFixedWidth(260)
        self.search_edit.returnPressed.connect(self.refresh_problems)
        toolbar.addWidget(self.search_edit)

        actions = [
            ("新建题目", self._new_problem),
            ("导入图片", self._import_images),
            ("导入文件夹", self._import_folder),
            ("今日复习", self._today_review),
            ("加入复习", self._schedule_review),
            ("查重", self._find_duplicates),
            ("批量优先级", self._batch_priority),
            ("AI 识别", self._ai_recognize),
            ("AI 任务", self._open_task_center),
            ("AI 审核", self._open_review),
            ("撤销 AI", self._undo_ai),
            ("导出工作区", self._export_workspace),
            ("导入工作区", self._import_workspace),
            ("导出 Word", self._export_word),
            ("导出 ebpack", self._export_ebpack),
            ("导入 ebpack", self._import_ebpack),
            ("云备份", self._cloud_backup),
            ("云恢复", self._cloud_restore),
            ("备份(zip)", self._backup),
            ("恢复备份", self._restore_backup),
            ("设置", self._open_settings),
        ]
        for label, slot in actions:
            act = QAction(label, self)
            act.triggered.connect(slot)
            toolbar.addAction(act)

    def _build_central(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QFrame()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("导航"))
        self.nav_list = QListWidget()
        self.nav_list.currentItemChanged.connect(self._on_nav_changed)
        left_layout.addWidget(self.nav_list)
        from PySide6.QtWidgets import QPushButton

        btn_row = QHBoxLayout()
        b1 = QPushButton("新建科目")
        b1.clicked.connect(self._new_subject)
        b2 = QPushButton("新建标签")
        b2.clicked.connect(self._new_tag)
        btn_row.addWidget(b1)
        btn_row.addWidget(b2)
        left_layout.addLayout(btn_row)

        center = QFrame()
        center_layout = QVBoxLayout(center)
        center_layout.addWidget(QLabel("错题列表（双击编辑）"))
        self.problem_list = QListWidget()
        self.problem_list.itemSelectionChanged.connect(self._on_problem_selected)
        self.problem_list.itemDoubleClicked.connect(self._edit_selected)
        center_layout.addWidget(self.problem_list)

        action_row = QHBoxLayout()
        for text, slot in (
            ("编辑", self._edit_selected),
            ("入正式库", self._promote_selected),
            ("删除", self._trash_selected),
            ("恢复", self._restore_selected),
            ("清空回收站", self._purge_trash),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            action_row.addWidget(btn)
        center_layout.addLayout(action_row)

        right = QFrame()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("属性"))
        self.detail = QLabel("未选中题目")
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        right_layout.addWidget(self.detail, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(center)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 1)
        layout.addWidget(splitter)
        self.setCentralWidget(root)

    def _build_status(self) -> None:
        self.status = QStatusBar()
        self.setStatusBar(self.status)

    def refresh_all(self) -> None:
        self.refresh_nav()
        self.refresh_problems()
        self._update_status()

    def refresh_nav(self) -> None:
        current_mode = self._nav_mode
        self.nav_list.blockSignals(True)
        self.nav_list.clear()
        items = [
            ("全部（收件箱+正式）", "library"),
            ("收件箱", "inbox"),
            ("正式题库", "active"),
            ("今日复习", "due"),
            ("归档", "archived"),
            ("回收站", "trashed"),
        ]
        for label, mode in items:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, mode)
            self.nav_list.addItem(item)

        for sub in self.services.list_subjects():
            item = QListWidgetItem(f"科目 · {sub.name}")
            item.setData(Qt.ItemDataRole.UserRole, f"subject:{sub.id}")
            self.nav_list.addItem(item)

        # 恢复选中
        for i in range(self.nav_list.count()):
            it = self.nav_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == current_mode:
                self.nav_list.setCurrentRow(i)
                break
        else:
            self.nav_list.setCurrentRow(0)
            self._nav_mode = "library"
        self.nav_list.blockSignals(False)

    def _filter_from_nav(self) -> ProblemFilter:
        mode = self._nav_mode
        q = self.search_edit.text().strip() or None
        if mode.startswith("subject:"):
            return ProblemFilter(
                status="library", subject_id=mode.split(":", 1)[1], query=q
            )
        if mode == "library":
            return ProblemFilter(status="library", query=q)
        if mode == "due":
            return ProblemFilter(status="active", due_for_review=True, query=q)
        return ProblemFilter(status=mode, query=q)

    def refresh_problems(self) -> None:
        self.problem_list.clear()
        try:
            problems = self.services.list_problems(self._filter_from_nav())
        except DomainError as exc:
            QMessageBox.warning(self, "筛选失败", str(exc))
            return
        for p in problems:
            title = p.title or "(无标题)"
            tags = ",".join(t.name for t in (p.tags or []))
            text = f"[{p.status}] P{p.priority}  {title}"
            if tags:
                text += f"  #{tags}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            self.problem_list.addItem(item)
        self._update_status()

    def _update_status(self) -> None:
        total = self.services.count_problems()
        inbox = self.services.count_problems("inbox")
        active = self.services.count_problems("active")
        trash = self.services.count_problems("trashed")
        self.status.showMessage(
            f"共 {total} · 收件箱 {inbox} · 正式 {active} · 回收站 {trash} · "
            f"schema v{self.runtime.schema_version} · {self.runtime.paths.root}"
        )

    def _on_nav_changed(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if current is None:
            return
        self._nav_mode = current.data(Qt.ItemDataRole.UserRole) or "library"
        self.refresh_problems()

    def _on_problem_selected(self) -> None:
        items = self.problem_list.selectedItems()
        if not items:
            self._selected_problem_id = None
            self.detail.setText("未选中题目")
            return
        pid = items[0].data(Qt.ItemDataRole.UserRole)
        self._selected_problem_id = pid
        p = self.services.get_problem(pid)
        if not p:
            self.detail.setText("题目不存在")
            return
        assets = "\n".join(
            f"- {a.role}: {a.relative_path}{' (不可变)' if a.is_immutable else ''}"
            for a in (p.assets or [])
        ) or "（无）"
        tags = ", ".join(t.name for t in (p.tags or [])) or "（无）"
        self.detail.setText(
            f"ID: {p.id}\n"
            f"标题: {p.title or '（无）'}\n"
            f"状态: {p.status}\n"
            f"优先级: {p.priority}\n"
            f"修订: r{p.revision}\n"
            f"标签: {tags}\n"
            f"原题预览:\n{(p.question_markdown or '')[:300]}\n\n"
            f"附件:\n{assets}"
        )

    def _selected_ids(self) -> list[str]:
        return [
            it.data(Qt.ItemDataRole.UserRole)
            for it in self.problem_list.selectedItems()
        ]

    def _require_one(self) -> str | None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择一道题")
            return None
        return ids[0]

    def _new_problem(self) -> None:
        try:
            p = self.services.create_problem(title="新题目", status="inbox")
            self.refresh_problems()
            self._open_editor(p.id)
        except DomainError as exc:
            QMessageBox.warning(self, "创建失败", str(exc))

    def _edit_selected(self) -> None:
        pid = self._require_one()
        if pid:
            self._open_editor(pid)

    def _open_editor(self, problem_id: str) -> None:
        p = self.services.get_problem(problem_id)
        if not p:
            return
        dlg = ProblemEditorDialog(self.services, p, self)
        if dlg.exec():
            self.refresh_problems()
            self._on_problem_selected()

    def _promote_selected(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.services.promote_to_active(pid)
            self.refresh_problems()
        except DomainError as exc:
            QMessageBox.warning(self, "无法转入正式库", str(exc))

    def _trash_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择题目")
            return
        if self.runtime.settings.application.confirm_before_delete:
            if (
                QMessageBox.question(self, "确认", f"将 {len(ids)} 道题移入回收站？")
                != QMessageBox.StandardButton.Yes
            ):
                return
        try:
            for pid in ids:
                self.services.trash_problem(pid)
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _restore_selected(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.services.restore_problem(pid, "inbox")
            self.refresh_problems()
        except DomainError as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _purge_trash(self) -> None:
        if (
            QMessageBox.question(self, "确认", "清空回收站？此操作不可撤销。")
            != QMessageBox.StandardButton.Yes
        ):
            return
        n = self.services.purge_trashed()
        QMessageBox.information(self, "完成", f"已永久删除 {n} 道题")
        self.refresh_all()

    def _import_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        if not files:
            return
        try:
            result = self.services.import_images([Path(f) for f in files])
            tip = result.get("duplicate_tip") or ""
            QMessageBox.information(
                self,
                "导入完成",
                f"新建 {len(result['created'])} 题，跳过重复 {len(result['skipped'])} 个"
                + (f"\n{tip}" if tip else ""),
            )
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _import_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹")
        if not folder:
            return
        try:
            result = self.services.import_folder(Path(folder))
            QMessageBox.information(
                self,
                "导入完成",
                f"新建 {len(result['created'])} 题，跳过重复 {len(result['skipped'])} 个",
            )
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _export_word(self) -> None:
        ids = self._selected_ids()
        if not ids:
            # 导出当前列表全部
            ids = [
                self.problem_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.problem_list.count())
            ]
        if not ids:
            QMessageBox.information(self, "提示", "没有可导出的题目")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 Word",
            str(self.runtime.paths.export_dir / "错题导出.docx"),
            "Word (*.docx)",
        )
        if not path:
            return
        try:
            dest = self.services.export_problems_docx(ids, Path(path))
            QMessageBox.information(self, "导出完成", str(dest))
        except DomainError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _backup(self) -> None:
        try:
            dest = self.services.create_backup()
            QMessageBox.information(self, "备份完成", str(dest))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "备份失败", str(exc))

    def _export_ebpack(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出 ebpack",
            str(self.runtime.paths.backup_dir / "yancuo.ebpack"),
            "Yancuo Pack (*.ebpack)",
        )
        if not path:
            return
        try:
            dest = self.ebpack.export_ebpack(Path(path))
            QMessageBox.information(self, "导出完成", str(dest))
        except DomainError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _import_ebpack(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 ebpack",
            str(self.runtime.paths.backup_dir),
            "Yancuo Pack (*.ebpack)",
        )
        if not path:
            return
        target = QFileDialog.getExistingDirectory(
            self, "选择恢复到的数据目录（建议空目录）"
        )
        if not target:
            return
        try:
            result = self.ebpack.restore_ebpack(Path(path), Path(target))
            QMessageBox.information(
                self,
                "恢复完成",
                f"已恢复到：{result['target_root']}\n"
                f"schema v{result['schema_version']}\n"
                "请将 YANCUO_DATA_ROOT 指向该目录后重启。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _cloud_backup(self) -> None:
        try:
            # 按当前设置重建 provider（设置里可能已切换）
            self.cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            self.cloud.ensure_repository()
            result = self.cloud.upload_backup()
            QMessageBox.information(
                self,
                "云备份完成",
                f"tag={result['tag']}\nsha256={result['sha256'][:16]}…\n"
                "已先上传完整包，再更新 latest 指针（非实时同步）。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "云备份失败", str(exc))

    def _cloud_restore(self) -> None:
        target = QFileDialog.getExistingDirectory(
            self, "选择恢复到的数据目录（建议空目录）"
        )
        if not target:
            return
        try:
            self.cloud = CloudBackupService(
                self.runtime, get_cloud_provider(self.runtime.settings)
            )
            backups = self.cloud.list_backups()
            latest = next((b for b in backups if b.get("is_latest")), None)
            summary = "云端备份列表：\n" + "\n".join(
                f"- {b['tag']}{' (latest)' if b.get('is_latest') else ''}" for b in backups[:20]
            )
            if not backups:
                QMessageBox.information(self, "云恢复", "没有可恢复的备份")
                return
            if (
                QMessageBox.question(
                    self,
                    "确认恢复",
                    summary + "\n\n将下载 latest（若无则需手动指定）并恢复到所选目录。继续？",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            result = self.cloud.restore_latest_to(Path(target))
            QMessageBox.information(
                self,
                "云恢复完成",
                f"{result['target_root']}\n请设置 YANCUO_DATA_ROOT 后重启。\n"
                f"（当前 latest={latest['tag'] if latest else '未知'}）",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "云恢复失败", str(exc))

    def _restore_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择备份包", str(self.runtime.paths.backup_dir), "Zip (*.zip)"
        )
        if not path:
            return
        target = QFileDialog.getExistingDirectory(
            self, "选择恢复到的空数据目录（将写入库与资源）"
        )
        if not target:
            return
        try:
            root = self.services.restore_backup(Path(path), Path(target))
            QMessageBox.information(
                self,
                "恢复完成",
                f"已恢复到：{root}\n请将 YANCUO_DATA_ROOT 指向该目录后重启。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))

    def _open_settings(self) -> None:
        SettingsDialog(self.runtime, self).exec()

    def _ai_recognize(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择带原图的题目")
            return
        if self._ai_worker and self._ai_worker.isRunning():
            QMessageBox.information(self, "提示", "已有 AI 任务在后台运行")
            return
        try:
            job = self.ai.create_structure_job(ids)
            self._ai_worker = AIJobWorker(self.ai, job.id, self)
            self._ai_worker.finished_ok.connect(self._on_ai_job_done)
            self._ai_worker.failed.connect(self._on_ai_job_fail)
            self._ai_worker.start()
            self.status.showMessage(f"AI 任务已开始：{job.id}（不阻塞界面）")
        except DomainError as exc:
            QMessageBox.warning(self, "无法创建 AI 任务", str(exc))

    def _on_ai_job_done(self, job_id: str) -> None:
        QMessageBox.information(self, "AI 完成", f"任务 {job_id} 完成，请打开「AI 审核」。")
        self.refresh_all()

    def _on_ai_job_fail(self, job_id: str, err: str) -> None:
        QMessageBox.warning(self, "AI 失败", f"{job_id}\n{err}")

    def _open_task_center(self) -> None:
        TaskCenterDialog(self.ai, self).exec()
        self.refresh_all()

    def _open_review(self) -> None:
        ReviewDialog(self.ai, self.services, self).exec()
        self.refresh_all()

    def _undo_ai(self) -> None:
        pid = self._require_one()
        if not pid:
            return
        try:
            self.ai.undo_last_ai_accept(pid)
            QMessageBox.information(self, "已撤销", "已恢复到接受之前的内容。")
            self.refresh_problems()
            self._on_problem_selected()
        except DomainError as exc:
            QMessageBox.warning(self, "无法撤销", str(exc))

    def _export_workspace(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择要导出的题目")
            return
        try:
            dest = self.workspace.export_workspace(ids)
            QMessageBox.information(
                self,
                "导出完成",
                f"{dest}\n\n请只编辑工作区内的 Markdown/JSON，不要直接改数据库。",
            )
        except DomainError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _import_workspace(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择工作区目录（含 manifest.json）", str(self.runtime.paths.workspace_dir)
        )
        if not folder:
            return
        try:
            result = self.workspace.import_workspace(Path(folder))
            msg = (
                f"已生成审核项 {len(result['items'])} 个，"
                f"其中冲突 {len(result['conflicts'])} 个。\n"
                "请打开「AI 审核」查看差异（工作区与 AI 共用审核列表）。"
            )
            if result["errors"]:
                msg += "\n\n部分失败：\n" + "\n".join(result["errors"][:10])
            QMessageBox.information(self, "导入完成", msg)
            self._open_review()
        except DomainError as exc:
            QMessageBox.warning(self, "导入失败", str(exc))

    def _today_review(self) -> None:
        TodayReviewDialog(self.services, self).exec()
        self.refresh_all()

    def _schedule_review(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择题目")
            return
        try:
            for pid in ids:
                self.services.schedule_initial_review(pid)
            QMessageBox.information(self, "完成", f"已将 {len(ids)} 题加入今日复习")
            self.refresh_all()
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _find_duplicates(self) -> None:
        pid = self._selected_ids()[0] if self._selected_ids() else None
        DuplicateDialog(self.services, focus_problem_id=pid, parent=self).exec()

    def _batch_priority(self) -> None:
        ids = self._selected_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先选择题目")
            return
        value, ok = QInputDialog.getInt(self, "批量优先级", "优先级 1–5：", 3, 1, 5)
        if not ok:
            return
        try:
            n = self.services.batch_update_problems(ids, priority=value)
            QMessageBox.information(self, "完成", f"已更新 {n} 题")
            self.refresh_problems()
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _new_subject(self) -> None:
        name, ok = QInputDialog.getText(self, "新建科目", "科目名称：")
        if not ok or not name.strip():
            return
        try:
            self.services.create_subject(name.strip())
            self.refresh_nav()
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))

    def _new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "新建标签", "标签名称：")
        if not ok or not name.strip():
            return
        try:
            self.services.create_tag(name.strip())
            QMessageBox.information(self, "完成", f"已创建标签：{name.strip()}")
        except DomainError as exc:
            QMessageBox.warning(self, "失败", str(exc))
