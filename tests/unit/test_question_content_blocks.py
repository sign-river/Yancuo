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


def test_content_blocks_preserve_merged_cells_and_render_spans() -> None:
    blocks = normalize_content_blocks(
        [
            {
                "type": "table",
                "rows": [
                    [{"content": "$x^2$", "rowspan": 2, "colspan": 2}],
                    ["结果"],
                ],
            }
        ]
    )
    assert blocks[0]["rows"][0][0]["rowspan"] == 2
    html = build_problem_html({"content_blocks": blocks})
    assert 'rowspan="2"' in html
    assert 'colspan="2"' in html


def test_legacy_markdown_pipe_table_renders_as_table() -> None:
    html = build_problem_html(
        {
            "question_markdown": "| x | $x^2$ |\n| --- | ---: |\n| 2 | 4 |"
        }
    )

    assert '<table class="problem-table">' in html
    assert "<th>x</th>" in html
    assert "<td>2</td>" in html
