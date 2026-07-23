"""阶段 B：错题服务、对象库、备份与导出。"""

from __future__ import annotations

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
