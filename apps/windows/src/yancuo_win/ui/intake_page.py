"""Task-oriented problem intake page.

Manual entry and AI-assisted entry live in one persistent page stack.  The
user never needs to navigate through the library, task center, and review
dialog to finish recording a new problem.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yancuo_win.application.intake_service import (
    IntakeCandidate,
    ProblemIntakeService,
    RegionRecognitionProposal,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.tasks.worker import AIJobWorker, RegionRecognitionWorker
from yancuo_win.ui.math_content import MathContentView
from yancuo_win.ui.widgets import (
    CardFrame,
    PageHeader,
    danger_button,
    ghost_button,
    primary_button,
)


_PAGE_MANUAL = 0
_PAGE_AI_UPLOAD = 1
_PAGE_AI_PROCESSING = 2
_PAGE_AI_CONFIRM = 3
_PAGE_DONE = 4


class ImagePreviewLabel(QLabel):
    """Aspect-ratio-preserving preview that follows the available panel size."""

    region_drawn = Signal(dict)

    def __init__(self, empty_text: str, parent=None) -> None:
        super().__init__(empty_text, parent)
        self.empty_text = empty_text
        self.source = QPixmap()
        self.region: dict[str, float] = {}
        self.editable = False
        self._drag_start: QPointF | None = None
        self._drag_mode: str | None = None
        self._region_before_drag: dict[str, float] = {}
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(QSize(360, 260))
        self.setObjectName("ImagePreview")

    def set_path(self, path: Path | None) -> bool:
        self.source = QPixmap(str(path)) if path is not None else QPixmap()
        self._render()
        return not self.source.isNull()

    def set_region(self, region: dict[str, float] | None) -> None:
        self.region = dict(region or {})
        self.update()

    def set_editable(self, editable: bool) -> None:
        self.editable = editable
        self.setCursor(
            Qt.CursorShape.CrossCursor
            if editable
            else Qt.CursorShape.ArrowCursor
        )
        self.setToolTip(
            "拖拽空白处重画区域；拖动蓝框内部可移动；拖动边框控制柄可微调"
            if editable
            else ""
        )

    def clear_preview(self) -> None:
        self.source = QPixmap()
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._render()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        super().paintEvent(event)
        if self.source.isNull() or not self.region:
            return
        displayed = self.pixmap()
        if displayed.isNull():
            return
        left = (self.width() - displayed.width()) / 2
        top = (self.height() - displayed.height()) / 2
        region = QRectF(
            left + displayed.width() * self.region.get("x", 0.0),
            top + displayed.height() * self.region.get("y", 0.0),
            displayed.width() * self.region.get("width", 1.0),
            displayed.height() * self.region.get("height", 1.0),
        )
        image_rect = QRectF(left, top, displayed.width(), displayed.height())
        painter = QPainter(self)
        shade = QColor(15, 23, 42, 105)
        painter.fillRect(
            QRectF(image_rect.left(), image_rect.top(), image_rect.width(), region.top() - image_rect.top()),
            shade,
        )
        painter.fillRect(
            QRectF(image_rect.left(), region.bottom(), image_rect.width(), image_rect.bottom() - region.bottom()),
            shade,
        )
        painter.fillRect(
            QRectF(image_rect.left(), region.top(), region.left() - image_rect.left(), region.height()),
            shade,
        )
        painter.fillRect(
            QRectF(region.right(), region.top(), image_rect.right() - region.right(), region.height()),
            shade,
        )
        painter.setPen(QPen(QColor("#3478F6"), 3))
        painter.drawRect(region)
        if self.editable:
            painter.setPen(QPen(QColor("white"), 1))
            painter.setBrush(QColor("#3478F6"))
            for point in self._handle_points(region).values():
                painter.drawRect(
                    QRectF(point.x() - 4, point.y() - 4, 8, 8)
                )

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if (
            not self.editable
            or event.button() != Qt.MouseButton.LeftButton
            or not self._displayed_rect().contains(event.position())
        ):
            super().mousePressEvent(event)
            return
        self._drag_start = event.position()
        self._region_before_drag = dict(self.region)
        self._drag_mode = (
            "draw"
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier
            else self._hit_test(event.position())
        )
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drag_start is None:
            if self.editable:
                self._update_hover_cursor(event.position())
            super().mouseMoveEvent(event)
            return
        if self._drag_mode == "draw":
            self.region = self._normalized_drag_region(
                self._drag_start, event.position()
            )
        else:
            self.region = self._transformed_region(event.position())
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drag_start is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        displayed = self._displayed_rect()
        region = dict(self.region)
        width_px = region.get("width", 0.0) * displayed.width()
        height_px = region.get("height", 0.0) * displayed.height()
        self._drag_start = None
        self._drag_mode = None
        if width_px < 8 or height_px < 8:
            self.region = self._region_before_drag
            self.update()
            event.accept()
            return
        self.region = region
        self.update()
        if region != self._region_before_drag:
            self.region_drawn.emit(dict(region))
        self._update_hover_cursor(event.position())
        event.accept()

    def _displayed_rect(self) -> QRectF:
        displayed = self.pixmap()
        if displayed.isNull():
            return QRectF()
        return QRectF(
            (self.width() - displayed.width()) / 2,
            (self.height() - displayed.height()) / 2,
            displayed.width(),
            displayed.height(),
        )

    def _normalized_drag_region(
        self, start: QPointF, end: QPointF
    ) -> dict[str, float]:
        displayed = self._displayed_rect()
        if displayed.isEmpty():
            return {}
        x1 = min(displayed.right(), max(displayed.left(), start.x()))
        y1 = min(displayed.bottom(), max(displayed.top(), start.y()))
        x2 = min(displayed.right(), max(displayed.left(), end.x()))
        y2 = min(displayed.bottom(), max(displayed.top(), end.y()))
        return {
            "x": (min(x1, x2) - displayed.left()) / displayed.width(),
            "y": (min(y1, y2) - displayed.top()) / displayed.height(),
            "width": abs(x2 - x1) / displayed.width(),
            "height": abs(y2 - y1) / displayed.height(),
        }

    def _region_rect(self) -> QRectF:
        displayed = self._displayed_rect()
        if displayed.isEmpty() or not self.region:
            return QRectF()
        return QRectF(
            displayed.left() + displayed.width() * self.region.get("x", 0.0),
            displayed.top() + displayed.height() * self.region.get("y", 0.0),
            displayed.width() * self.region.get("width", 1.0),
            displayed.height() * self.region.get("height", 1.0),
        )

    @staticmethod
    def _handle_points(rect: QRectF) -> dict[str, QPointF]:
        return {
            "nw": rect.topLeft(),
            "n": QPointF(rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }

    def _hit_test(self, position: QPointF) -> str:
        rect = self._region_rect()
        if rect.isEmpty():
            return "draw"
        for mode, point in self._handle_points(rect).items():
            if abs(position.x() - point.x()) <= 8 and abs(
                position.y() - point.y()
            ) <= 8:
                return mode
        if rect.contains(position):
            return "move"
        return "draw"

    def _update_hover_cursor(self, position: QPointF) -> None:
        cursors = {
            "nw": Qt.CursorShape.SizeFDiagCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor,
            "sw": Qt.CursorShape.SizeBDiagCursor,
            "n": Qt.CursorShape.SizeVerCursor,
            "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor,
            "w": Qt.CursorShape.SizeHorCursor,
            "move": Qt.CursorShape.SizeAllCursor,
            "draw": Qt.CursorShape.CrossCursor,
        }
        self.setCursor(cursors[self._hit_test(position)])

    def _transformed_region(self, position: QPointF) -> dict[str, float]:
        displayed = self._displayed_rect()
        if (
            displayed.isEmpty()
            or self._drag_start is None
            or not self._region_before_drag
        ):
            return self._normalized_drag_region(
                self._drag_start or position, position
            )
        mode = self._drag_mode or "move"
        initial = self._region_before_drag
        left = initial["x"]
        top = initial["y"]
        right = left + initial["width"]
        bottom = top + initial["height"]
        dx = (position.x() - self._drag_start.x()) / displayed.width()
        dy = (position.y() - self._drag_start.y()) / displayed.height()
        min_width = 8 / displayed.width()
        min_height = 8 / displayed.height()
        if mode == "move":
            width = initial["width"]
            height = initial["height"]
            return {
                "x": min(1.0 - width, max(0.0, left + dx)),
                "y": min(1.0 - height, max(0.0, top + dy)),
                "width": width,
                "height": height,
            }
        pointer_x = min(
            1.0,
            max(
                0.0,
                (position.x() - displayed.left()) / displayed.width(),
            ),
        )
        pointer_y = min(
            1.0,
            max(
                0.0,
                (position.y() - displayed.top()) / displayed.height(),
            ),
        )
        if "w" in mode:
            left = min(right - min_width, pointer_x)
        if "e" in mode:
            right = max(left + min_width, pointer_x)
        if "n" in mode:
            top = min(bottom - min_height, pointer_y)
        if "s" in mode:
            bottom = max(top + min_height, pointer_y)
        return {
            "x": left,
            "y": top,
            "width": right - left,
            "height": bottom - top,
        }

    def _render(self) -> None:
        if self.source.isNull():
            self.setPixmap(QPixmap())
            self.setText(self.empty_text)
            return
        target = self.size() - QSize(24, 24)
        self.setText("")
        self.setPixmap(
            self.source.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class _RegionRecognitionCompareDialog(QDialog):
    def __init__(
        self,
        proposal: RegionRecognitionProposal,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.apply_new = False
        self.setWindowTitle("比较区域重新识别结果")
        self.resize(1320, 760)
        root = QVBoxLayout(self)
        title = QLabel("区域重新识别完成")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        hint = QLabel(
            "左侧保留重新识别前的内容，右侧是当前蓝框生成的新结果。"
            "只有点击“采用新结果”才会覆盖候选字段。"
        )
        hint.setObjectName("PageHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        comparison = QHBoxLayout()
        old_card = CardFrame()
        old_card.add_title("原结果")
        old_view = MathContentView()
        old_view.set_problem(
            proposal.old_fields,
            tag_names=proposal.old_fields.get("tags", []),
            include_answers=True,
        )
        old_card.body.addWidget(old_view)
        comparison.addWidget(old_card, stretch=1)

        new_card = CardFrame()
        new_card.add_title("区域重新识别结果")
        if proposal.uncertain:
            new_card.add_hint(
                f"AI 报告 {len(proposal.uncertain)} 项不确定内容，请重点核对。"
            )
        new_view = MathContentView()
        new_view.set_problem(
            proposal.new_fields,
            tag_names=proposal.new_fields.get("tags", []),
            include_answers=True,
        )
        new_card.body.addWidget(new_view)
        comparison.addWidget(new_card, stretch=1)
        root.addLayout(comparison, stretch=1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        keep_old = QPushButton("保留原结果")
        keep_old.clicked.connect(self.reject)
        adopt = primary_button("采用新结果")
        adopt.clicked.connect(self._adopt)
        actions.addWidget(keep_old)
        actions.addWidget(adopt)
        root.addLayout(actions)

    def _adopt(self) -> None:
        self.apply_new = True
        self.accept()


class ProblemForm(QWidget):
    """Reusable inline form shared by manual entry and AI confirmation."""

    changed = Signal()

    def __init__(
        self,
        intake: ProblemIntakeService,
        parent=None,
        *,
        clear_user_answer_on_load: bool = False,
    ) -> None:
        super().__init__(parent)
        self.intake = intake
        self.clear_user_answer_on_load = clear_user_answer_on_load
        self._text_area_resize_timer = QTimer(self)
        self._text_area_resize_timer.setSingleShot(True)
        self._text_area_resize_timer.timeout.connect(
            self._resize_text_areas_to_content
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        basic = CardFrame()
        basic.add_title("基本归属")
        form = QFormLayout()
        form.setSpacing(10)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：换元积分中遗漏绝对值")
        self.subject = QComboBox()
        self.chapter = QComboBox()
        self.problem_type = QLineEdit()
        self.problem_type.setPlaceholderText("例如：选择题 / 计算题")
        self.priority = QSpinBox()
        self.priority.setRange(1, 5)
        self.priority.setValue(3)
        self.source_book = QLineEdit()
        self.source_year = QLineEdit()
        self.original_number = QLineEdit()
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("多个标签用逗号分隔")
        form.addRow("标题", self.title_edit)
        form.addRow("科目", self.subject)
        form.addRow("章节", self.chapter)
        form.addRow("题型", self.problem_type)
        form.addRow("优先级", self.priority)
        form.addRow("来源书籍", self.source_book)
        form.addRow("来源年份", self.source_year)
        form.addRow("原题题号", self.original_number)
        form.addRow("标签", self.tags)
        basic.body.addLayout(form)
        root.addWidget(basic)

        content = CardFrame()
        content.add_title("题目内容")
        self.question = self._text_area("题干 Markdown / 文本", 150)
        self.latex = self._text_area("公式 LaTeX（可留空）", 78)
        content.body.addWidget(QLabel("题干"))
        content.body.addWidget(self.question)
        content.body.addWidget(QLabel("LaTeX"))
        content.body.addWidget(self.latex)
        root.addWidget(content)

        answer = CardFrame()
        answer.add_title("作答与解析")
        self.user_answer = self._text_area("我的作答", 90)
        self.correct_answer = self._text_area("正确答案", 90)
        self.solution = self._text_area("完整解析", 130)
        self.notes = self._text_area("备注", 80)
        for label, editor in (
            ("我的作答", self.user_answer),
            ("正确答案", self.correct_answer),
            ("解析", self.solution),
            ("备注", self.notes),
        ):
            answer.body.addWidget(QLabel(label))
            answer.body.addWidget(editor)
        root.addWidget(answer)
        root.addStretch(1)

        self.subject.currentIndexChanged.connect(self._reload_chapters)
        self.reload_catalog()
        self._connect_change_signals()

    def _connect_change_signals(self) -> None:
        def notify(*_args) -> None:
            self.changed.emit()

        for editor in (
            self.title_edit,
            self.problem_type,
            self.source_book,
            self.source_year,
            self.original_number,
            self.tags,
        ):
            editor.textChanged.connect(notify)
        for editor in (
            self.question,
            self.latex,
            self.user_answer,
            self.correct_answer,
            self.solution,
            self.notes,
        ):
            editor.textChanged.connect(notify)
            editor.textChanged.connect(self._queue_text_area_resize)
        self.subject.currentIndexChanged.connect(notify)
        self.chapter.currentIndexChanged.connect(notify)
        self.priority.valueChanged.connect(notify)

    @staticmethod
    def _text_area(placeholder: str, height: int) -> QTextEdit:
        editor = QTextEdit()
        editor.setPlaceholderText(placeholder)
        editor.setProperty("contentMaxHeight", max(150, height * 2))
        editor.setUndoRedoEnabled(True)
        return editor

    def _queue_text_area_resize(self) -> None:
        self._text_area_resize_timer.start(0)

    def _resize_text_areas_to_content(self) -> None:
        for editor in (
            self.question,
            self.latex,
            self.user_answer,
            self.correct_answer,
            self.solution,
            self.notes,
        ):
            viewport_width = editor.viewport().width()
            if viewport_width <= 1:
                continue
            document = editor.document()
            document.setTextWidth(viewport_width)
            line_height = editor.fontMetrics().lineSpacing()
            content_height = math.ceil(
                document.documentLayout().documentSize().height()
            )
            one_line_height = line_height + 24
            target_height = max(one_line_height, content_height + 24)
            maximum = int(editor.property("contentMaxHeight"))
            editor.setFixedHeight(min(maximum, target_height))
            editor.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
                if target_height > maximum
                else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._queue_text_area_resize()

    def reload_catalog(self) -> None:
        current = self.subject.currentData()
        self.subject.blockSignals(True)
        self.subject.clear()
        self.subject.addItem("（未指定）", None)
        for subject in self.intake.app.list_subjects():
            self.subject.addItem(subject.name, subject.id)
        index = self.subject.findData(current)
        self.subject.setCurrentIndex(index if index >= 0 else 0)
        self.subject.blockSignals(False)
        self._reload_chapters()

    def _reload_chapters(self) -> None:
        current = self.chapter.currentData()
        self.chapter.clear()
        self.chapter.addItem("（未指定）", None)
        subject_id = self.subject.currentData()
        if subject_id:
            for choice in self.intake.app.list_category_choices():
                if choice.subject_id == subject_id and choice.chapter_id is not None:
                    self.chapter.addItem(
                        " / ".join(choice.chapter_path),
                        choice.chapter_id,
                    )
        index = self.chapter.findData(current)
        self.chapter.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _optional(text: str) -> str | None:
        value = text.strip()
        return value or None

    def values(self) -> dict[str, Any]:
        return {
            "title": self._optional(self.title_edit.text()),
            "subject_id": self.subject.currentData(),
            "chapter_id": self.chapter.currentData(),
            "problem_type": self._optional(self.problem_type.text()),
            "priority": self.priority.value(),
            "source_book": self._optional(self.source_book.text()),
            "source_year": self._optional(self.source_year.text()),
            "original_number": self._optional(self.original_number.text()),
            "question_markdown": self.question.toPlainText(),
            "question_latex": self.latex.toPlainText(),
            "user_answer": self.user_answer.toPlainText(),
            "correct_answer": self.correct_answer.toPlainText(),
            "solution_markdown": self.solution.toPlainText(),
            "error_analysis": "",
            "notes": self.notes.toPlainText(),
        }

    def tag_names(self) -> list[str]:
        text = self.tags.text().replace("，", ",")
        return [part.strip() for part in text.split(",") if part.strip()]

    def set_values(self, values: dict[str, Any]) -> None:
        self.reload_catalog()
        self.title_edit.setText(str(values.get("title") or ""))
        subject_id = values.get("subject_id")
        if not subject_id and values.get("subject_name"):
            for index in range(self.subject.count()):
                if self.subject.itemText(index) == str(values["subject_name"]):
                    subject_id = self.subject.itemData(index)
                    break
        index = self.subject.findData(subject_id)
        self.subject.setCurrentIndex(index if index >= 0 else 0)
        self._reload_chapters()
        chapter_id = values.get("chapter_id")
        if not chapter_id and values.get("chapter_name"):
            expected_name = str(values["chapter_name"])
            for idx in range(self.chapter.count()):
                label = self.chapter.itemText(idx)
                if label == expected_name or label.rsplit(" / ", 1)[-1] == expected_name:
                    chapter_id = self.chapter.itemData(idx)
                    break
        chapter_index = self.chapter.findData(chapter_id)
        self.chapter.setCurrentIndex(chapter_index if chapter_index >= 0 else 0)
        self.problem_type.setText(str(values.get("problem_type") or ""))
        try:
            priority = int(values.get("priority") or 3)
        except (TypeError, ValueError):
            priority = 3
        self.priority.setValue(max(1, min(5, priority)))
        self.source_book.setText(str(values.get("source_book") or ""))
        self.source_year.setText(str(values.get("source_year") or ""))
        self.original_number.setText(str(values.get("original_number") or ""))
        self.question.setPlainText(str(values.get("question_markdown") or ""))
        self.latex.setPlainText(str(values.get("question_latex") or ""))
        self.user_answer.setPlainText(
            ""
            if self.clear_user_answer_on_load
            else str(values.get("user_answer") or "")
        )
        self.correct_answer.setPlainText(str(values.get("correct_answer") or ""))
        self.solution.setPlainText(str(values.get("solution_markdown") or ""))
        self.notes.setPlainText(str(values.get("notes") or ""))
        tags = values.get("tags")
        self.tags.setText(", ".join(str(tag) for tag in tags) if isinstance(tags, list) else "")
        self._queue_text_area_resize()

    def clear(self) -> None:
        self.set_values({})


class IntakePage(QWidget):
    problem_committed = Signal(str)
    status_message = Signal(str)
    dashboard_requested = Signal()
    library_requested = Signal()
    open_problem_requested = Signal(str)

    def __init__(self, intake: ProblemIntakeService, parent=None) -> None:
        super().__init__(parent)
        self.intake = intake
        self.manual_images: list[Path] = []
        self.ai_files: list[Path] = []
        self.ai_job_id: str | None = None
        self.ai_worker: AIJobWorker | None = None
        self._cancelled_ai_jobs: set[str] = set()
        self.region_worker: RegionRecognitionWorker | None = None
        self.ai_candidates: list[IntakeCandidate] = []
        self.candidate_index = 0
        self.last_problem_id: str | None = None
        self._restoring_manual_draft = False

        self.manual_draft_timer = QTimer(self)
        self.manual_draft_timer.setSingleShot(True)
        self.manual_draft_timer.setInterval(700)
        self.manual_draft_timer.timeout.connect(self._save_manual_draft)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self._refresh_ai_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_manual())
        self.stack.addWidget(self._build_ai_upload())
        self.stack.addWidget(self._build_processing())
        self.stack.addWidget(self._build_confirmation())
        self.stack.addWidget(self._build_done())
        root.addWidget(self.stack)

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(500)
        self.progress_timer.timeout.connect(self._poll_progress)
        self._restore_manual_draft()
        self._restore_existing_session()

    @staticmethod
    def _scroll(widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _header(self, title_text: str, hint_text: str, back_slot) -> PageHeader:
        header = PageHeader(title_text, hint_text)
        back = ghost_button("返回")
        back.clicked.connect(back_slot)
        header.add_leading(back)
        return header

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("PageRoot")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        return page, layout

    def _build_manual(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._header(
                "手动录题",
                "内容会自动保存为草稿；只有点击“确认入库”才会创建正式题目。",
                self.library_requested.emit,
            )
        )
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        self.manual_form = ProblemForm(self.intake)
        self.manual_form.changed.connect(self._queue_manual_draft)
        body_layout.addWidget(self.manual_form)

        assets = CardFrame()
        assets.add_title("原始图片")
        assets.add_hint("可选。原图会按内容寻址保存并保持不可覆盖。")
        self.manual_asset_list = QListWidget()
        self.manual_asset_list.setMaximumHeight(110)
        add_asset = QPushButton("添加图片")
        add_asset.clicked.connect(self._add_manual_images)
        remove_asset = QPushButton("移除选中")
        remove_asset.clicked.connect(self._remove_manual_images)
        assets.body.addWidget(self.manual_asset_list)
        asset_buttons = QHBoxLayout()
        asset_buttons.addWidget(add_asset)
        asset_buttons.addWidget(remove_asset)
        asset_buttons.addStretch(1)
        assets.body.addLayout(asset_buttons)
        body_layout.addWidget(assets)
        layout.addWidget(self._scroll(body), stretch=1)

        actions = QHBoxLayout()
        actions.addWidget(QLabel("草稿自动保存，关闭程序后仍可继续"))
        actions.addStretch(1)
        cancel = QPushButton("清空表单")
        cancel.clicked.connect(self._clear_manual)
        submit = primary_button("确认入库")
        submit.clicked.connect(self._commit_manual)
        actions.addWidget(cancel)
        actions.addWidget(submit)
        layout.addLayout(actions)
        return page

    def _build_ai_upload(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._header(
                "AI 录题 · 上传",
                "添加图片并说明目标特征，例如“红圈处是错题”或“只提取第 3 题”。",
                self.library_requested.emit,
            )
        )
        upload = CardFrame()
        upload.add_title("1. 添加图片")
        upload.add_hint(
            "每张图片可以识别一道或多道候选题；AI 会拆分后逐题进入确认流程。"
        )
        upload_content = QHBoxLayout()
        self.ai_upload_preview = ImagePreviewLabel("选择图片后将在这里预览")
        upload_content.addWidget(self.ai_upload_preview, stretch=2)
        self.ai_file_list = QListWidget()
        self.ai_file_list.setObjectName("UploadFileList")
        self.ai_file_list.setViewMode(QListView.ViewMode.IconMode)
        self.ai_file_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.ai_file_list.setMovement(QListView.Movement.Static)
        self.ai_file_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.ai_file_list.setIconSize(QSize(128, 88))
        self.ai_file_list.setGridSize(QSize(154, 122))
        self.ai_file_list.setMinimumSize(QSize(330, 260))
        self.ai_file_list.currentRowChanged.connect(self._show_ai_file_preview)
        upload_content.addWidget(self.ai_file_list, stretch=1)
        upload.body.addLayout(upload_content)
        file_actions = QHBoxLayout()
        add = primary_button("选择图片")
        add.clicked.connect(self._add_ai_files)
        remove = QPushButton("移除选中")
        remove.clicked.connect(self._remove_ai_files)
        file_actions.addWidget(add)
        file_actions.addWidget(remove)
        file_actions.addStretch(1)
        upload.body.addLayout(file_actions)
        layout.addWidget(upload)

        mode = CardFrame()
        mode.add_title("2. 选择识别方式")
        mode.add_hint("自动模式保持逐图识别；多图一题会按上传顺序将全部图片作为同一次视觉请求发送。")
        self.ai_recognition_mode = QComboBox()
        self.ai_recognition_mode.addItem("自动（逐图识别，仅提示结构建议）", "auto")
        self.ai_recognition_mode.addItem("一图一题", "one_to_one")
        self.ai_recognition_mode.addItem("一图多题", "one_to_many")
        self.ai_recognition_mode.addItem("多图一题（按上传顺序）", "many_to_one")
        mode.body.addWidget(self.ai_recognition_mode)
        layout.addWidget(mode)

        prompt = CardFrame()
        prompt.add_title("3. 告诉 AI 如何定位题目")
        prompt.add_hint("这是本批图片的补充说明；程序仍会强制结构化输出和字段安全规则。")
        self.ai_instruction = QTextEdit()
        self.ai_instruction.setPlaceholderText(
            "例如：画红圈的是目标错题；蓝色手写内容是我的作答；不要提取页脚答案。"
        )
        self.ai_instruction.setMaximumHeight(120)
        prompt.body.addWidget(self.ai_instruction)
        templates = QHBoxLayout()
        for text in ("红圈处是目标错题", "只提取第 3 题", "手写内容是我的作答"):
            button = ghost_button(text)
            button.clicked.connect(lambda _checked=False, value=text: self._append_instruction(value))
            templates.addWidget(button)
        templates.addStretch(1)
        prompt.body.addLayout(templates)
        layout.addWidget(prompt)
        layout.addStretch(1)

        start_row = QHBoxLayout()
        self.ai_use_cache = QCheckBox("使用历史识别缓存")
        self.ai_use_cache.setChecked(True)
        self.ai_use_cache.setToolTip("关闭后将重新请求 AI，并用新结果更新同一图片的缓存")
        self.ai_cache_hint = QLabel()
        self.ai_cache_hint.setObjectName("MutedLabel")
        clear_cache = ghost_button("清空识别缓存")
        clear_cache.clicked.connect(self._clear_recognition_cache)
        start_row.addWidget(self.ai_use_cache)
        start_row.addWidget(self.ai_cache_hint)
        start_row.addWidget(clear_cache)
        self.ai_config_hint = QLabel()
        self.ai_config_hint.setObjectName("PageHint")
        start_row.addWidget(self.ai_config_hint)
        start_row.addStretch(1)
        start = primary_button("开始识别")
        start.clicked.connect(self._start_ai)
        start_row.addWidget(start)
        layout.addLayout(start_row)
        return page

    def _build_processing(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._header(
                "AI 录题 · 后台处理中",
                "可以返回题库，任务仍会继续；再次从题库进入即可查看当前进度。",
                self.library_requested.emit,
            )
        )
        card = CardFrame()
        card.add_title("正在整理图片")
        self.processing_status = card.add_hint("正在准备任务…")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        card.body.addWidget(self.progress_bar)
        self.processing_steps = QLabel(
            "每张图片只发起一次主 AI 请求；完成首张后会显示各阶段实测耗时。"
        )
        self.processing_steps.setWordWrap(True)
        card.body.addWidget(self.processing_steps)
        self.processing_error = QLabel("")
        self.processing_error.setWordWrap(True)
        self.processing_error.setObjectName("DangerLabel")
        card.body.addWidget(self.processing_error)
        actions = QHBoxLayout()
        self.processing_cancel_button = danger_button("取消后台任务")
        self.processing_cancel_button.clicked.connect(self._cancel_ai)
        self.processing_retry = primary_button("重新尝试失败项")
        self.processing_retry.clicked.connect(self._retry_failed_ai)
        self.processing_retry.setVisible(False)
        self.processing_back = QPushButton("返回修改上传内容")
        self.processing_back.clicked.connect(self.show_ai_upload)
        self.processing_back.setVisible(False)
        actions.addWidget(self.processing_cancel_button)
        actions.addWidget(self.processing_retry)
        actions.addWidget(self.processing_back)
        actions.addStretch(1)
        card.body.addLayout(actions)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _build_confirmation(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            self._header(
                "AI 录题 · 确认结果",
                "",
                self.library_requested.emit,
            )
        )
        self.ai_result_tabs = QTabWidget()
        self.ai_result_tabs.setObjectName("AIResultTabs")

        preview_host = QWidget()
        preview_layout = QVBoxLayout(preview_host)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self.ai_result_preview = MathContentView()
        self.ai_result_preview.setMinimumSize(QSize(520, 440))
        preview_layout.addWidget(self.ai_result_preview)
        self.ai_result_tabs.addTab(preview_host, "阅读预览")

        form_host = QWidget()
        form_layout = QVBoxLayout(form_host)
        form_layout.setContentsMargins(0, 0, 8, 0)
        self.ai_form = ProblemForm(
            self.intake, clear_user_answer_on_load=True
        )
        self.ai_form.changed.connect(self._queue_ai_preview)
        form_layout.addWidget(self.ai_form)
        self.ai_result_tabs.addTab(self._scroll(form_host), "编辑字段")
        self.ai_result_tabs.addTab(
            self._build_image_tools_tab(), "原图范围与重识别"
        )
        self.ai_result_tabs.currentChanged.connect(self._on_ai_result_tab_changed)
        layout.addWidget(self.ai_result_tabs, stretch=1)

        actions = QHBoxLayout()
        previous = QPushButton("上一题")
        previous.clicked.connect(lambda: self._move_candidate(-1))
        next_button = QPushButton("下一题")
        next_button.clicked.connect(lambda: self._move_candidate(1))
        skip = danger_button("删除错误候选")
        skip.clicked.connect(self._reject_candidate)
        confirm = primary_button("确认入库")
        confirm.clicked.connect(self._commit_candidate)
        actions.addWidget(previous)
        actions.addWidget(next_button)
        actions.addStretch(1)
        actions.addWidget(skip)
        actions.addWidget(confirm)
        layout.addLayout(actions)
        return page

    def _build_image_tools_tab(self) -> QScrollArea:
        """Build the dedicated source-image and recognition adjustment tab."""
        # The image tools can be taller than the available confirmation pane.
        # Keep them in a single scrollable work area so controls never paint
        # over the preview when the window height is constrained.
        image_tools = QWidget()
        image_tools_layout = QVBoxLayout(image_tools)
        image_tools_layout.setContentsMargins(0, 0, 0, 0)
        image_tools_layout.setSpacing(10)
        self.image_preview = ImagePreviewLabel("无原图预览")
        self.image_preview.setMinimumHeight(280)
        self.image_preview.setMaximumHeight(360)
        self.image_preview.set_editable(True)
        self.image_preview.region_drawn.connect(self._save_drawn_region)
        image_tools_layout.addWidget(self.image_preview)

        source_title = QLabel("来源图片")
        source_title.setObjectName("SectionTitle")
        image_tools_layout.addWidget(source_title)
        self.source_image_list = QListWidget()
        self.source_image_list.setObjectName("CandidateSourceImages")
        self.source_image_list.setFixedHeight(72)
        image_tools_layout.addWidget(self.source_image_list)
        source_actions = QHBoxLayout()
        source_up = QPushButton("来源图上移")
        source_up.clicked.connect(lambda: self._move_source_image(-1))
        source_down = QPushButton("来源图下移")
        source_down.clicked.connect(lambda: self._move_source_image(1))
        source_actions.addWidget(source_up)
        source_actions.addWidget(source_down)
        source_actions.addStretch(1)
        image_tools_layout.addLayout(source_actions)
        source_split = QPushButton("拆分识别单元")
        source_split.clicked.connect(self._split_recognition_unit)
        image_tools_layout.addWidget(source_split)

        region_title = QLabel("题目区域")
        region_title.setObjectName("SectionTitle")
        image_tools_layout.addWidget(region_title)
        self.region_label = QLabel("")
        self.region_label.setObjectName("PageHint")
        image_tools_layout.addWidget(self.region_label)
        region_hint = QLabel("空白处重画，框内移动，拖动控制柄微调。调整后仅保存区域，不会自动调用 AI。")
        region_hint.setObjectName("PageHint")
        region_hint.setWordWrap(True)
        image_tools_layout.addWidget(region_hint)
        region_secondary_actions = QHBoxLayout()
        reset_region = QPushButton("恢复整图")
        reset_region.clicked.connect(self._reset_candidate_region)
        self.rerecognize_region = primary_button("按当前区域重新识别")
        self.rerecognize_region.clicked.connect(
            self._start_region_rerecognition
        )
        self.undo_region_recognition = QPushButton("撤回上次重识别")
        self.undo_region_recognition.clicked.connect(
            self._undo_region_rerecognition
        )
        region_secondary_actions.addWidget(reset_region)
        region_secondary_actions.addWidget(self.undo_region_recognition)
        region_secondary_actions.addStretch(1)
        image_tools_layout.addLayout(region_secondary_actions)
        image_tools_layout.addWidget(self.rerecognize_region)

        uncertain_title = QLabel("AI 核对提示")
        uncertain_title.setObjectName("SectionTitle")
        image_tools_layout.addWidget(uncertain_title)
        self.uncertain_label = QLabel("")
        self.uncertain_label.setWordWrap(True)
        self.uncertain_label.setObjectName("PageHint")
        image_tools_layout.addWidget(self.uncertain_label)
        image_tools_layout.addStretch(1)
        return self._scroll(image_tools)

    def _build_done(self) -> QWidget:
        page, layout = self._page()
        layout.addStretch(1)
        card = CardFrame()
        card.add_title("录题完成")
        self.done_message = card.add_hint("")
        primary_actions = QHBoxLayout()
        ai = primary_button("继续 AI 录题")
        ai.clicked.connect(self._new_ai)
        manual = QPushButton("继续手动录题")
        manual.clicked.connect(self._new_manual)
        home = QPushButton("返回题库")
        home.clicked.connect(self.library_requested.emit)
        primary_actions.addWidget(ai)
        primary_actions.addWidget(manual)
        primary_actions.addWidget(home)
        card.body.addLayout(primary_actions)

        secondary_actions = QHBoxLayout()
        view = QPushButton("查看刚入库的题目")
        view.clicked.connect(self._open_last_problem)
        finish = QPushButton("返回工作台")
        finish.clicked.connect(self.dashboard_requested.emit)
        secondary_actions.addWidget(view)
        secondary_actions.addWidget(finish)
        secondary_actions.addStretch(1)
        card.body.addLayout(secondary_actions)
        layout.addWidget(card)
        layout.addStretch(2)
        return page

    def show_manual(self) -> None:
        self.manual_form.reload_catalog()
        self.stack.setCurrentIndex(_PAGE_MANUAL)

    def show_ai_upload(self) -> None:
        ai = self.intake.runtime.settings.ai
        provider_label = (
            "Faro API（真实识图）"
            if ai.default_provider == "openai_compatible"
            else "Mock（离线测试）"
        )
        self.ai_config_hint.setText(
            f"{provider_label} · {ai.default_vision_model or '未设置模型'} · "
            f"{'已启用' if ai.enabled else '尚未启用'}"
        )
        self._refresh_recognition_cache_hint()
        self.stack.setCurrentIndex(_PAGE_AI_UPLOAD)

    def _refresh_recognition_cache_hint(self) -> None:
        summary = self.intake.ai.recognition_cache_summary()
        size_kb = summary["bytes"] / 1024
        self.ai_cache_hint.setText(
            f"已缓存 {summary['count']} 条 · {size_kb:.1f} KB"
        )

    def _clear_recognition_cache(self) -> None:
        summary = self.intake.ai.recognition_cache_summary()
        if not summary["count"]:
            self._refresh_recognition_cache_hint()
            return
        if (
            QMessageBox.question(
                self,
                "清空识别缓存",
                f"确认删除 {summary['count']} 条 AI 识别缓存？\n"
                "不会删除原图、题目或历史录题任务。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        deleted = self.intake.ai.clear_recognition_cache()
        self._refresh_recognition_cache_hint()
        self.status_message.emit(f"已清空 {deleted} 条 AI 识别缓存")

    def show_ai(self) -> None:
        if not self.ai_job_id:
            self.ai_job_id = self.intake.latest_resumable_ai_job()
        if self.ai_job_id:
            try:
                self.ai_candidates = [
                    item
                    for item in self.intake.list_candidates(self.ai_job_id)
                    if item.status in {"pending", "conflict"}
                ]
            except DomainError:
                self.ai_job_id = None
                self.ai_candidates = []
        if self.ai_candidates:
            self._load_candidate()
            self.stack.setCurrentIndex(_PAGE_AI_CONFIRM)
        elif self.ai_job_id:
            self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
            if not (self.ai_worker and self.ai_worker.isRunning()):
                self._start_worker(self.ai_job_id)
            else:
                self.progress_timer.start()
                self._poll_progress()
        else:
            self.show_ai_upload()

    def _abandon_ai(self) -> None:
        job_id = self.ai_job_id or ""
        if not job_id:
            return
        if (
            QMessageBox.question(
                self,
                "放弃录题批次",
                "确认放弃选中的 AI 录题批次？\n"
                "已入库的题目不会受影响，未确认候选将不再显示。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        if job_id == self.ai_job_id and self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
        try:
            self.intake.abandon_ai_batch(job_id)
        except DomainError as exc:
            QMessageBox.warning(self, "无法放弃批次", str(exc))
            return
        if job_id == self.ai_job_id:
            self.ai_job_id = None
            self.ai_candidates.clear()
            self.progress_timer.stop()
        self.status_message.emit("已放弃选中的 AI 录题批次")
        self.library_requested.emit()

    def _restore_existing_session(self) -> None:
        job_id = self.intake.latest_resumable_ai_job()
        if not job_id:
            return
        try:
            progress = self.intake.progress(job_id)
            if progress.status == "running":
                self.intake.abandon_ai_batch(job_id)
                self.status_message.emit(
                    "已取消上次意外中断的 AI 录题任务，请重新开始识别"
                )
                return
        except DomainError:
            return
        self.ai_job_id = job_id
        try:
            self.ai_candidates = [
                item
                for item in self.intake.list_candidates(job_id)
                if item.status in {"pending", "conflict"}
            ]
        except DomainError:
            self.ai_candidates = []

    def _restore_manual_draft(self) -> None:
        draft = self.intake.load_manual_draft()
        if draft is None:
            return
        self._restoring_manual_draft = True
        values = dict(draft.fields)
        values["tags"] = draft.tag_names
        self.manual_form.set_values(values)
        self.manual_images = list(draft.image_paths)
        self.manual_asset_list.clear()
        for path in self.manual_images:
            self.manual_asset_list.addItem(str(path))
        self._restoring_manual_draft = False

    def _queue_manual_draft(self) -> None:
        if not self._restoring_manual_draft:
            self.manual_draft_timer.start()

    def _save_manual_draft(self) -> None:
        if self._restoring_manual_draft:
            return
        fields = self.manual_form.values()
        tags = self.manual_form.tag_names()
        has_content = bool(
            fields.get("title")
            or str(fields.get("question_markdown") or "").strip()
            or str(fields.get("question_latex") or "").strip()
            or tags
            or self.manual_images
        )
        if not has_content:
            self.intake.clear_manual_draft()
            return
        try:
            self.intake.save_manual_draft(
                fields,
                tag_names=tags,
                image_paths=self.manual_images,
            )
        except (DomainError, OSError) as exc:
            self.status_message.emit(f"手动草稿保存失败：{exc}")

    def _add_manual_images(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "添加原始图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        for value in files:
            path = Path(value)
            if path not in self.manual_images:
                self.manual_images.append(path)
                self.manual_asset_list.addItem(str(path))
        self._queue_manual_draft()

    def _remove_manual_images(self) -> None:
        rows = sorted({self.manual_asset_list.row(item) for item in self.manual_asset_list.selectedItems()}, reverse=True)
        for row in rows:
            self.manual_asset_list.takeItem(row)
            self.manual_images.pop(row)
        self._queue_manual_draft()

    def _clear_manual(self) -> None:
        if (
            QMessageBox.question(self, "清空表单", "清空当前尚未入库的内容？")
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.manual_form.clear()
        self.manual_images.clear()
        self.manual_asset_list.clear()
        self.manual_draft_timer.stop()
        self.intake.clear_manual_draft()

    def _commit_manual(self) -> None:
        try:
            problem = self.intake.commit_manual(
                self.manual_form.values(),
                tag_names=self.manual_form.tag_names(),
                image_paths=self.manual_images,
            )
        except DomainError as exc:
            self.status_message.emit(str(exc))
            QMessageBox.warning(self, "无法入库", str(exc))
            return
        self.last_problem_id = problem.id
        self.manual_form.clear()
        self.manual_images.clear()
        self.manual_asset_list.clear()
        self.manual_draft_timer.stop()
        self.intake.clear_manual_draft()
        self.problem_committed.emit(problem.id)
        self._show_done(f"“{problem.title or '无标题题目'}”已进入正式题库。")

    def _add_ai_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择需要 AI 整理的图片",
            "",
            "Images (*.png *.jpg *.jpeg *.webp);;All (*.*)",
        )
        invalid: list[str] = []
        first_added_row: int | None = None
        for value in files:
            path = Path(value)
            if path in self.ai_files:
                continue
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                invalid.append(path.name)
                continue
            self.ai_files.append(path)
            thumbnail = pixmap.scaled(
                self.ai_file_list.iconSize(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            item = QListWidgetItem(QIcon(thumbnail), path.name)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.ai_file_list.addItem(item)
            if first_added_row is None:
                first_added_row = self.ai_file_list.count() - 1
        if first_added_row is not None:
            self.ai_file_list.setCurrentRow(first_added_row)
        if invalid:
            QMessageBox.warning(
                self,
                "部分图片无法读取",
                "以下文件不是有效图片或格式不受支持：\n" + "\n".join(invalid),
            )

    def _remove_ai_files(self) -> None:
        rows = sorted({self.ai_file_list.row(item) for item in self.ai_file_list.selectedItems()}, reverse=True)
        for row in rows:
            self.ai_file_list.takeItem(row)
            self.ai_files.pop(row)
        if self.ai_file_list.count():
            self.ai_file_list.setCurrentRow(
                min(rows[-1] if rows else 0, self.ai_file_list.count() - 1)
            )
        else:
            self.ai_upload_preview.clear_preview()

    def _show_ai_file_preview(self, row: int) -> None:
        if 0 <= row < len(self.ai_files):
            if self.ai_upload_preview.set_path(self.ai_files[row]):
                return
        self.ai_upload_preview.clear_preview()

    def _append_instruction(self, text: str) -> None:
        current = self.ai_instruction.toPlainText().strip()
        self.ai_instruction.setPlainText(f"{current}\n{text}".strip())

    def _start_ai(self) -> None:
        if self.ai_worker and self.ai_worker.isRunning():
            self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
            return
        try:
            started = self.intake.start_ai(
                self.ai_files,
                user_instruction=self.ai_instruction.toPlainText(),
                recognition_mode=str(self.ai_recognition_mode.currentData()),
                use_recognition_cache=self.ai_use_cache.isChecked(),
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法开始识别", str(exc))
            return
        self.ai_job_id = started.job_id
        self._cancelled_ai_jobs.discard(started.job_id)
        self.ai_candidates.clear()
        self.processing_error.clear()
        self.processing_retry.setVisible(False)
        self.processing_back.setVisible(False)
        self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
        self._start_worker(started.job_id)
        self.status_message.emit("AI 录题任务已在后台开始")

    def _start_worker(self, job_id: str) -> None:
        self.ai_worker = AIJobWorker(self.intake.ai, job_id, self)
        self.ai_worker.finished_ok.connect(self._on_ai_done)
        self.ai_worker.failed.connect(self._on_ai_failed)
        self.ai_worker.start()
        self.progress_timer.start()
        self._poll_progress()

    def _poll_progress(self) -> None:
        if not self.ai_job_id:
            return
        try:
            progress = self.intake.progress(self.ai_job_id)
        except DomainError as exc:
            self.processing_error.setText(str(exc))
            self.progress_timer.stop()
            return
        self.progress_bar.setRange(0, max(1, progress.total))
        self.progress_bar.setValue(progress.done + progress.failed)
        self.processing_status.setText(
            f"{progress.stage_label} · "
            f"完成 {progress.done} / {progress.total} · 失败 {progress.failed}"
        )
        timing_labels = (
            ("preflight", "本地预检查"),
            ("image_encode", "图片读取与编码"),
            ("request", "AI 请求与等待"),
            ("response_parse", "响应 JSON 解析"),
            ("validation", "字段校验"),
            ("candidate_write", "候选写入"),
            ("provider_total", "AI 提供商总计"),
            ("total", "单图总计"),
        )
        measured = []
        granular_provider = "request" in progress.timings_ms
        for key, label in timing_labels:
            if key == "provider_total" and granular_provider:
                continue
            value = progress.timings_ms.get(key)
            if value is None:
                continue
            measured.append(
                f"{label} {value / 1000:.2f} 秒"
                if value >= 1000
                else f"{label} {value:.0f} 毫秒"
            )
        if measured:
            retry_text = (
                f" · 自动重试 {progress.retry_count} 次"
                if progress.retry_count
                else ""
            )
            cache_text = (
                f" · 本地缓存命中 {progress.cache_hits} 次"
                if progress.cache_hits
                else ""
            )
            self.processing_steps.setText(
                f"已完成 {progress.timing_samples} 张的实测平均："
                + " · ".join(measured)
                + retry_text
                + cache_text
            )
        else:
            self.processing_steps.setText(
                f"当前真实阶段：{progress.stage_label}。"
                "每张图片只发起一次主 AI 请求；完成首张后显示实测耗时。"
            )
        if progress.status == "cancelled":
            self.progress_timer.stop()
            self._cancelled_ai_jobs.add(progress.job_id)
            if progress.job_id == self.ai_job_id:
                self.ai_job_id = None
                self.ai_candidates.clear()
                self.processing_status.setText("任务已取消")
                self.stack.setCurrentIndex(_PAGE_AI_UPLOAD)
                QTimer.singleShot(0, self.show_ai_upload)
            return
        if progress.status == "completed":
            self.progress_timer.stop()

    def _cancel_ai(self) -> None:
        job_id = self.ai_job_id
        if not job_id:
            return
        self.processing_cancel_button.setEnabled(False)
        self._cancelled_ai_jobs.add(job_id)
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
        try:
            self.intake.abandon_ai_batch(job_id)
        except DomainError as exc:
            self._cancelled_ai_jobs.discard(job_id)
            self.processing_cancel_button.setEnabled(True)
            self.processing_error.setText(str(exc))
            return
        self.progress_timer.stop()
        self.ai_job_id = None
        self.ai_candidates.clear()
        self.processing_error.clear()
        self.processing_status.setText("任务已取消")
        self.status_message.emit("AI 录题任务已取消，不会在下次启动时恢复")
        self.stack.setCurrentIndex(_PAGE_AI_UPLOAD)
        self.show_ai_upload()
        QTimer.singleShot(0, self.show_ai_upload)

    def _retry_failed_ai(self) -> None:
        if not self.ai_job_id:
            return
        if self.ai_worker and self.ai_worker.isRunning():
            return
        self.processing_error.clear()
        self.processing_retry.setVisible(False)
        self.processing_back.setVisible(False)
        self.processing_status.setText("正在重新连接 AI 服务并重试失败图片…")
        self._start_worker(self.ai_job_id)
        self.status_message.emit("正在重新尝试失败的 AI 录题项")

    def _on_ai_done(self, job_id: str) -> None:
        if job_id in self._cancelled_ai_jobs:
            return
        if job_id != self.ai_job_id:
            return
        self.progress_timer.stop()
        self._poll_progress()
        try:
            self.ai_candidates = [
                item
                for item in self.intake.list_candidates(job_id)
                if item.status in {"pending", "conflict"}
            ]
            failures = self.intake.failed_items(job_id)
        except DomainError as exc:
            self._on_ai_failed(job_id, str(exc))
            return
        if not self.ai_candidates:
            detail = "\n".join(failures[:5]) or "AI 没有生成可确认的题目。"
            self.processing_error.setText(detail)
            self.processing_retry.setVisible(bool(failures))
            self.processing_back.setVisible(True)
            self.status_message.emit("AI 识别未生成候选题")
            return
        self.candidate_index = 0
        self.processing_retry.setVisible(False)
        self._load_candidate()
        self.stack.setCurrentIndex(_PAGE_AI_CONFIRM)
        self.status_message.emit(
            f"AI 已完成，生成 {len(self.ai_candidates)} 道待确认题目"
        )

    def _on_ai_failed(self, job_id: str, error: str) -> None:
        if job_id in self._cancelled_ai_jobs:
            return
        if job_id != self.ai_job_id:
            return
        self.progress_timer.stop()
        self.processing_error.setText(error)
        self.processing_retry.setVisible(True)
        self.processing_back.setVisible(True)
        self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
        self.status_message.emit(f"AI 录题失败：{error}")

    def _load_candidate(self) -> None:
        if not self.ai_candidates:
            return
        self.candidate_index %= len(self.ai_candidates)
        candidate = self.ai_candidates[self.candidate_index]
        self.ai_form.set_values(candidate.fields)
        self._refresh_ai_preview()
        self.image_preview.set_path(candidate.original_image)
        self.source_image_list.clear()
        for path in candidate.source_images:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.source_image_list.addItem(item)
        if self.source_image_list.count():
            self.source_image_list.setCurrentRow(0)
        self.image_preview.set_region(candidate.region)
        self._show_region_label(candidate.region)
        region = candidate.region
        is_cropped = bool(
            region
            and (
                region.get("x", 0) > 0.001
                or region.get("y", 0) > 0.001
                or region.get("width", 1) < 0.998
                or region.get("height", 1) < 0.998
            )
        )
        self.rerecognize_region.setEnabled(
            is_cropped
            and not (
                self.region_worker
                and self.region_worker.isRunning()
            )
        )
        self.undo_region_recognition.setEnabled(
            self.intake.can_undo_region_rerecognition(
                candidate.review_item_id
            )
        )
        if candidate.uncertain:
            self.uncertain_label.setText(
                "AI 不确定字段：\n"
                + json.dumps(candidate.uncertain, ensure_ascii=False, indent=2)
            )
            self.uncertain_label.setObjectName("WarningLabel")
        else:
            self.uncertain_label.setText("未报告不确定字段")
            self.uncertain_label.setObjectName("")
        self.uncertain_label.style().unpolish(self.uncertain_label)
        self.uncertain_label.style().polish(self.uncertain_label)

    def _queue_ai_preview(self) -> None:
        self.preview_timer.start()

    def _on_ai_result_tab_changed(self, index: int) -> None:
        if index == 0:
            self._refresh_ai_preview()

    def _refresh_ai_preview(self) -> None:
        if not hasattr(self, "ai_result_preview") or not hasattr(self, "ai_form"):
            return
        fields = self.ai_form.values()
        if self.ai_form.subject.currentData():
            fields["subject_name"] = self.ai_form.subject.currentText()
        if self.ai_form.chapter.currentData():
            fields["chapter_name"] = self.ai_form.chapter.currentText()
        self.ai_result_preview.set_problem(
            fields,
            tag_names=self.ai_form.tag_names(),
            include_answers=True,
            compact=True,
        )

    def _move_candidate(self, delta: int) -> None:
        if not self.ai_candidates:
            return
        self.candidate_index = (self.candidate_index + delta) % len(self.ai_candidates)
        self._load_candidate()

    def _move_source_image(self, delta: int) -> None:
        if not self.ai_candidates:
            return
        row = self.source_image_list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.source_image_list.count():
            return
        item = self.source_image_list.takeItem(row)
        self.source_image_list.insertItem(target, item)
        self.source_image_list.setCurrentRow(target)
        paths = [
            Path(str(self.source_image_list.item(index).data(Qt.ItemDataRole.UserRole)))
            for index in range(self.source_image_list.count())
        ]
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.reorder_candidate_source_images(candidate.review_item_id, paths)
        except DomainError as exc:
            QMessageBox.warning(self, "无法调整来源图", str(exc))
            self._load_candidate()
            return
        self.status_message.emit("来源图片顺序已保存；不会自动重新识别")

    def _split_recognition_unit(self) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.split_candidate_recognition_unit(candidate.review_item_id)
        except DomainError as exc:
            QMessageBox.information(self, "无法拆分识别单元", str(exc))
            return
        self._reload_region_candidate(candidate.review_item_id)
        self.status_message.emit("来源图片已拆为独立识别单元；当前候选内容保持不变")

    def _show_region_label(self, region: dict[str, float]) -> None:
        if region:
            text = (
                "当前题目区域："
                f"x {region['x']:.1%} · y {region['y']:.1%} · "
                f"宽 {region['width']:.1%} · 高 {region['height']:.1%}"
            )
        else:
            text = "当前题目区域：整张原图"
        self.region_label.setText(text)

    def _save_drawn_region(self, region: dict[str, float]) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            normalized = self.intake.update_ai_candidate_region(
                candidate.review_item_id, region
            )
        except DomainError as exc:
            self.image_preview.set_region(candidate.region)
            QMessageBox.warning(self, "无法保存区域", str(exc))
            return
        candidate.region.clear()
        candidate.region.update(normalized)
        self.image_preview.set_region(normalized)
        self._show_region_label(normalized)
        self.rerecognize_region.setEnabled(True)
        self.status_message.emit("当前题目区域已保存")

    def _reset_candidate_region(self) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.update_ai_candidate_region(candidate.review_item_id, {})
        except DomainError as exc:
            QMessageBox.warning(self, "无法恢复整图", str(exc))
            return
        candidate.region.clear()
        self.image_preview.set_region({})
        self._show_region_label({})
        self.rerecognize_region.setEnabled(False)
        self.status_message.emit("当前候选已恢复使用整张原图")

    def _start_region_rerecognition(self) -> None:
        if not self.ai_candidates:
            return
        if self.region_worker and self.region_worker.isRunning():
            return
        candidate = self.ai_candidates[self.candidate_index]
        if not candidate.region:
            QMessageBox.information(
                self,
                "请先框选题目",
                "请在左侧原图上绘制一个小于整图的题目区域，再重新识别。",
            )
            return
        self.rerecognize_region.setEnabled(False)
        self.rerecognize_region.setText("正在重新识别…")
        self.status_message.emit("正在按当前蓝框裁切临时图片并重新识别")
        self.region_worker = RegionRecognitionWorker(
            self.intake,
            candidate.review_item_id,
            self.ai_form.values(),
            self.ai_form.tag_names(),
            self,
        )
        self.region_worker.finished_ok.connect(
            self._on_region_rerecognition_done
        )
        self.region_worker.failed.connect(
            self._on_region_rerecognition_failed
        )
        self.region_worker.finished.connect(
            self._on_region_worker_finished
        )
        self.region_worker.start()

    def _on_region_rerecognition_done(
        self,
        proposal: RegionRecognitionProposal,
    ) -> None:
        self.rerecognize_region.setText("按当前区域重新识别")
        dialog = _RegionRecognitionCompareDialog(proposal, self)
        dialog.exec()
        try:
            self.intake.decide_region_rerecognition(
                proposal.proposal_id,
                apply_new=dialog.apply_new,
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法处理重新识别结果", str(exc))
            self._reload_region_candidate(proposal.candidate_id)
            return
        if dialog.apply_new:
            self.status_message.emit("已采用区域重新识别结果，可继续编辑或撤回")
            self._reload_region_candidate(proposal.candidate_id)
        else:
            self.status_message.emit("已保留原识别结果")
            self._load_candidate()

    def _on_region_rerecognition_failed(self, error: str) -> None:
        self.rerecognize_region.setText("按当前区域重新识别")
        self._load_candidate()
        QMessageBox.warning(self, "区域重新识别失败", error)

    def _on_region_worker_finished(self) -> None:
        worker = self.region_worker
        self.region_worker = None
        if worker is not None:
            worker.deleteLater()
        if self.ai_candidates:
            region = self.ai_candidates[self.candidate_index].region
            self.rerecognize_region.setEnabled(bool(region))

    def _reload_region_candidate(self, candidate_id: str) -> None:
        self.ai_candidates = [
            item
            for item in self.intake.list_candidates(self.ai_job_id or "")
            if item.status in {"pending", "conflict"}
        ]
        self.candidate_index = next(
            (
                index
                for index, item in enumerate(self.ai_candidates)
                if item.review_item_id == candidate_id
            ),
            0,
        )
        if self.ai_candidates:
            self._load_candidate()

    def _undo_region_rerecognition(self) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.undo_region_rerecognition(
                candidate.review_item_id
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法撤回", str(exc))
            return
        self._reload_region_candidate(candidate.review_item_id)
        self.status_message.emit("已撤回上一次采用的区域重新识别结果")

    def _commit_candidate(self) -> None:
        if not self.ai_candidates:
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            problem = self.intake.commit_ai_candidate(
                candidate.review_item_id,
                self.ai_form.values(),
                tag_names=self.ai_form.tag_names(),
            )
        except DomainError as exc:
            QMessageBox.warning(self, "无法入库", str(exc))
            return
        self.last_problem_id = problem.id
        self.problem_committed.emit(problem.id)
        self.ai_candidates.pop(self.candidate_index)
        if self.ai_candidates:
            self.candidate_index %= len(self.ai_candidates)
            self._load_candidate()
            self.status_message.emit("题目已入库，继续确认下一题")
        else:
            failures = (
                self.intake.failed_items(self.ai_job_id)
                if self.ai_job_id
                else []
            )
            if failures:
                self.processing_error.setText(
                    f"题目已入库，但本批仍有 {len(failures)} 张图片识别失败。"
                )
                self.processing_retry.setVisible(True)
                self.processing_back.setVisible(False)
                self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)
                self.status_message.emit("题目已入库，仍有失败图片可继续重试")
            else:
                self.ai_job_id = None
                self.ai_files.clear()
                self.ai_file_list.clear()
                self.ai_upload_preview.clear_preview()
                self.ai_instruction.clear()
                self._show_done(
                    f"“{problem.title or 'AI 识别题目'}”已进入正式题库。"
                )

    def _reject_candidate(self) -> None:
        if not self.ai_candidates:
            return
        if (
            QMessageBox.question(
                self,
                "删除错误候选",
                "确认删除这道错误候选？\n"
                "它的暂存题会移入回收站，不影响同图的其他候选。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        candidate = self.ai_candidates[self.candidate_index]
        try:
            self.intake.reject_ai_candidate(candidate.review_item_id)
        except DomainError as exc:
            QMessageBox.warning(self, "无法删除", str(exc))
            return
        self.ai_candidates.pop(self.candidate_index)
        if self.ai_candidates:
            self.candidate_index %= len(self.ai_candidates)
            self._load_candidate()
        else:
            failures = (
                self.intake.failed_items(self.ai_job_id)
                if self.ai_job_id
                else []
            )
            if not failures:
                self.ai_job_id = None
            self.processing_error.setText(
                (
                    f"候选题已处理，但本批仍有 {len(failures)} 张图片识别失败。"
                    if failures
                    else "本批候选题均已跳过。可以返回并重新上传。"
                )
            )
            self.processing_retry.setVisible(bool(failures))
            self.processing_back.setVisible(not failures)
            self.stack.setCurrentIndex(_PAGE_AI_PROCESSING)

    def _show_done(self, message: str) -> None:
        self.done_message.setText(message)
        self.stack.setCurrentIndex(_PAGE_DONE)

    def _new_manual(self) -> None:
        self.manual_form.clear()
        self.show_manual()

    def _new_ai(self) -> None:
        self.ai_job_id = None
        self.ai_candidates.clear()
        self.ai_files.clear()
        self.ai_file_list.clear()
        self.ai_upload_preview.clear_preview()
        self.ai_instruction.clear()
        self.show_ai_upload()

    def _open_last_problem(self) -> None:
        if self.last_problem_id:
            self.open_problem_requested.emit(self.last_problem_id)

    def shutdown(self) -> None:
        """Stop the page-owned worker before the application destroys Qt objects."""

        self.manual_draft_timer.stop()
        self._save_manual_draft()
        self.progress_timer.stop()
        if self.ai_worker and self.ai_worker.isRunning():
            self.ai_worker.cancel()
            self.ai_worker.wait(300)
        if self.region_worker and self.region_worker.isRunning():
            self.region_worker.wait(300)
