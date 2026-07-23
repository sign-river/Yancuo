"""阶段 B：错题服务、对象库、备份与导出。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy import select

from yancuo_win.application.ai_service import AIService
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices, ProblemFilter
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import AiJob, AiJobItem, Asset, ReviewItem
from yancuo_win.domain.rules import DomainError


@pytest.fixture()
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppServices:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setenv("YANCUO_AI__DEFAULT_PROVIDER", "mock")
    return AppServices(bootstrap_runtime())


def test_problem_lifecycle_and_trash(services: AppServices) -> None:
    sub = services.create_subject("高等数学")
    ch = services.create_chapter(sub.id, "极限")
    p = services.create_problem(title="例1", subject_id=sub.id, chapter_id=ch.id)
    assert p.status == "inbox"
    services.update_problem(p.id, {"question_markdown": "求极限", "priority": 5})
    services.promote_to_active(p.id)
    got = services.get_problem(p.id)
    assert got is not None
    assert got.status == "active"
    assert got.priority == 5
    assert got.revision >= 2

    services.trash_problem(p.id)
    assert services.get_problem(p.id).status == "trashed"
    services.restore_problem(p.id)
    assert services.get_problem(p.id).status == "inbox"

    services.trash_problem(p.id)
    assert services.purge_trashed() == 1
    assert services.get_problem(p.id) is None


def test_import_image_dedup_and_immutable(
    services: AppServices, tmp_path: Path
) -> None:
    img = tmp_path / "a.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"fake-jpeg-content-001")
    r1 = services.import_images([img])
    assert len(r1["created"]) == 1
    r2 = services.import_images([img])
    assert len(r2["created"]) == 0
    assert len(r2["skipped"]) == 1

    problem = services.get_problem(r1["created"][0])
    assert problem is not None
    assert problem.assets
    asset = problem.assets[0]
    assert asset.role == "original"
    assert asset.is_immutable is True
    with pytest.raises(DomainError):
        services.try_overwrite_original(asset.id)


def test_trashed_image_can_be_reimported_without_deleting_shared_object(
    services: AppServices, tmp_path: Path
) -> None:
    image = tmp_path / "retry.jpg"
    image.write_bytes(b"\xff\xd8\xff" + b"retry-after-trash")

    first_id = services.import_images([image])["created"][0]
    first = services.get_problem(first_id)
    assert first is not None
    object_path = services.store.resolve(first.assets[0].relative_path)
    services.trash_problem(first_id)

    second = services.import_images([image])
    assert len(second["created"]) == 1
    second_id = second["created"][0]
    assert second["skipped"] == []

    assert services.purge_trashed() == 1
    assert services.get_problem(first_id) is None
    assert services.get_problem(second_id) is not None
    assert object_path.is_file()


def test_purge_trashed_removes_ai_dependencies_and_orphan_file(
    services: AppServices, tmp_path: Path
) -> None:
    image = tmp_path / "ai-trash.jpg"
    image.write_bytes(b"\xff\xd8\xff" + b"ai-trash-with-dependencies")
    problem_id = services.import_images([image])["created"][0]
    problem = services.get_problem(problem_id)
    assert problem is not None
    asset_id = problem.assets[0].id
    object_path = services.store.resolve(problem.assets[0].relative_path)

    ai = AIService(services.runtime)
    job = ai.create_structure_job([problem_id])
    ai.run_job(job.id)
    assert ai.list_review_items_for_job(job.id)

    services.trash_problem(problem_id)
    assert services.purge_trashed() == 1

    assert services.get_problem(problem_id) is None
    assert not object_path.exists()
    with services.session() as session:
        assert session.get(Asset, asset_id) is None
        assert session.get(AiJob, job.id) is None
        assert session.scalar(
            select(AiJobItem).where(AiJobItem.problem_id == problem_id)
        ) is None
        assert session.scalar(
            select(ReviewItem).where(ReviewItem.problem_id == problem_id)
        ) is None


def test_search_filter_and_tags(services: AppServices) -> None:
    services.create_problem(title="换元积分", question_markdown="计算积分")
    services.create_problem(title="矩阵秩", question_markdown="求秩")
    tag = services.create_tag("高频")
    problems = services.list_problems(ProblemFilter(status="library", query="积分"))
    assert len(problems) == 1
    services.set_problem_tags(problems[0].id, [tag.id])
    tagged = services.list_problems(ProblemFilter(status="library", tag_id=tag.id))
    assert len(tagged) == 1


def test_backup_restore_and_word_export(
    services: AppServices, tmp_path: Path
) -> None:
    img = tmp_path / "b.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"payload-xyz")
    created = services.import_images([img])["created"]
    pid = created[0]
    services.update_problem(pid, {"question_markdown": "原题内容A", "correct_answer": "42"})

    backup = services.create_backup(tmp_path / "bak.zip")
    assert backup.is_file()

    restore_root = tmp_path / "restored"
    services.restore_backup(backup, restore_root)
    assert (restore_root / "error_book.db").is_file()
    assert any((restore_root / "assets").rglob("*"))

    docx_path = tmp_path / "out.docx"
    services.export_problems_docx([pid], docx_path)
    assert docx_path.is_file()
    doc = Document(str(docx_path))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "原题内容A" in text
    assert "42" in text


def test_chapter_template_roundtrip(services: AppServices, tmp_path: Path) -> None:
    sub = services.create_subject("线性代数")
    services.create_chapter(sub.id, "行列式")
    services.create_chapter(sub.id, "矩阵")
    tpl = tmp_path / "chapters.json"
    services.export_chapter_template(sub.id, tpl)
    # 再导入到同名科目应跳过已有章节且不报错
    services.import_chapter_template(tpl)
    assert len(services.list_chapters(sub.id)) == 2


def test_chapter_template_v2_preserves_duplicate_names_in_different_paths(
    services: AppServices,
    tmp_path: Path,
) -> None:
    subject = services.create_subject("目录源")
    basic = services.create_chapter(subject.id, "基础", sort_order=1)
    advanced = services.create_chapter(subject.id, "进阶", sort_order=2)
    services.create_chapter(subject.id, "通用", parent_id=basic.id)
    services.create_chapter(subject.id, "通用", parent_id=advanced.id)

    template = services.export_chapter_template(subject.id, tmp_path / "tree.json")
    payload = json.loads(template.read_text(encoding="utf-8"))
    assert payload["version"] == 2
    assert {
        (tuple(item["parent_path"]), item["name"])
        for item in payload["chapters"]
        if item["name"] == "通用"
    } == {
        (("基础",), "通用"),
        (("进阶",), "通用"),
    }

    payload["subject"]["name"] = "目录副本"
    template.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    imported_subject_id = services.import_chapter_template(template)
    imported = services.list_chapter_tree(imported_subject_id, problem_status=None)
    assert [node.name for node in imported] == ["基础", "进阶"]
    assert imported[0].children[0].path_names == ("基础", "通用")
    assert imported[1].children[0].path_names == ("进阶", "通用")


def test_recursive_chapter_tree_paths_counts_and_descendant_filter(
    services: AppServices,
) -> None:
    subject = services.create_subject("高等数学")
    limit = services.create_chapter(subject.id, "极限", sort_order=1)
    integral = services.create_chapter(subject.id, "积分", sort_order=2)
    double = services.create_chapter(
        subject.id,
        "二重积分",
        parent_id=integral.id,
        sort_order=1,
    )
    line = services.create_chapter(
        subject.id,
        "曲线积分",
        parent_id=integral.id,
        sort_order=2,
    )

    services.create_problem(
        title="极限题",
        status="active",
        subject_id=subject.id,
        chapter_id=limit.id,
    )
    services.create_problem(
        title="积分基础题",
        status="active",
        subject_id=subject.id,
        chapter_id=integral.id,
    )
    services.create_problem(
        title="二重积分题",
        status="active",
        subject_id=subject.id,
        chapter_id=double.id,
    )
    services.create_problem(
        title="待整理曲线积分",
        status="inbox",
        subject_id=subject.id,
        chapter_id=line.id,
    )

    roots = services.list_chapter_tree(subject.id)
    assert [node.name for node in roots] == ["极限", "积分"]
    integral_node = roots[1]
    assert integral_node.path_label == "积分"
    assert integral_node.direct_problem_count == 1
    assert integral_node.total_problem_count == 2
    assert [node.name for node in integral_node.children] == ["二重积分", "曲线积分"]
    assert integral_node.children[0].path_names == ("积分", "二重积分")
    assert integral_node.children[0].depth == 1
    assert integral_node.children[0].total_problem_count == 1
    assert integral_node.children[1].total_problem_count == 0

    problems = services.list_problems(
        ProblemFilter(
            status="active",
            chapter_id=integral.id,
            include_descendant_chapters=True,
        )
    )
    assert {problem.title for problem in problems} == {"积分基础题", "二重积分题"}


def test_chapter_maintenance_rejects_invalid_hierarchy(services: AppServices) -> None:
    math = services.create_subject("高等数学")
    algebra = services.create_subject("线性代数")
    integral = services.create_chapter(math.id, "积分")
    double = services.create_chapter(math.id, "二重积分", parent_id=integral.id)
    foreign_parent = services.create_chapter(algebra.id, "矩阵")

    with pytest.raises(DomainError, match="同一科目"):
        services.create_chapter(math.id, "非法章节", parent_id=foreign_parent.id)
    with pytest.raises(DomainError, match="同一层级"):
        services.create_chapter(math.id, "积分")
    with pytest.raises(DomainError, match="自己的下级"):
        services.move_chapter(integral.id, double.id)
    with pytest.raises(DomainError, match="子章节"):
        services.delete_chapter(integral.id)

    services.create_problem(
        title="二重积分题",
        subject_id=math.id,
        chapter_id=double.id,
    )
    with pytest.raises(DomainError, match="仍有题目"):
        services.delete_chapter(double.id)

    empty = services.create_chapter(math.id, "空章节")
    services.rename_chapter(empty.id, "待分类")
    moved = services.move_chapter(empty.id, integral.id, sort_order=9)
    assert moved.parent_id == integral.id
    assert moved.sort_order == 9
    services.delete_chapter(empty.id)
    assert all(chapter.id != empty.id for chapter in services.list_chapters(math.id))


def test_catalog_choices_reordering_and_problem_category_move(
    services: AppServices,
) -> None:
    math = services.create_subject("高等数学", sort_order=1)
    algebra = services.create_subject("线性代数", sort_order=2)
    integral = services.create_chapter(math.id, "积分", sort_order=1)
    double = services.create_chapter(
        math.id,
        "二重积分",
        parent_id=integral.id,
    )
    derivative = services.create_chapter(math.id, "导数", sort_order=2)
    problem = services.create_problem(
        title="待移动题",
        status="active",
        subject_id=math.id,
        chapter_id=double.id,
    )

    labels = [choice.label for choice in services.list_category_choices()]
    assert "高等数学 / 积分 / 二重积分" in labels
    assert "高等数学 / 未分类" in labels

    services.reorder_subject(algebra.id, -1)
    assert [subject.id for subject in services.list_subjects()][:2] == [
        algebra.id,
        math.id,
    ]
    services.reorder_chapter(derivative.id, -1)
    assert [chapter.id for chapter in services.list_chapters(math.id) if chapter.parent_id is None][
        :2
    ] == [derivative.id, integral.id]

    assert (
        services.move_problems_to_category(
            [problem.id],
            subject_id=math.id,
            chapter_id=None,
        )
        == 1
    )
    moved = services.get_problem(problem.id)
    assert moved.subject_id == math.id
    assert moved.chapter_id is None

    with pytest.raises(DomainError, match="不属于"):
        services.move_problems_to_category(
            [problem.id],
            subject_id=algebra.id,
            chapter_id=double.id,
        )

    services.move_problems_to_category(
        [problem.id],
        subject_id=None,
        chapter_id=None,
    )
    moved = services.get_problem(problem.id)
    assert moved.subject_id is None
    assert moved.chapter_id is None

    scopes = services.list_knowledge_scopes()
    assert any(scope.label == "高等数学 / 积分 / 二重积分" for scope in scopes)
    with pytest.raises(DomainError, match="天数"):
        services.list_problems(
            ProblemFilter(status="active", created_within_days=0)
        )
