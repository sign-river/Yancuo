from __future__ import annotations

from yancuo_win.ai.base import normalize_content_blocks
from yancuo_win.ui.math_content import build_problem_html


def test_content_blocks_keep_explicit_table_and_reject_untraced_figure() -> None:
    blocks = normalize_content_blocks([
        {"type": "table", "rows": [["x", "$x^2$"]]},
        {"type": "figure", "content": "missing region"},
        {"type": "figure", "content": "graph", "source_image_index": 0, "source_region": {"x": .1, "y": .2, "width": .3, "height": .4}},
    ])
    assert [block["type"] for block in blocks] == ["table", "figure"]
    html = build_problem_html({"title": "结构化题", "content_blocks": blocks})
    assert "problem-table" in html
    assert "函数图像" not in html
    assert "graph" in html
