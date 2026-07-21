"""工作区 problem.md front matter 与分区解析（无第三方 YAML 依赖）。"""

from __future__ import annotations

import re
from typing import Any


SECTION_MAP = {
    "原题": "question_markdown",
    "我的错误过程": "user_answer",
    "正确答案": "correct_answer",
    "正确解法": "solution_markdown",
    "核心公式": "question_latex",
    "错因": "error_analysis",
    "备注": "notes",
}


def parse_problem_md(text: str) -> tuple[dict[str, Any], dict[str, str]]:
    """返回 (front_matter, sections)。"""
    text = text.replace("\r\n", "\n")
    fm: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end].strip("\n")
            body = text[end + 4 :].lstrip("\n")
            fm = _parse_front_matter(block)

    sections: dict[str, str] = {}
    parts = re.split(r"(?m)^#\s+", body)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.split("\n", 1)
        title = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else ""
        field = SECTION_MAP.get(title)
        if field:
            sections[field] = content
    return fm, sections


def render_problem_md(
    *,
    front: dict[str, Any],
    sections: dict[str, str],
) -> str:
    lines = ["---"]
    for key in ("id", "revision", "priority", "title", "status"):
        if key in front and front[key] is not None:
            lines.append(f"{key}: {front[key]}")
    tags = front.get("tags") or []
    if tags:
        lines.append("tags:")
        for t in tags:
            lines.append(f"  - {t}")
    lines.append("---")
    lines.append("")
    order = [
        ("原题", "question_markdown"),
        ("我的错误过程", "user_answer"),
        ("正确答案", "correct_answer"),
        ("正确解法", "solution_markdown"),
        ("核心公式", "question_latex"),
        ("错因", "error_analysis"),
        ("备注", "notes"),
    ]
    for title, field in order:
        lines.append(f"# {title}")
        lines.append("")
        lines.append(sections.get(field, "") or "")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_front_matter(block: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if key == "tags" and raw == "":
            tags: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip().startswith("-"):
                tags.append(lines[i].strip()[1:].strip())
                i += 1
            result["tags"] = tags
            continue
        if key in {"revision", "priority"}:
            try:
                result[key] = int(raw)
            except ValueError:
                result[key] = raw
        else:
            result[key] = raw
        i += 1
    return result
