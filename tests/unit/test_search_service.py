"""Local search projection, FTS, and knowledge-scope tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.search_service import SearchIndexService
from yancuo_win.application.services import AppServices, KnowledgeScope
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import Problem, SearchDocument
from yancuo_win.data.migrate import ensure_search_index_schema


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


def test_problem_update_refreshes_projection_automatically(search_bundle) -> None:
    _runtime, app, search, primary, *_rest = search_bundle
    search.rebuild()
    app.update_problem(
        primary.id,
        {
            "title": "曲面积分换元",
            "question_markdown": "计算空间曲面上的积分",
        },
    )

    assert [hit.problem_id for hit in search.search("曲面积分")] == [primary.id]
    assert search.check_consistency().is_consistent


def test_status_tags_category_and_purge_stay_in_sync(search_bundle) -> None:
    (
        _runtime,
        app,
        search,
        primary,
        _other,
        _draft,
        math,
        _integral,
        derivative,
    ) = search_bundle
    search.rebuild()

    tag = app.create_tag("旋转曲面")
    app.set_problem_tags(primary.id, [tag.id])
    assert [hit.problem_id for hit in search.search("旋转曲面")] == [primary.id]

    app.move_problems_to_category(
        [primary.id],
        subject_id=math.id,
        chapter_id=derivative.id,
    )
    derivative_scope = KnowledgeScope(
        key="derivative",
        label="高等数学 / 导数",
        subject_id=math.id,
        chapter_id=derivative.id,
        include_descendants=True,
    )
    assert [hit.problem_id for hit in search.search("二重积分", scope=derivative_scope)] == [
        primary.id
    ]

    app.rename_chapter(derivative.id, "微分学")
    renamed = search.search("二重积分", scope=derivative_scope)
    assert renamed[0].knowledge_path == "高等数学 / 微分学"

    app.trash_problem(primary.id)
    assert search.search("二重积分") == ()
    assert [hit.problem_id for hit in search.search("二重积分", statuses=("trashed",))] == [
        primary.id
    ]
    app.restore_problem(primary.id, to_status="active")
    assert [hit.problem_id for hit in search.search("二重积分")] == [primary.id]
    app.trash_problem(primary.id)
    assert app.purge_trashed() == 1
    assert search.search("二重积分", statuses=("trashed",)) == ()
    assert search.check_consistency().is_consistent


def test_direct_orm_writes_are_indexed_for_import_and_sync_paths(search_bundle) -> None:
    runtime, _app, search, *_rest = search_bundle
    with runtime.session_factory() as session:
        problem = Problem(
            id="problem_external_write",
            status="active",
            title="外部同步矩阵题",
            question_markdown="求特征向量与特征空间",
        )
        session.add(problem)
        session.commit()

    hits = search.search("特征向量")
    assert [hit.problem_id for hit in hits] == ["problem_external_write"]

    with runtime.session_factory() as session:
        problem = session.get(Problem, "problem_external_write")
        assert problem is not None
        session.delete(problem)
        session.commit()
    assert search.search("特征向量") == ()


def test_consistency_diagnosis_repairs_projection_and_fts(search_bundle) -> None:
    runtime, _app, search, primary, *_rest = search_bundle
    search.rebuild()
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE search_documents SET title='损坏投影' "
                "WHERE problem_id=:problem_id"
            ),
            {"problem_id": primary.id},
        )
        connection.execute(
            text(
                "UPDATE search_documents_fts SET title='损坏索引' "
                "WHERE problem_id=:problem_id"
            ),
            {"problem_id": primary.id},
        )

    damaged = search.check_consistency()
    assert primary.id in damaged.stale_problem_ids
    assert primary.id in damaged.stale_fts_problem_ids
    repaired = search.repair_if_needed()
    assert repaired.is_consistent
    assert [hit.problem_id for hit in search.search("二重积分")] == [primary.id]


def test_index_failure_rolls_back_canonical_problem_update(search_bundle) -> None:
    runtime, app, search, primary, *_rest = search_bundle
    search.rebuild()
    original_title = app.get_problem(primary.id).title
    with runtime.engine.begin() as connection:
        connection.execute(text("DROP TABLE search_documents_fts"))

    with pytest.raises(SQLAlchemyError):
        app.update_problem(primary.id, {"title": "不应提交的标题"})

    with runtime.engine.connect() as connection:
        title = connection.execute(
            text("SELECT title FROM problems WHERE id=:problem_id"),
            {"problem_id": primary.id},
        ).scalar_one()
    assert title == original_title

    ensure_search_index_schema(runtime.engine)
    assert search.repair_if_needed().is_consistent


def test_bootstrap_repairs_out_of_band_index_drift(search_bundle) -> None:
    runtime, _app, _search, primary, *_rest = search_bundle
    with runtime.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE problems SET title='启动自动修复题目' "
                "WHERE id=:problem_id"
            ),
            {"problem_id": primary.id},
        )
        connection.execute(
            text(
                "DELETE FROM search_documents_fts WHERE problem_id=:problem_id"
            ),
            {"problem_id": primary.id},
        )
    runtime.engine.dispose()

    repaired_runtime = bootstrap_runtime()
    repaired_search = SearchIndexService(repaired_runtime)
    assert repaired_search.check_consistency().is_consistent
    assert [
        hit.problem_id for hit in repaired_search.search("启动自动修复")
    ] == [primary.id]
