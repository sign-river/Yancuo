"""Safe, offline rendering for problem text and LaTeX formulas."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from typing import Any

from latex2mathml import converter
from PySide6.QtCore import Qt
from PySide6.QtWebEngineWidgets import QWebEngineView


_MATH_PATTERN = re.compile(
    r"(?P<display_bracket>\\\[(?P<display_bracket_body>.*?)\\\])"
    r"|(?P<display_dollar>\$\$(?P<display_dollar_body>.*?)\$\$)"
    r"|(?P<inline_bracket>\\\((?P<inline_bracket_body>.*?)\\\))"
    r"|(?P<inline_dollar>(?<!\\)\$(?!\$)(?P<inline_dollar_body>.*?)(?<!\\)\$)",
    re.DOTALL,
)

# Conservative signal used only for fields that are expected to contain math.
# It deliberately ignores arbitrary backslashes such as Windows paths.
_BARE_LATEX_COMMAND_PATTERN = re.compile(
    r"\\(?:"
    r"begin|end|frac|dfrac|tfrac|sqrt|lim|sum|prod|int|iint|iiint|"
    r"sin|cos|tan|cot|ln|log|exp|"
    r"alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega|infty|"
    r"to|rightarrow|leftarrow|leftrightarrow|"
    r"left|right|cdot|times|div|pm|mp|leq|geq|neq|approx|sim|equiv|"
    r"quad|qquad|text|mathrm|mathbf|mathbb|mathcal|operatorname|"
    r"overline|underline|hat|bar|vec|partial|nabla"
    r")\b"
)

# Keep Chinese prose and common full-width punctuation outside formula chunks.
# A matching chunk is converted only when it also contains a known command.
_BARE_LATEX_CHUNK_PATTERN = re.compile(
    r"[^\u3400-\u9fff，。！？；：“”‘’《》【】（）]+",
    re.DOTALL,
)


def _plain_html(value: str) -> str:
    """Escape user content while retaining its intentional line layout."""

    return html.escape(value, quote=True).replace("\t", "    ")


def _formula_html(latex: str, *, display: bool) -> str:
    latex = latex.strip()
    if not latex:
        return ""
    try:
        return converter.convert(latex, display="block" if display else "inline")
    except Exception:  # latex2mathml raises several parser-specific exceptions
        kind = " math-fallback-block" if display else ""
        return f'<code class="math-fallback{kind}">{html.escape(latex)}</code>'


def _render_plain_fragment(value: str, *, allow_bare_latex: bool) -> str:
    if not allow_bare_latex or not _BARE_LATEX_COMMAND_PATTERN.search(value):
        return _plain_html(value)

    output: list[str] = []
    cursor = 0
    for match in _BARE_LATEX_CHUNK_PATTERN.finditer(value):
        chunk = match.group(0)
        if not _BARE_LATEX_COMMAND_PATTERN.search(chunk):
            continue
        output.append(_plain_html(value[cursor : match.start()]))
        leading = chunk[: len(chunk) - len(chunk.lstrip())]
        trailing = chunk[len(chunk.rstrip()) :]
        formula = chunk.strip()
        output.append(_plain_html(leading))
        output.append(_formula_html(formula, display=False))
        output.append(_plain_html(trailing))
        cursor = match.end()
    output.append(_plain_html(value[cursor:]))
    return "".join(output)


def render_math_text(
    value: str | None,
    *,
    empty: str = "（空）",
    allow_bare_latex: bool = False,
) -> str:
    """Convert math delimiters to MathML and escape every non-math fragment.

    Supported delimiters are ``\\(...\\)``, ``\\[...\\]``, ``$...$`` and
    ``$$...$$``. Invalid formula fragments fall back to readable source text
    instead of making the whole problem disappear. Formula-capable fields can
    opt into conservative bare-LaTeX detection for legacy AI output.
    """

    text = str(value or "")
    if not text.strip():
        return f'<span class="empty">{html.escape(empty)}</span>'

    output: list[str] = []
    cursor = 0
    for match in _MATH_PATTERN.finditer(text):
        output.append(
            _render_plain_fragment(
                text[cursor : match.start()],
                allow_bare_latex=allow_bare_latex,
            )
        )
        display = bool(match.group("display_bracket") or match.group("display_dollar"))
        body = next(
            group
            for group in (
                match.group("display_bracket_body"),
                match.group("display_dollar_body"),
                match.group("inline_bracket_body"),
                match.group("inline_dollar_body"),
            )
            if group is not None
        )
        output.append(_formula_html(body, display=display))
        cursor = match.end()
    output.append(
        _render_plain_fragment(
            text[cursor:],
            allow_bare_latex=allow_bare_latex,
        )
    )
    return "".join(output)


def _contains_math(value: str | None, *, allow_bare_latex: bool = False) -> bool:
    return bool(
        value
        and (
            _MATH_PATTERN.search(value)
            or (
                allow_bare_latex
                and _BARE_LATEX_COMMAND_PATTERN.search(value)
            )
        )
    )


def _section(
    title: str,
    value: str | None,
    *,
    empty: str = "（空）",
    allow_bare_latex: bool = True,
) -> str:
    return (
        '<section class="content-card">'
        f"<h2>{html.escape(title)}</h2>"
        f'<div class="rich-text">{render_math_text(value, empty=empty, allow_bare_latex=allow_bare_latex)}</div>'
        "</section>"
    )


def build_problem_html(
    fields: Mapping[str, Any],
    *,
    tag_names: Iterable[str] = (),
    include_answers: bool = True,
    show_header: bool = True,
    show_answer_notice: bool = True,
) -> str:
    """Build a complete, self-contained HTML problem document."""

    title = str(fields.get("title") or "无标题题目")
    question = str(fields.get("question_markdown") or "")
    latex = str(fields.get("question_latex") or "").strip()
    tags = [str(tag).strip() for tag in tag_names if str(tag).strip()]

    meta_parts: list[str] = []
    for label, key in (
        ("科目", "subject_name"),
        ("章节", "chapter_name"),
        ("题型", "problem_type"),
        ("来源", "source_book"),
    ):
        value = fields.get(key)
        if value:
            meta_parts.append(
                f'<span class="meta-chip"><b>{html.escape(label)}</b> '
                f"{html.escape(str(value))}</span>"
            )
    priority = fields.get("priority")
    if priority:
        meta_parts.append(
            f'<span class="meta-chip"><b>优先级</b> P{html.escape(str(priority))}</span>'
        )
    meta_parts.extend(f'<span class="tag">{html.escape(tag)}</span>' for tag in tags)

    body: list[str] = []
    if show_header:
        body.append(
            '<header class="problem-header">'
            '<div class="eyebrow">题目阅读</div>'
            f"<h1>{html.escape(title)}</h1>"
            f'<div class="meta-row">{"".join(meta_parts)}</div>'
            "</header>"
        )
    elif meta_parts:
        body.append(f'<div class="reader-meta meta-row">{"".join(meta_parts)}</div>')
    body.append(_section("题目", question))
    if latex and not _contains_math(question, allow_bare_latex=True):
        body.append(
            '<section class="content-card formula-card"><h2>题目公式</h2>'
            f'<div class="rich-text">{_formula_html(latex, display=True)}</div></section>'
        )

    user_answer = str(fields.get("user_answer") or "")
    correct_answer = str(fields.get("correct_answer") or "")
    solution = str(fields.get("solution_markdown") or "")
    if include_answers:
        if user_answer.strip():
            body.append(_section("我的作答", user_answer))
        body.append(_section("正确答案", correct_answer))
        body.append(_section("解析", solution))
    elif show_answer_notice:
        body.append(
            '<section class="answer-hidden">答案与解析已隐藏，完成思考后再显示。</section>'
        )

    error_analysis = str(fields.get("error_analysis") or "")
    notes = str(fields.get("notes") or "")
    if include_answers and error_analysis.strip():
        body.append(_section("错因分析", error_analysis))
    if include_answers and notes.strip():
        body.append(_section("备注", notes))

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light">
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; min-height: 100%; background: #f5f7fb; color: #172033; }}
  body {{
    padding: 24px;
    font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 16px;
    line-height: 1.8;
  }}
  .problem-header {{ margin: 0 0 18px; }}
  .eyebrow {{ color: #3572ff; font-size: 13px; font-weight: 700; letter-spacing: .08em; }}
  h1 {{ margin: 4px 0 12px; font-size: 26px; line-height: 1.35; }}
  h2 {{ margin: 0 0 12px; font-size: 17px; line-height: 1.4; }}
  .meta-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .reader-meta {{ margin: 0 0 14px; }}
  .meta-chip, .tag {{ padding: 4px 10px; border-radius: 999px; background: #eaf0ff; color: #315fb8; font-size: 13px; }}
  .tag {{ background: #eef1f5; color: #566074; }}
  .content-card {{
    margin: 0 0 14px; padding: 18px 20px; background: #fff;
    border: 1px solid #e1e7f0; border-radius: 12px;
  }}
  .rich-text {{ white-space: pre-wrap; overflow-wrap: anywhere; overflow-x: auto; }}
  .rich-text math {{
    font-family: "Cambria Math", "STIX Two Math", serif;
    font-size: 1.18em;
  }}
  .rich-text math[display="block"] {{ margin: .85em 0; text-align: left; }}
  .empty {{ color: #9aa3b3; }}
  .answer-hidden {{
    margin: 0 0 14px; padding: 14px 18px; border: 1px dashed #bdc8d9;
    border-radius: 10px; color: #7c8799; background: #fbfcfe;
  }}
  .math-fallback {{ padding: 2px 5px; border-radius: 4px; background: #fff3d9; color: #744b00; }}
  .math-fallback-block {{ display: block; padding: 10px; overflow-x: auto; }}
</style>
</head>
<body>{''.join(body)}</body>
</html>"""


class MathContentView(QWebEngineView):
    """Read-only embedded browser used by every formula-bearing UI."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.last_html = ""
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.page().setBackgroundColor(Qt.GlobalColor.transparent)

    def set_problem(
        self,
        fields: Mapping[str, Any],
        *,
        tag_names: Iterable[str] = (),
        include_answers: bool = True,
        show_header: bool = True,
        show_answer_notice: bool = True,
    ) -> None:
        self.last_html = build_problem_html(
            fields,
            tag_names=tag_names,
            include_answers=include_answers,
            show_header=show_header,
            show_answer_notice=show_answer_notice,
        )
        self.setHtml(self.last_html)

    def set_message(self, title: str, message: str) -> None:
        self.set_problem(
            {"title": title, "question_markdown": message},
            include_answers=False,
            show_answer_notice=False,
        )
