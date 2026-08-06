"""Formula rendering stays safe and readable across all UI surfaces."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QObject, QSizeF, Signal, Qt
from PySide6.QtWidgets import QApplication, QWidget

import yancuo_win.ui.math_content as math_content_module
from yancuo_win.ui.math_content import (
    MathContentView,
    build_note_html,
    build_problem_html,
    render_math_text,
)


class _PageStub(QObject):
    loadFinished = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.html = ""
        self.pdf_callback = None

    def setBackgroundColor(self, _color) -> None:  # noqa: ANN001, N802
        pass

    def setHtml(self, value: str) -> None:  # noqa: N802
        self.html = value

    def printToPdf(self, callback) -> None:  # noqa: ANN001, N802
        self.pdf_callback = callback


class _DocumentStub(QObject):
    class Error:
        None_ = 0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.loaded = False

    def load(self, _device) -> int:  # noqa: ANN001
        self.loaded = True
        return self.Error.None_

    def pageCount(self) -> int:  # noqa: N802
        return 1 if self.loaded else 0

    def pagePointSize(self, _page_number: int) -> QSizeF:  # noqa: N802
        return QSizeF(794.0, 600.0)


class _PdfViewStub(QWidget):
    class PageMode:
        MultiPage = 1

    class ZoomMode:
        FitToWidth = 1
        Custom = 2

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.current_document = None

    def viewport(self):
        return self

    def setPageMode(self, _mode) -> None:  # noqa: ANN001, N802
        pass

    def setZoomMode(self, _mode) -> None:  # noqa: ANN001, N802
        pass

    def setZoomFactor(self, factor) -> None:  # noqa: ANN001, N802
        self.zoom_factor_value = factor

    def setPageSpacing(self, _spacing: int) -> None:  # noqa: N802
        pass

    def setDocumentMargins(self, _margins) -> None:  # noqa: ANN001, N802
        pass

    def setDocument(self, document) -> None:  # noqa: ANN001, N802
        self.current_document = document


class _SizingDocument:
    def __init__(self, page_size: QSizeF) -> None:
        self.page_size = page_size

    def pageCount(self) -> int:  # noqa: N802
        return 1

    def pagePointSize(self, _page_number: int) -> QSizeF:  # noqa: N802
        return self.page_size


def test_render_math_text_converts_inline_and_display_latex_to_mathml() -> None:
    rendered = render_math_text(
        r"已知 \[\lim_{x\to\pi}\frac{\sqrt{\sin\frac{x}{2}}-1}{A(x-\pi)^k}=1\]，"
        r"求 \(A\) 与 \(k\)。"
    )

    assert rendered.count("<math") == 3
    assert 'display="block"' in rendered
    assert "<mfrac>" in rendered
    assert r"\frac" not in rendered


def test_reader_swaps_in_only_fully_rendered_documents(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(math_content_module, "QWebEnginePage", _PageStub)
    monkeypatch.setattr(math_content_module, "QPdfDocument", _DocumentStub)
    monkeypatch.setattr(math_content_module, "QPdfView", _PdfViewStub)

    reader = MathContentView()
    reader.set_message("第一版", "内容")
    reader.show()
    app.processEvents()

    pdf_view = reader._view
    first_page = reader._renderer
    assert first_page is not None
    assert pdf_view.isHidden()

    first_page.loadFinished.emit(True)
    assert first_page.pdf_callback is not None
    assert pdf_view.isHidden()
    first_page.pdf_callback(QByteArray(b"first document"))
    first_document = reader._document
    assert pdf_view.current_document is first_document
    assert pdf_view.isVisible()

    reader.set_message("第二版", "新内容")
    app.processEvents()
    second_page = reader._renderer
    assert second_page is not None
    assert second_page is not first_page
    assert pdf_view.current_document is first_document
    assert pdf_view.isVisible()

    second_page.loadFinished.emit(True)
    second_page.pdf_callback(QByteArray(b"second document"))
    assert reader._document is not first_document
    assert pdf_view.current_document is reader._document
    assert pdf_view.isVisible()
    reader.close()


def test_reader_serializes_and_coalesces_overlapping_renders(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(math_content_module, "QWebEnginePage", _PageStub)
    monkeypatch.setattr(math_content_module, "QPdfDocument", _DocumentStub)
    monkeypatch.setattr(math_content_module, "QPdfView", _PdfViewStub)

    reader = MathContentView()
    reader.set_message("First", "one")
    reader.show()
    app.processEvents()
    first_page = reader._renderer
    assert first_page is not None

    first_page.loadFinished.emit(True)
    assert first_page.pdf_callback is not None
    reader.set_message("Second", "two")
    reader.set_message("Latest", "three")
    app.processEvents()

    assert reader._renderer is first_page
    first_page.pdf_callback(QByteArray(b"first document"))
    app.processEvents()

    second_page = reader._renderer
    assert second_page is not None
    assert second_page is not first_page
    assert "Latest" in second_page.html
    assert "Second" not in second_page.html
    reader.close()


def test_adaptive_reader_fits_short_content_and_scrolls_long_content() -> None:
    app = QApplication.instance() or QApplication([])
    reader = MathContentView()
    reader.resize(794, 600)
    reader.set_zoom_scale(1.0)
    reader.set_adaptive_content_height(300, minimum_height=80)

    reader._document = _SizingDocument(QSizeF(794, 120))  # type: ignore[assignment]
    reader._update_content_height()
    assert 115 <= reader.height() <= 125
    assert (
        reader._view.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )

    reader._document = _SizingDocument(QSizeF(794, 1200))  # type: ignore[assignment]
    reader._content_height = None
    reader._update_content_height()
    assert reader.height() == 300
    assert (
        reader._view.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    reader.close()
    app.processEvents()


def test_reader_scroll_helpers_restore_position_and_follow_long_chat() -> None:
    QApplication.instance() or QApplication([])
    reader = MathContentView()
    scrollbar = reader._view.verticalScrollBar()
    scrollbar.setRange(0, 240)

    reader.restore_scroll_position(73)
    assert reader.scroll_position() == 73

    reader.scroll_to_bottom()
    assert reader.scroll_position() == 240


def test_reserved_adaptive_reader_keeps_stable_height() -> None:
    app = QApplication.instance() or QApplication([])
    reader = MathContentView()
    reader.resize(794, 420)
    reader.set_zoom_scale(1.0)
    reader.set_adaptive_content_height(420, reserve_height=True)

    assert reader.height() == 420

    reader._document = _SizingDocument(QSizeF(794, 120))  # type: ignore[assignment]
    reader._update_content_height()
    assert reader.height() == 420

    reader._document = _SizingDocument(QSizeF(794, 1200))  # type: ignore[assignment]
    reader._update_content_height()
    assert reader.height() == 420
    assert (
        reader._view.verticalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    reader.close()
    app.processEvents()


def test_fixed_zoom_scale_is_independent_from_shared_preview_zoom() -> None:
    app = QApplication.instance() or QApplication([])
    original = math_content_module.preview_zoom_scale()
    reader = MathContentView()
    try:
        reader.set_fixed_zoom_scale(1.0)
        assert reader.follows_global_zoom() is False
        assert reader._zoom_scale == 1.0

        math_content_module.set_preview_zoom_scale(0.8)
        assert math_content_module.preview_zoom_scale() == 0.8
        assert reader._zoom_scale == 1.0
    finally:
        math_content_module.set_preview_zoom_scale(original)
        reader.close()
        app.processEvents()


def test_content_sized_problem_document_does_not_force_viewport_height() -> None:
    rendered = build_problem_html(
        {"title": "短题", "question_markdown": "求 $x+1$。"},
        fit_content=True,
    )

    assert "min-height: 100%" not in rendered


def test_render_math_text_escapes_non_math_user_content() -> None:
    rendered = render_math_text('<script>alert("x")</script> 与 $x^2$')

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "<math" in rendered


def test_render_math_text_can_render_bare_latex_in_formula_capable_fields() -> None:
    rendered = render_math_text(
        r"A=-\frac{1}{16},\quad k=2",
        allow_bare_latex=True,
    )

    assert "<math" in rendered
    assert "<mfrac>" in rendered
    assert r"\frac" not in rendered
    assert r"\quad" not in rendered


def test_render_math_text_keeps_mixed_chinese_around_bare_latex() -> None:
    rendered = render_math_text(
        r"所以 A=-\frac{1}{16}，故 k=2。",
        allow_bare_latex=True,
    )

    assert "所以" in rendered
    assert "故 k=2。" in rendered
    assert "<math" in rendered
    assert "<mfrac>" in rendered


def test_render_math_text_does_not_guess_bare_latex_without_field_opt_in() -> None:
    rendered = render_math_text(r"命令示例：\frac{1}{2}")

    assert "<math" not in rendered
    assert r"\frac{1}{2}" in rendered


def test_invalid_bare_latex_falls_back_to_readable_escaped_source() -> None:
    rendered = render_math_text(
        r'\left( <script>alert("x")</script>',
        allow_bare_latex=True,
    )

    assert "math-fallback" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert r"\left" in rendered


def test_problem_document_uses_separate_latex_only_when_question_needs_it() -> None:
    without_delimiter = build_problem_html(
        {"title": "极限", "question_markdown": "求下式", "question_latex": r"x^2+1"}
    )
    with_delimiter = build_problem_html(
        {
            "title": "极限",
            "question_markdown": r"求 \[x^2+1\]",
            "question_latex": r"x^2+1",
        }
    )

    assert "题目公式" in without_delimiter
    assert "题目公式" not in with_delimiter


def test_problem_document_renders_bare_latex_in_answer_fields() -> None:
    rendered = build_problem_html(
        {
            "title": "等价无穷小确定参数",
            "question_markdown": "求参数。",
            "user_answer": r"A=-\frac{1}{16}",
            "correct_answer": r"A=-\frac{1}{16},\quad k=2",
        }
    )

    assert "我的作答" in rendered
    assert "正确答案" in rendered
    assert rendered.count("<mfrac>") == 2
    assert r"\frac" not in rendered


def test_problem_document_hides_answers_without_leaking_source() -> None:
    rendered = build_problem_html(
        {
            "title": "题目",
            "question_markdown": r"计算 \(x+1\)",
            "correct_answer": "secret-answer",
            "solution_markdown": "secret-solution",
        },
        include_answers=False,
    )

    assert "答案与解析已隐藏" in rendered
    assert "secret-answer" not in rendered
    assert "secret-solution" not in rendered


def test_problem_document_uses_dark_theme_tokens() -> None:
    rendered = build_problem_html(
        {"title": "暗色公式", "question_markdown": r"计算 \(\frac{1}{2}\)"},
        theme="dark",
    )

    assert '<meta name="color-scheme" content="dark">' in rendered
    assert "background: #11151C" in rendered
    assert "color: #E8EDF5" in rendered
    assert "<mfrac>" in rendered


def test_note_document_renders_formula_concept_and_source_region() -> None:
    rendered = build_note_html(
        {"title": "积分公式", "summary": r"记住 \(\int x\,dx\)"},
        blocks=(
            {
                "block_type": "formula",
                "content_latex": r"\int x\,dx=\frac{x^2}{2}+C",
                "source_region": {
                    "x": 0.1,
                    "y": 0.2,
                    "width": 0.5,
                    "height": 0.25,
                },
            },
            {
                "block_type": "concept",
                "content_markdown": r"常数 \(C\) 表示任意积分常数。",
            },
        ),
        tag_names=("不定积分",),
    )

    assert "积分公式" in rendered
    assert "概念" in rendered
    assert "原图区域 10% / 20% / 50% × 25%" in rendered
    assert "不定积分" in rendered
    assert rendered.count("<math") >= 3
    assert "<mfrac>" in rendered
    assert r"\frac" not in rendered

def test_classic_problem_card_three_part_layout() -> None:
    rendered = build_problem_html(
        {
            "title": "含参数极限",
            "content_blocks": [
                {"type": "text", "content": "12. 已知"},
                {"type": "formula", "content": r"\lim_{x\to 0}\frac{1-\cos x}{x^2}"},
                {"type": "text", "content": "求 A 与 k 的值。"},
            ],
            "user_answer": "A=1",
            "correct_answer": "A=1",
            "solution_markdown": "由等价无穷小可得。",
        },
        classic=True,
    )

    assert 'class="classic-problem"' in rendered
    assert 'class="content-card problem-card"' in rendered
    assert '<h2 class="card-section-title">题目</h2>' in rendered
    assert '<div class="problem-statement">' in rendered
    assert '<div class="formula-block">' in rendered
    assert '<div class="problem-ask">' in rendered
    assert '<div class="problem-flow">' not in rendered
    body_start = rendered.index("<body")
    statement_index = rendered.index("problem-statement", body_start)
    formula_index = rendered.index("formula-block", body_start)
    ask_index = rendered.index("problem-ask", body_start)
    assert statement_index < formula_index < ask_index
    assert 'body.classic-problem .formula-block math[display="block"]' in rendered
    assert "min-height: 280px" in rendered


def test_classic_problem_card_separates_answer_cards() -> None:
    rendered = build_problem_html(
        {
            "title": "经典题",
            "content_blocks": [
                {"type": "text", "content": "题干"},
                {"type": "formula", "content": r"x^2+1"},
                {"type": "text", "content": "求值。"},
            ],
            "user_answer": "我的作答内容",
            "correct_answer": "正确答案内容",
            "solution_markdown": "解析内容",
        },
        classic=True,
    )

    assert rendered.count('class="content-card detail-card"') == 3
    assert rendered.count('class="content-card problem-card"') == 1
    assert "我的作答" in rendered
    assert "正确答案" in rendered
    assert "解析" in rendered


def test_classic_legacy_question_uses_centered_formula_card() -> None:
    rendered = build_problem_html(
        {
            "title": "极限",
            "question_markdown": "求下式",
            "question_latex": r"x^2+1",
        },
        classic=True,
    )

    assert "题目公式" not in rendered
    assert "formula-block" in rendered
    assert "problem-statement" in rendered
    assert 'class="content-card problem-card"' in rendered


def test_classic_compact_preview_uses_tight_spacing() -> None:
    rendered = build_problem_html(
        {
            "title": "预览题",
            "content_blocks": [
                {"type": "text", "content": "12. 已知"},
                {"type": "formula", "content": r"x^2+1"},
                {"type": "text", "content": "求值。"},
            ],
        },
        classic=True,
        compact=True,
    )

    # 紧凑模式：卡片不设最小高，公式上下距缩小，防止预览框留大片空白
    assert "min-height: 0px" in rendered
    assert "margin: 22px 0 26px" in rendered
    assert "padding: 18px 22px" in rendered
    assert '<div class="problem-statement">' in rendered
    assert '<div class="formula-block">' in rendered
    assert '<div class="problem-ask">' in rendered


def test_classic_disabled_by_default_keeps_compact_flow() -> None:
    rendered = build_problem_html(
        {
            "title": "普通题",
            "content_blocks": [
                {"type": "text", "content": "题干"},
                {"type": "formula", "content": r"x^2+1"},
            ],
        }
    )

    assert 'class="classic-problem"' not in rendered
    assert '<div class="problem-flow">' in rendered
    assert 'class="content-card problem-card"' not in rendered
