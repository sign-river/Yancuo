"""Local search projection, FTS, and knowledge-scope tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.search_service import SearchIndexService
from yancuo_win.application.services import AppServices, KnowledgeScope
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import SearchDocument


@pytest.fixture()
def search_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    runtime = bootstrap_runtime()
    app = AppServices(runtime)
    search = SearchIndexService(runtime)

    math = app.create_subject("高等数学")
    integral = app.create_chapter(math.id, "积分")
    double = app.create_chapter(math.id, "二重积分", parent_id=integral.id)
    derivative = app.create_chapter(math.id, "导数")
    primary = app.create_problem(
        title="二重积分换元",
        question_markdown="计算极坐标区域上的累次积分",
        status="active",
        subject_id=math.id,
        chapter_id=double.id,
    )
    tag = app.create_tag("区域换元")
    app.set_problem_tags(primary.id, [tag.id])
    other = app.create_problem(
        title="导数定义",
        question_markdown="使用差商求导",
        status="active",
        subject_id=math.id,
        chapter_id=derivative.id,
    )
    draft = app.create_problem(
        title="二重积分草稿",
        question_markdown="尚未整理的积分题",
        status="inbox",
        subject_id=math.id,
        chapter_id=double.id,
    )
    return runtime, app, search, primary, other, draft, math, integral, derivative


def test_rebuild_is_atomic_and_idempotent(search_bundle) -> None:
    runtime, _app, search, *_rest = search_bundle
    assert search.rebuild() == 3
    assert search.rebuild() == 3
    with runtime.engine.connect() as connection:
        projection_count = connection.scalar(
            select(func.count()).select_from(SearchDocument)
        )
        fts_count = connection.execute(
            text("SELECT count(*) FROM search_documents_fts")
        ).scalar_one()
    assert projection_count == 3
    assert fts_count == 3


def test_trigram_search_filters_status_and_searches_tags(search_bundle) -> None:
    _runtime, _app, search, primary, _other, draft, *_rest = search_bundle
    search.rebuild()

    active_hits = search.search("二重积分")
    assert [hit.problem_id for hit in active_hits] == [primary.id]
    assert active_hits[0].knowledge_path == "高等数学 / 积分 / 二重积分"

    inbox_hits = search.search("二重积分", statuses=("inbox",))
    assert [hit.problem_id for hit in inbox_hits] == [draft.id]
    assert [hit.problem_id for hit in search.search("区域换元")] == [primary.id]


def test_short_query_and_recursive_knowledge_scope(search_bundle) -> None:
    (
        _runtime,
        _app,
        search,
        primary,
        _other,
        _draft,
        math,
        integral,
        derivative,
    ) = search_bundle
    search.rebuild()

    short_hits = search.search("积分")
    assert [hit.problem_id for hit in short_hits] == [primary.id]

    integral_scope = KnowledgeScope(
        key="integral",
        label="高等数学 / 积分",
        subject_id=math.id,
        chapter_id=integral.id,
        include_descendants=True,
    )
    assert [hit.problem_id for hit in search.search("二重积分", scope=integral_scope)] == [
        primary.id
    ]

    derivative_scope = KnowledgeScope(
        key="derivative",
        label="高等数学 / 导数",
        subject_id=math.id,
        chapter_id=derivative.id,
        include_descendants=True,
    )
    assert search.search("二重积分", scope=derivative_scope) == ()


def test_rebuild_refreshes_stale_projection(search_bundle) -> None:
    _runtime, app, search, primary, *_rest = search_bundle
    search.rebuild()
    app.update_problem(
        primary.id,
        {
            "title": "曲面积分换元",
            "question_markdown": "计算空间曲面上的积分",
        },
    )

    assert search.search("曲面积分") == ()
    assert search.rebuild() == 3
    assert [hit.problem_id for hit in search.search("曲面积分")] == [primary.id]
