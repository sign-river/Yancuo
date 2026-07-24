"""Formula rendering stays safe and readable across all UI surfaces."""

from __future__ import annotations

from yancuo_win.ui.math_content import (
    build_note_html,
    build_problem_html,
    render_math_text,
)


def test_render_math_text_converts_inline_and_display_latex_to_mathml() -> None:
    rendered = render_math_text(
        r"已知 \[\lim_{x\to\pi}\frac{\sqrt{\sin\frac{x}{2}}-1}{A(x-\pi)^k}=1\]，"
        r"求 \(A\) 与 \(k\)。"
    )

    assert rendered.count("<math") == 3
    assert 'display="block"' in rendered
    assert "<mfrac>" in rendered
    assert r"\frac" not in rendered


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
