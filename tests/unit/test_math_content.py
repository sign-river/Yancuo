"""Formula rendering stays safe and readable across all UI surfaces."""

from __future__ import annotations

from yancuo_win.ui.math_content import build_problem_html, render_math_text


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
