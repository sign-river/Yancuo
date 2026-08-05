"""Safe, offline rendering for problem text and LaTeX formulas."""

from __future__ import annotations

import html
import json
import logging
import re
import weakref
from collections.abc import Iterable, Mapping
from typing import Any

from latex2mathml import converter
from PySide6.QtCore import QBuffer, QIODevice, QMargins, QMarginsF, QSize, QSizeF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPageLayout, QPageSize, QPalette
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QApplication, QSizePolicy, QVBoxLayout, QWidget
from PySide6.QtWebEngineCore import QWebEnginePage

from yancuo_win.ui.theme import current_theme_name, get_theme_manager, theme_tokens


_logger = logging.getLogger("yancuo.math_content")


_PREVIEW_ZOOM_SCALE = 0.96
_PREVIEW_VIEWS: weakref.WeakSet["MathContentView"] = weakref.WeakSet()
_PDF_CSS_WIDTH = 794


def preview_zoom_scale() -> float:
    return _PREVIEW_ZOOM_SCALE


def set_preview_zoom_scale(scale: float) -> None:
    """Apply the shared reader scale to both existing and future previews."""

    global _PREVIEW_ZOOM_SCALE
    _PREVIEW_ZOOM_SCALE = max(0.8, min(1.5, float(scale)))
    for view in tuple(_PREVIEW_VIEWS):
        view.set_zoom_scale(_PREVIEW_ZOOM_SCALE)


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
    card_class: str = "",
) -> str:
    rendered = _render_markdown_tables(
        value,
        empty=empty,
        allow_bare_latex=allow_bare_latex,
    )
    extra = f" {card_class}" if card_class else ""
    return (
        f'<section class="content-card{extra}">'
        f"<h2>{html.escape(title)}</h2>"
        f'<div class="rich-text">{rendered}</div>'
        "</section>"
    )


def _classic_problem_card(parts: list[tuple[str, str]]) -> str:
    """Render the classic textbook 题目 card: title, statement, centered formula, ask."""
    text_indices = [index for index, (kind, _) in enumerate(parts) if kind == "text"]
    first_text = text_indices[0] if text_indices else None
    last_text = text_indices[-1] if text_indices else None
    statement: list[str] = []
    formulas: list[str] = []
    ask: list[str] = []
    for index, (kind, part) in enumerate(parts):
        if kind == "formula":
            formulas.append(f'<div class="formula-block">{part}</div>')
        elif kind == "text":
            if index == last_text and index != first_text:
                ask.append(f'<div class="problem-ask">{part}</div>')
            else:
                statement.append(f'<div class="problem-statement">{part}</div>')
        elif last_text is None or index < last_text:
            statement.append(part)
        else:
            ask.append(part)
    return (
        '<section class="content-card problem-card">'
        '<h2 class="card-section-title">题目</h2>'
        f'<div class="problem-body">{"".join(statement)}{"".join(formulas)}{"".join(ask)}</div>'
        "</section>"
    )


def _table_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _render_markdown_tables(
    value: str | None,
    *,
    empty: str,
    allow_bare_latex: bool,
) -> str:
    """Render conservative pipe tables while leaving ordinary Markdown untouched."""

    text = value or ""
    if not text.strip():
        return f'<span class="empty">{html.escape(empty)}</span>'
    lines = text.splitlines()
    output: list[str] = []
    plain: list[str] = []

    def flush_plain() -> None:
        if not plain:
            return
        output.append(
            render_math_text(
                "\n".join(plain),
                empty="",
                allow_bare_latex=allow_bare_latex,
            )
        )
        plain.clear()

    index = 0
    while index < len(lines):
        header = _table_cells(lines[index]) if "|" in lines[index] else []
        separator = (
            _table_cells(lines[index + 1])
            if index + 1 < len(lines) and "|" in lines[index + 1]
            else []
        )
        if (
            len(header) >= 2
            and len(separator) == len(header)
            and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator)
        ):
            flush_plain()
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                cells = _table_cells(lines[index])
                if len(cells) != len(header):
                    break
                rows.append(cells)
                index += 1
            head_html = "".join(
                f"<th>{render_math_text(cell, empty='', allow_bare_latex=True)}</th>"
                for cell in header
            )
            row_html = "".join(
                "<tr>"
                + "".join(
                    f"<td>{render_math_text(cell, empty='', allow_bare_latex=True)}</td>"
                    for cell in row
                )
                + "</tr>"
                for row in rows
            )
            output.append(
                f'<table class="problem-table"><thead><tr>{head_html}</tr></thead><tbody>{row_html}</tbody></table>'
            )
            continue
        plain.append(lines[index])
        index += 1
    flush_plain()
    return "".join(output)


def build_problem_html(
    fields: Mapping[str, Any],
    *,
    tag_names: Iterable[str] = (),
    include_answers: bool = True,
    show_header: bool = True,
    show_answer_notice: bool = True,
    fit_content: bool = False,
    compact: bool = False,
    classic: bool = False,
    theme: str = "light",
) -> str:
    """Build a complete, self-contained HTML problem document."""

    colors = theme_tokens(theme)
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
    raw_blocks = fields.get("content_blocks")
    if not isinstance(raw_blocks, list):
        try:
            raw_blocks = json.loads(str(fields.get("question_content_json") or "[]"))
        except json.JSONDecodeError:
            raw_blocks = []
    problem_parts: list[tuple[str, str]] = []
    for block in raw_blocks:
        if not isinstance(block, Mapping):
            continue
        kind = block.get("type")
        if kind in {"text", "formula"}:
            problem_parts.append(
                (
                    kind,
                    f'<div class="rich-text">{_render_markdown_tables(str(block.get("content") or ""), empty="", allow_bare_latex=True)}</div>',
                )
            )
        elif kind == "table" and isinstance(block.get("rows"), list):
            rendered_rows: list[str] = []
            for row in block["rows"]:
                if not isinstance(row, list):
                    continue
                cells: list[str] = []
                for raw_cell in row:
                    if isinstance(raw_cell, Mapping):
                        content = str(raw_cell.get("content") or "")
                        spans = "".join(
                            f' {name}="{max(1, min(100, int(raw_cell.get(name, 1))))}"'
                            for name in ("rowspan", "colspan")
                            if str(raw_cell.get(name, "1")).isdigit()
                            and int(raw_cell.get(name, 1)) > 1
                        )
                    else:
                        content, spans = str(raw_cell or ""), ""
                    cells.append(
                        f"<td{spans}>{render_math_text(content, empty='', allow_bare_latex=True)}</td>"
                    )
                rendered_rows.append(f"<tr>{''.join(cells)}</tr>")
            problem_parts.append(("table", f'<table class="problem-table">{"".join(rendered_rows)}</table>'))
        elif kind == "figure":
            source = str(block.get("image_data_uri") or block.get("image_src") or "")
            caption = str(block.get("content") or "题图")
            if source.startswith(("data:image/", "file:")):
                problem_parts.append(
                    (
                        "figure",
                        '<div class="figure-block">'
                        f'<img class="problem-figure" src="{html.escape(source, quote=True)}" '
                        f'alt="{html.escape(caption, quote=True)}">'
                        f'<div class="figure-caption">{html.escape(caption)}</div></div>',
                    )
                )
            else:
                problem_parts.append(("text", f'<div class="rich-text">{html.escape(caption)}</div>'))
    if problem_parts:
        if latex and not _contains_math(question, allow_bare_latex=True):
            problem_parts.append(
                ("formula", f'<div class="rich-text">{_formula_html(latex, display=True)}</div>')
            )
        if classic:
            body.append(_classic_problem_card(problem_parts))
        else:
            body.append(
                '<section class="content-card">'
                f'<div class="problem-flow">{"".join(part for _, part in problem_parts)}</div>'
                "</section>"
            )
    elif classic:
        legacy_parts: list[tuple[str, str]] = [
            (
                "text",
                f'<div class="rich-text">{_render_markdown_tables(question, empty="（空）", allow_bare_latex=True)}</div>',
            )
        ]
        if latex and not _contains_math(question, allow_bare_latex=True):
            legacy_parts.append(
                ("formula", f'<div class="rich-text">{_formula_html(latex, display=True)}</div>')
            )
        body.append(_classic_problem_card(legacy_parts))
    else:
        body.append(_section("题目", question))
        if latex and not _contains_math(question, allow_bare_latex=True):
            body.append(
                '<section class="content-card formula-card"><h2>题目公式</h2>'
                f'<div class="rich-text">{_formula_html(latex, display=True)}</div></section>'
            )

    user_answer = str(fields.get("user_answer") or "")
    correct_answer = str(fields.get("correct_answer") or "")
    solution = str(fields.get("solution_markdown") or "")
    detail_class = "detail-card" if classic else ""
    if include_answers:
        if user_answer.strip():
            body.append(_section("我的作答", user_answer, card_class=detail_class))
        body.append(_section("正确答案", correct_answer, card_class=detail_class))
        body.append(_section("解析", solution, card_class=detail_class))
    elif show_answer_notice:
        body.append(
            '<section class="answer-hidden">答案与解析已隐藏，完成思考后再显示。</section>'
        )

    notes = str(fields.get("notes") or "")
    if include_answers and notes.strip():
        body.append(_section("备注", notes, card_class="detail-card" if classic else ""))
    classic_body = ' class="classic-problem"' if classic else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="{colors.name}">
<style>
  :root {{ color-scheme: {colors.name}; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; {"" if fit_content else "min-height: 100%;"} background: {colors.bg}; color: {colors.text}; }}
  body {{
    padding: {16 if compact else 24}px;
    font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: {14 if compact else 16}px;
    line-height: {1.65 if compact else 1.8};
  }}
  .problem-header {{ margin: 0 0 {12 if compact else 18}px; }}
  .eyebrow {{ color: {colors.primary}; font-size: {12 if compact else 13}px; font-weight: 700; letter-spacing: .08em; }}
  h1 {{ margin: 4px 0 {8 if compact else 12}px; font-size: {22 if compact else 26}px; line-height: 1.35; }}
  h2 {{ margin: 0 0 {8 if compact else 12}px; font-size: {16 if compact else 17}px; line-height: 1.4; }}
  .meta-row {{ display: flex; flex-wrap: wrap; gap: {6 if compact else 8}px; }}
  .reader-meta {{ margin: 0 0 {10 if compact else 14}px; }}
  .meta-chip, .tag {{ padding: {2 if compact else 4}px {8 if compact else 10}px; border-radius: 999px; background: {colors.chip_bg}; color: {colors.chip_text}; font-size: {12 if compact else 13}px; }}
  .tag {{ background: {colors.tag_bg}; color: {colors.tag_text}; }}
  .content-card {{
    margin: 0 0 {10 if compact else 14}px; padding: {14 if compact else 18}px {16 if compact else 20}px; background: {colors.card};
    border: 1px solid {colors.border}; border-radius: 12px;
  }}
  .rich-text {{ white-space: pre-wrap; overflow-wrap: anywhere; overflow-x: auto; }}
  .problem-flow {{ display: block; }}
  .problem-flow .rich-text {{ margin: 0 0 10px; }}
  .problem-flow .rich-text:last-child {{ margin-bottom: 0; }}
  .problem-table {{ width: 100%; border-collapse: collapse; }}
  .problem-table td {{ border: 1px solid {colors.border}; padding: 7px 9px; vertical-align: top; }}
  .problem-table th {{ border: 1px solid {colors.border}; padding: 7px 9px; text-align: left; background: {colors.chip_bg}; }}
  .problem-figure {{ display: block; max-width: 100%; height: auto; margin: 4px auto 8px; }}
  .figure-caption {{ color: {colors.muted}; font-size: .9em; text-align: center; }}
  .rich-text math {{
    font-family: "Cambria Math", "STIX Two Math", serif;
    font-size: {1.08 if compact else 1.18}em;
  }}
  .rich-text math[display="block"] {{ margin: {'.55em' if compact else '.85em'} 0; text-align: left; }}
  .empty {{ color: {colors.muted}; }}
  .answer-hidden {{
    margin: 0 0 14px; padding: 14px 18px; border: 1px dashed {colors.border};
    border-radius: 10px; color: {colors.muted}; background: {colors.hidden_bg};
  }}
  .math-fallback {{ padding: 2px 5px; border-radius: 4px; background: {colors.fallback_bg}; color: {colors.fallback_text}; }}
  .math-fallback-block {{ display: block; padding: 10px; overflow-x: auto; }}
  body.classic-problem {{ padding: 28px 32px; }}
  body.classic-problem .content-card {{
    margin: 0 0 22px; padding: 30px 32px; border-radius: 16px; overflow: hidden;
  }}
  body.classic-problem .problem-card {{ min-height: 440px; }}
  body.classic-problem .card-section-title {{
    margin: 0 0 22px; font-size: 19px; line-height: 1.4; font-weight: 700;
  }}
  body.classic-problem .problem-statement,
  body.classic-problem .problem-ask {{ font-size: 17px; line-height: 1.85; }}
  body.classic-problem .problem-card .rich-text {{ overflow-x: hidden; }}
  body.classic-problem .formula-block {{
    text-align: center; margin: 58px 0 70px; max-width: 100%;
    overflow-x: auto; scrollbar-width: none;
  }}
  body.classic-problem .formula-block::-webkit-scrollbar {{ display: none; }}
  body.classic-problem .formula-block .rich-text {{ overflow: visible; }}
  body.classic-problem .formula-block math {{ font-size: 1.28em; }}
  body.classic-problem .formula-block math[display="block"] {{
    display: block; margin: 0 auto; text-align: center;
  }}
  body.classic-problem .detail-card h2 {{
    margin: 0 0 18px; font-size: 19px; line-height: 1.4; font-weight: 700;
  }}
  body.classic-problem .detail-card .rich-text {{
    font-size: 17px; line-height: 1.85; overflow-x: hidden;
  }}
  body.classic-problem .detail-card .rich-text math[display="block"] {{
    display: block; margin: 0.85em auto; text-align: center;
  }}
  body.classic-problem .meta-chip, body.classic-problem .tag {{
    padding: 5px 12px; border-radius: 999px; font-size: 13px; line-height: 1.5;
  }}
</style>
</head>
<body{classic_body}>{''.join(body)}</body>
</html>"""


def build_math_fragment_html(
    title: str,
    value: str | None,
    *,
    theme: str = "light",
) -> str:
    """Build a compact, single-field math preview for inline editing."""

    colors = theme_tokens(theme)
    content = render_math_text(value, allow_bare_latex=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="{colors.name}">
<style>
  :root {{ color-scheme: {colors.name}; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; background: {colors.card}; color: {colors.text}; }}
  body {{
    padding: 10px 12px;
    font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 13px;
    line-height: 1.55;
  }}
  .label {{ color: {colors.muted}; font-size: 12px; margin: 0 0 5px; }}
  .content {{ white-space: pre-wrap; overflow-wrap: anywhere; overflow-x: auto; }}
  .content math {{ font-family: "Cambria Math", "STIX Two Math", serif; font-size: 1.08em; }}
  .content math[display="block"] {{ margin: .5em 0; text-align: left; }}
  .empty {{ color: {colors.muted}; }}
  .math-fallback {{ padding: 2px 5px; border-radius: 4px; background: {colors.fallback_bg}; color: {colors.fallback_text}; }}
  .math-fallback-block {{ display: block; padding: 8px; overflow-x: auto; }}
</style>
</head>
<body><div class="label">{html.escape(title)}</div><div class="content">{content}</div></body>
</html>"""


def build_note_html(
    fields: Mapping[str, Any],
    *,
    blocks: Iterable[Mapping[str, Any]] = (),
    tag_names: Iterable[str] = (),
    theme: str = "light",
) -> str:
    """Build an offline note document with independently rendered content blocks."""

    colors = theme_tokens(theme)
    title = str(fields.get("title") or "未命名笔记")
    summary = str(fields.get("summary") or "")
    tags = [str(tag).strip() for tag in tag_names if str(tag).strip()]
    meta_parts: list[str] = []
    for label, key in (("科目", "subject_name"), ("章节", "chapter_name")):
        value = fields.get(key)
        if value:
            meta_parts.append(
                f'<span class="meta-chip"><b>{html.escape(label)}</b> '
                f"{html.escape(str(value))}</span>"
            )
    meta_parts.extend(f'<span class="tag">{html.escape(tag)}</span>' for tag in tags)

    rendered_blocks: list[str] = []
    for raw in blocks:
        block_type = str(raw.get("block_type") or "text")
        markdown = str(raw.get("content_markdown") or "")
        latex = str(raw.get("content_latex") or "")
        region = raw.get("source_region")
        source = ""
        if isinstance(region, Mapping) and region:
            try:
                x = round(float(region.get("x", 0)) * 100)
                y = round(float(region.get("y", 0)) * 100)
                width = round(float(region.get("width", 0)) * 100)
                height = round(float(region.get("height", 0)) * 100)
                source = (
                    '<span class="source-chip">'
                    f"原图区域 {x}% / {y}% / {width}% × {height}%"
                    "</span>"
                )
            except (TypeError, ValueError):
                source = ""
        if block_type == "heading":
            rendered_blocks.append(
                '<section class="note-heading">'
                f"<h2>{render_math_text(markdown, allow_bare_latex=True)}</h2>{source}"
                "</section>"
            )
            continue
        if block_type == "formula":
            content = _formula_html(latex or markdown, display=True)
            label = "公式"
        elif block_type == "concept":
            content = render_math_text(markdown, allow_bare_latex=True)
            label = "概念"
        elif block_type == "callout":
            content = render_math_text(markdown, allow_bare_latex=True)
            rendered_blocks.append(
                '<section class="callout-card"><div class="block-label">重点提示</div>'
                f'<div class="rich-text">{content}</div>{source}</section>'
            )
            continue
        elif block_type == "image":
            content = render_math_text(markdown, empty="图片块", allow_bare_latex=False)
            label = "图片说明"
        else:
            content = render_math_text(markdown, allow_bare_latex=True)
            label = "内容"
        rendered_blocks.append(
            '<section class="content-card">'
            f'<div class="block-label">{label}</div>'
            f'<div class="rich-text">{content}</div>{source}</section>'
        )

    if not rendered_blocks:
        rendered_blocks.append(
            '<section class="empty-note">'
            '<div class="empty-note-title">尚未添加内容</div>'
            '<div class="empty-note-hint">进入编辑模式，添加标题、正文、公式或提示。</div>'
            "</section>"
        )
    summary_html = (
        f'<p class="summary">{render_math_text(summary, empty="", allow_bare_latex=True)}</p>'
        if summary.strip()
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="{colors.name}">
<style>
  :root {{ color-scheme: {colors.name}; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; min-height: 100%; background: {colors.surface}; color: {colors.text}; }}
  body {{
    padding: 24px;
    font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 16px; line-height: 1.8;
  }}
  header {{ margin: 0 0 18px; }}
  .eyebrow, .block-label {{ color: {colors.primary}; font-size: 13px; font-weight: 700; letter-spacing: .06em; }}
  h1 {{ margin: 4px 0 8px; font-size: 28px; line-height: 1.35; }}
  h2 {{ margin: 0; font-size: 21px; line-height: 1.5; }}
  .summary {{ margin: 0 0 12px; color: {colors.muted}; white-space: pre-wrap; }}
  .meta-row {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .meta-chip, .tag, .source-chip {{
    display: inline-block; padding: 3px 9px; border-radius: 999px;
    background: {colors.chip_bg}; color: {colors.chip_text}; font-size: 12px;
  }}
  .tag {{ background: {colors.tag_bg}; color: {colors.tag_text}; }}
  .source-chip {{ margin-top: 12px; color: {colors.muted}; }}
  .content-card, .note-heading, .callout-card {{
    margin: 0 0 14px; padding: 18px 20px; background: {colors.card};
    border: 1px solid {colors.border}; border-radius: 12px;
  }}
  .note-heading {{ padding: 14px 18px; }}
  .callout-card {{ border-left: 4px solid {colors.primary}; background: {colors.hidden_bg}; }}
  .rich-text {{ white-space: pre-wrap; overflow-wrap: anywhere; overflow-x: auto; }}
  .rich-text math {{ font-family: "Cambria Math", "STIX Two Math", serif; font-size: 1.18em; }}
  .rich-text math[display="block"] {{ margin: .85em 0; text-align: left; }}
  .empty-note {{
    margin-top: 24px; padding: 52px 28px; color: {colors.muted};
    text-align: center; border: 1px dashed {colors.border}; border-radius: 12px;
    background: {colors.surface_subtle};
  }}
  .empty-note-title {{ color: {colors.text}; font-size: 16px; font-weight: 600; }}
  .empty-note-hint {{ margin-top: 6px; font-size: 13px; }}
  .empty {{ color: {colors.muted}; }}
  .math-fallback {{ padding: 2px 5px; border-radius: 4px; background: {colors.fallback_bg}; color: {colors.fallback_text}; }}
  .math-fallback-block {{ display: block; padding: 10px; overflow-x: auto; }}
</style>
</head>
<body>
<header>
  <div class="eyebrow">笔记阅读</div>
  <h1>{html.escape(title)}</h1>
  {summary_html}
  <div class="meta-row">{''.join(meta_parts)}</div>
</header>
{''.join(rendered_blocks)}
</body>
</html>"""


class MathContentView(QWidget):
    """Render formula-rich HTML off-screen and display it with native Qt PDF.

    ``QWebEngineView`` creates a Chromium child window that can briefly surface
    or steal focus on Windows.  A windowless ``QWebEnginePage`` still gives us
    accurate MathML rendering; its in-memory PDF output is displayed by
    ``QPdfView``, so navigation never creates a Chromium UI window.
    """

    content_height_changed = Signal()
    render_completed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.last_html = ""
        self._last_render: dict[str, Any] | None = None
        self._last_note_render: dict[str, Any] | None = None
        self._last_fragment_render: dict[str, str] | None = None
        self._document: QPdfDocument | None = None
        self._document_buffer: QBuffer | None = None
        self._renderer: QWebEnginePage | None = None
        self._active_html: str | None = None
        self._render_generation = 0
        self._render_scheduled = False
        self._fit_content_height = False
        self._content_sized_pdf = False
        self._content_height_limit: int | None = None
        self._reserve_content_height = False
        self._minimum_content_height = 80
        self._compact = False
        self._zoom_scale = preview_zoom_scale()
        self._content_height: int | None = None
        self._pdf_view_initialized = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._view = QPdfView(self)
        self._view.setPageMode(QPdfView.PageMode.MultiPage)
        self._view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._view.setPageSpacing(0)
        self._view.setDocumentMargins(QMargins())
        self._apply_canvas_background()
        self._view.hide()
        layout.addWidget(self._view)
        _PREVIEW_VIEWS.add(self)
        manager = get_theme_manager(QApplication.instance())
        if manager is not None:
            manager.theme_changed.connect(self._on_theme_changed)

    def set_accessible_content(self, name: str, description: str = "") -> None:
        """Expose the native PDF reader as one named keyboard focus target."""

        self.setAccessibleName(name)
        self._view.setAccessibleName(name)
        if description:
            self.setAccessibleDescription(description)
            self._view.setAccessibleDescription(description)
        self.setFocusProxy(self._view)
        self._view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def fit_to_width(self) -> None:
        """Reapply width fitting after an enclosing splitter changes size."""

        self._view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def _apply_canvas_background(self, theme: str | None = None) -> None:
        background = QColor(theme_tokens(theme or current_theme_name()).bg)
        palette = self._view.palette()
        palette.setColor(QPalette.ColorRole.Window, background)
        palette.setColor(QPalette.ColorRole.Base, background)
        palette.setColor(QPalette.ColorRole.AlternateBase, background)
        palette.setColor(QPalette.ColorRole.Dark, background)
        palette.setColor(QPalette.ColorRole.Shadow, background)
        self._view.setPalette(palette)
        self._view.setAutoFillBackground(True)
        self._view.setBackgroundRole(QPalette.ColorRole.Dark)
        self._view.setStyleSheet(
            f"QPdfView, QPdfView > QWidget {{ background: {background.name()}; }}"
        )
        viewport = getattr(self._view, "viewport", None)
        if callable(viewport):
            canvas = viewport()
            canvas.setPalette(palette)
            canvas.setAutoFillBackground(True)
            canvas.setBackgroundRole(QPalette.ColorRole.Dark)
            canvas.setStyleSheet(f"background: {background.name()};")

    def set_fit_content_height(
        self, enabled: bool = True, *, expand_widget: bool = True
    ) -> None:
        """Use a content-sized PDF, optionally growing the widget instead of scrolling."""

        self._content_sized_pdf = enabled
        self._fit_content_height = enabled and expand_widget
        self._content_height_limit = None
        if enabled:
            policy = (
                QSizePolicy.Policy.Fixed
                if expand_widget
                else QSizePolicy.Policy.Expanding
            )
            self.setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        else:
            self.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
            )
        policy = (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if self._fit_content_height
            else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._view.setVerticalScrollBarPolicy(policy)
        self._view.setHorizontalScrollBarPolicy(policy)

    def set_adaptive_content_height(
        self,
        maximum_height: int = 480,
        *,
        minimum_height: int = 80,
        reserve_height: bool = False,
    ) -> None:
        """Fit short content and scroll inside the reader once it grows long."""

        self._content_sized_pdf = True
        self._fit_content_height = True
        self._minimum_content_height = max(1, int(minimum_height))
        self._content_height_limit = max(
            self._minimum_content_height,
            int(maximum_height),
        )
        self._reserve_content_height = reserve_height
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        if reserve_height:
            self._content_height = self._content_height_limit
            self.setFixedHeight(self._content_height_limit)
        self._view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

    def set_compact(self, enabled: bool = True) -> None:
        """Use the denser reader scale for constrained workflow screens."""
        if self._compact == enabled:
            return
        self._compact = enabled
        self._render_last()

    def set_zoom_scale(self, scale: float) -> None:
        """Apply a reader-only scale without changing the host container."""
        normalized = max(0.5, min(1.5, float(scale)))
        if self._zoom_scale == normalized:
            return
        self._zoom_scale = normalized
        self._apply_zoom_scale()

    def sizeHint(self) -> QSize:  # noqa: N802
        if self._fit_content_height and self._content_height is not None:
            return QSize(0, self._content_height)
        return super().sizeHint()

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._schedule_render()

    def _schedule_render(self) -> None:
        if (
            not self.last_html
            or not self.isVisible()
            or self._renderer is not None
            or self._render_scheduled
        ):
            return
        self._render_scheduled = True
        QTimer.singleShot(0, self._start_render)

    def _start_render(self) -> None:
        self._render_scheduled = False
        if not self.last_html or not self.isVisible():
            return

        self._render_generation += 1
        generation = self._render_generation
        if self._fit_content_height:
            self._content_height = None
            if self._document is None:
                initial_height = (
                    self._content_height_limit
                    if self._reserve_content_height
                    else self._minimum_content_height
                )
                self.setFixedHeight(initial_height)

        page = QWebEnginePage(self)
        page.setBackgroundColor(QColor(theme_tokens(current_theme_name()).bg))
        page.loadFinished.connect(
            lambda ok, target=page, token=generation: self._html_loaded(
                target, token, ok
            )
        )
        self._renderer = page
        self._active_html = self.last_html
        page.setHtml(self._active_html)

    def _finish_render(self, page: QWebEnginePage, generation: int) -> None:
        if page is not self._renderer or generation != self._render_generation:
            return
        rendered_html = self._active_html
        self._renderer = None
        self._active_html = None
        page.deleteLater()
        if self.last_html != rendered_html:
            self._schedule_render()
        else:
            self.render_completed.emit()

    def scroll_position(self) -> int:
        return self._view.verticalScrollBar().value()

    def restore_scroll_position(self, value: int) -> None:
        scrollbar = self._view.verticalScrollBar()
        scrollbar.setValue(max(scrollbar.minimum(), min(int(value), scrollbar.maximum())))

    def scroll_to_bottom(self) -> None:
        scrollbar = self._view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _html_loaded(
        self, page: QWebEnginePage, generation: int, loaded: bool
    ) -> None:
        if page is not self._renderer or generation != self._render_generation:
            return
        if not loaded:
            self._finish_render(page, generation)
            return
        if self._content_sized_pdf:
            page.runJavaScript(
                f"""(() => {{
                    const width = {_PDF_CSS_WIDTH};
                    document.documentElement.style.width = `${{width}}px`;
                    document.body.style.width = `${{width}}px`;
                    return Math.ceil(Math.max(
                        document.body.scrollHeight,
                        document.body.getBoundingClientRect().height
                    ));
                }})()""",
                lambda height, target=page, token=generation: self._print_content_pdf(
                    target, token, height
                ),
            )
            return
        self._print_pdf(page, generation)

    def _print_content_pdf(self, page: QWebEnginePage, generation: int, height) -> None:  # noqa: ANN001
        if page is not self._renderer or generation != self._render_generation:
            return
        content_height = max(80, int(height or 0))
        # Chromium uses 96 CSS pixels per inch. The custom page height removes
        # the PDF reader's otherwise independent vertical viewport.
        page_size = QPageSize(
            QSizeF(210, content_height * 25.4 / 96 + 4),
            QPageSize.Unit.Millimeter,
        )
        layout = QPageLayout(
            page_size,
            QPageLayout.Orientation.Portrait,
            QMarginsF(),
        )
        self._print_pdf(page, generation, layout)

    def _print_pdf(
        self,
        page: QWebEnginePage,
        generation: int,
        layout: QPageLayout | None = None,
    ) -> None:
        def callback(data, target=page, token=generation) -> None:  # noqa: ANN001
            self._pdf_ready(target, token, data)

        if layout is None:
            page.printToPdf(callback)
        else:
            page.printToPdf(callback, layout)

    def _pdf_ready(self, page: QWebEnginePage, generation: int, data) -> None:  # noqa: ANN001
        if page is not self._renderer or generation != self._render_generation:
            return
        if not data:
            self._finish_render(page, generation)
            return

        buffer = QBuffer(self)
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        document = QPdfDocument(self)
        load_result = document.load(buffer)
        # PySide 6.8 exposes this overload as void, while older bindings return
        # QPdfDocument.Error.  In both cases pageCount confirms a usable result.
        if load_result not in (None, QPdfDocument.Error.None_) or document.pageCount() < 1:
            document.deleteLater()
            buffer.deleteLater()
            self._finish_render(page, generation)
            return

        previous_document = self._document
        previous_buffer = self._document_buffer
        self._document = document
        self._document_buffer = buffer
        self._view.setDocument(document)
        if not self._pdf_view_initialized:
            self._pdf_view_initialized = True
            self._view.show()
        self._apply_zoom_scale()
        if self._fit_content_height:
            self._update_content_height()
        if previous_document is not None:
            previous_document.deleteLater()
        if previous_buffer is not None:
            previous_buffer.deleteLater()
        self._finish_render(page, generation)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        if self._document is not None:
            self._apply_zoom_scale()

    def _apply_zoom_scale(self) -> None:
        if self._document is None:
            return
        QTimer.singleShot(0, self._apply_scaled_zoom)

    def _apply_scaled_zoom(self) -> None:
        if self._document is None or self._document.pageCount() < 1:
            return
        page = self._document.pagePointSize(0)
        if page.width() <= 0:
            return
        viewport_getter = getattr(self._view, "viewport", None)
        view_width = (
            viewport_getter().width()
            if callable(viewport_getter) and viewport_getter() is not None
            else 0
        )
        if view_width <= 0:
            view_width = max(1, self._view.width())
        # QPdfView renders 1 page point as 96/72 px at 100% (96 dpi), so the
        # fit factor that makes the page width equal the viewport is:
        #   fit = viewport / (page_pt * 96/72)
        fit_factor = view_width / (page.width() * (96.0 / 72.0))
        _logger.debug(
            "pdf zoom apply scale=%.3f fit=%.3f view_w=%d doc_w=%.1f page_h=%.1f",
            self._zoom_scale,
            fit_factor,
            view_width,
            page.width(),
            page.height(),
        )
        self._view.setZoomMode(QPdfView.ZoomMode.Custom)
        self._view.setZoomFactor(fit_factor * self._zoom_scale)
        if self._fit_content_height:
            self._update_content_height()

    def _update_content_height(self) -> None:
        if self._document is None or self._document.pageCount() < 1:
            return
        width = max(round(self.width() * self._zoom_scale), 1)
        height = 0.0
        for page_number in range(self._document.pageCount()):
            page_size = self._document.pagePointSize(page_number)
            if page_size.width() > 0:
                height += width * page_size.height() / page_size.width()
        natural_height = max(self._minimum_content_height, round(height))
        if self._reserve_content_height and self._content_height_limit is not None:
            new_height = self._content_height_limit
        else:
            new_height = (
                min(natural_height, self._content_height_limit)
                if self._content_height_limit is not None
                else natural_height
            )
        overflow = natural_height > new_height
        self._view.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if overflow
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        if new_height == self._content_height:
            return
        self._content_height = new_height
        self.setFixedHeight(new_height)
        self.updateGeometry()
        self.content_height_changed.emit()

    def set_problem(
        self,
        fields: Mapping[str, Any],
        *,
        tag_names: Iterable[str] = (),
        include_answers: bool = True,
        show_header: bool = True,
        show_answer_notice: bool = True,
        compact: bool = False,
        classic: bool = False,
    ) -> None:
        self._last_note_render = None
        self._last_fragment_render = None
        self._last_render = {
            "fields": dict(fields),
            "tag_names": tuple(tag_names),
            "include_answers": include_answers,
            "show_header": show_header,
            "show_answer_notice": show_answer_notice,
            "compact": compact,
            "classic": classic,
        }
        self._render_last()

    def set_note(
        self,
        fields: Mapping[str, Any],
        *,
        blocks: Iterable[Mapping[str, Any]] = (),
        tag_names: Iterable[str] = (),
    ) -> None:
        self._last_render = None
        self._last_fragment_render = None
        self._last_note_render = {
            "fields": dict(fields),
            "blocks": tuple(dict(block) for block in blocks),
            "tag_names": tuple(tag_names),
        }
        self._render_last()

    def set_fragment(self, title: str, value: str | None) -> None:
        """Render one field without the surrounding problem document."""
        self._last_render = None
        self._last_note_render = None
        self._last_fragment_render = {"title": title, "value": value or ""}
        self._render_last()

    def _render_last(self) -> None:
        if self._last_fragment_render is not None:
            self.last_html = build_math_fragment_html(
                self._last_fragment_render["title"],
                self._last_fragment_render["value"],
                theme=current_theme_name(),
            )
            self._schedule_render()
            return
        if self._last_note_render is not None:
            self.last_html = build_note_html(
                self._last_note_render["fields"],
                blocks=self._last_note_render["blocks"],
                tag_names=self._last_note_render["tag_names"],
                theme=current_theme_name(),
            )
            self._schedule_render()
            return
        if self._last_render is None:
            return
        self.last_html = build_problem_html(
            self._last_render["fields"],
            tag_names=self._last_render["tag_names"],
            include_answers=self._last_render["include_answers"],
            show_header=self._last_render["show_header"],
            show_answer_notice=self._last_render["show_answer_notice"],
            fit_content=self._content_sized_pdf,
            compact=self._last_render["compact"] or self._compact,
            classic=self._last_render.get("classic", False),
            theme=current_theme_name(),
        )
        self._schedule_render()

    def _on_theme_changed(self, theme: str) -> None:
        self._apply_canvas_background(theme)
        self._render_last()

    def set_message(self, title: str, message: str) -> None:
        self.set_problem(
            {"title": title, "question_markdown": message},
            include_answers=False,
            show_answer_notice=False,
        )
