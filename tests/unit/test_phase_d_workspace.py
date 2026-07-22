"""阶段 D：外部工作区导出导入与冲突。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from yancuo_win.application.ai_service import AIService
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.models import ReviewItem, Version
from yancuo_win.domain.rules import DomainError
from yancuo_win.import_export.markdown_problem import parse_problem_md
from yancuo_win.import_export.workspace import WorkspaceService


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return bootstrap_runtime()


@pytest.fixture()
def bundle(runtime):
    return AppServices(runtime), AIService(runtime), WorkspaceService(runtime)


def test_export_edit_import_diff_accept(bundle, tmp_path: Path) -> None:
    services, ai, workspace = bundle
    img = tmp_path / "ws.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"workspace-img")
    pid = services.import_images([img])["created"][0]
    services.update_problem(pid, {"question_markdown": "原始题目A", "title": "题A"})

    root = workspace.export_workspace([pid], dest_dir=tmp_path / "ws1")
    assert (root / "manifest.json").is_file()
    md_path = root / "problems" / pid / "problem.md"
    text = md_path.read_text(encoding="utf-8")
    text = text.replace("原始题目A", "外部修改后的题目B")
    md_path.write_text(text, encoding="utf-8")

    result = workspace.import_workspace(root)
    assert len(result["items"]) == 1
    assert result["conflicts"] == []
    rid = result["items"][0]
    diffs = ai.review_diffs(rid)
    assert any(d["field"] == "question_markdown" and "题目B" in str(d["after"]) for d in diffs)

    ai.accept_review_item(rid)
    got = services.get_problem(pid)
    assert got is not None
    assert "题目B" in got.question_markdown
    with services.session() as s:
        versions = list(
            s.scalars(select(Version).where(Version.problem_id == pid)).all()
        )
        assert any(v.source == "workspace" for v in versions)


def test_review_accept_filters_identity_and_coerces_sync_fields(
    bundle, tmp_path: Path
) -> None:
    services, ai, workspace = bundle
    image = tmp_path / "typed.jpg"
    image.write_bytes(b"typed-review")
    pid = services.import_images([image])["created"][0]

    root = workspace.export_workspace([pid], dest_dir=tmp_path / "typed-ws")
    rid = workspace.import_workspace(root)["items"][0]
    with services.session() as s:
        item = s.get(ReviewItem, rid)
        assert item is not None
        item.proposed_json = "{"
        s.commit()
    with pytest.raises(DomainError):
        ai.accept_review_item(rid)

    with services.session() as s:
        item = s.get(ReviewItem, rid)
        assert item is not None
        item.proposed_json = json.dumps({"tags": "not-a-list"})
        s.commit()
    with pytest.raises(DomainError):
        ai.accept_review_item(rid)

    with services.session() as s:
        item = s.get(ReviewItem, rid)
        assert item is not None
        item.proposed_json = json.dumps({"next_review_at": "not-a-datetime"})
        s.commit()
    with pytest.raises(DomainError):
        ai.accept_review_item(rid)

    with services.session() as s:
        item = s.get(ReviewItem, rid)
        assert item is not None
        item.proposed_json = "[]"
        s.commit()
    with pytest.raises(DomainError):
        ai.accept_review_item(rid)

    with services.session() as s:
        item = s.get(ReviewItem, rid)
        assert item is not None
        item.proposed_json = json.dumps(
            {
                "status": "trashed",
                "deleted_at": "2026-01-02T03:04:05+00:00",
                "priority": "5",
                "id": "problem_hijack",
                "revision": 9999,
                "updated_at": "not-a-datetime",
            },
            ensure_ascii=False,
        )
        s.commit()

    ai.accept_review_item(rid)
    got = services.get_problem(pid)
    assert got is not None
    assert got.id == pid
    assert got.status == "trashed"
    assert got.priority == 5
    assert got.revision == 2
    assert isinstance(got.deleted_at, datetime)


def test_conflict_when_internal_changed(bundle, tmp_path: Path) -> None:
    services, ai, workspace = bundle
    img = tmp_path / "c.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"conflict-img")
    pid = services.import_images([img])["created"][0]
    services.update_problem(pid, {"question_markdown": "导出前"})

    root = workspace.export_workspace([pid], dest_dir=tmp_path / "ws2")
    # 外部改
    md_path = root / "problems" / pid / "problem.md"
    md_path.write_text(
        md_path.read_text(encoding="utf-8").replace("导出前", "外部版本"),
        encoding="utf-8",
    )
    # 内部也改 → revision 升高
    services.update_problem(pid, {"question_markdown": "内部版本"})

    result = workspace.import_workspace(root)
    assert len(result["conflicts"]) == 1
    rid = result["conflicts"][0]
    item = ai.get_review_item(rid)
    assert item is not None
    assert item.status == "conflict"

    with pytest.raises(DomainError):
        ai.accept_review_item(rid)  # 非 force 不可

    # 保留内部
    ai.reject_review_item(rid)
    assert services.get_problem(pid).question_markdown == "内部版本"

    # 再导入一次并强制外部
    result2 = workspace.import_workspace(root)
    rid2 = result2["conflicts"][0]
    ai.accept_review_item(rid2, force=True)
    assert "外部版本" in services.get_problem(pid).question_markdown


def test_missing_asset_fails(bundle, tmp_path: Path) -> None:
    services, _ai, workspace = bundle
    img = tmp_path / "m.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"missing-asset")
    pid = services.import_images([img])["created"][0]
    root = workspace.export_workspace([pid], dest_dir=tmp_path / "ws3")
    assets = root / "problems" / pid / "assets"
    for f in assets.iterdir():
        f.chmod(0o666)
        f.unlink()
    with pytest.raises(DomainError):
        workspace.import_workspace(root)


def test_invalid_manifest(bundle, tmp_path: Path) -> None:
    _services, _ai, workspace = bundle
    bad = tmp_path / "badws"
    bad.mkdir()
    (bad / "manifest.json").write_text(
        json.dumps({"format": "other", "format_version": 1}), encoding="utf-8"
    )
    with pytest.raises(DomainError):
        workspace.import_workspace(bad)


def test_parse_problem_md_roundtrip() -> None:
    from yancuo_win.import_export.markdown_problem import render_problem_md

    md = render_problem_md(
        front={"id": "problem_x", "revision": 2, "priority": 4, "title": "T", "tags": ["a"]},
        sections={"question_markdown": "Q", "correct_answer": "A"},
    )
    fm, sections = parse_problem_md(md)
    assert fm["id"] == "problem_x"
    assert fm["revision"] == 2
    assert sections["question_markdown"] == "Q"
    assert sections["correct_answer"] == "A"
