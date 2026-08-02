"""阶段 K：.gmshare 脱敏分享与 origin 去重。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

import yancuo_win.import_export.gmshare as gmshare_module
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import Asset, SyncOperation
from yancuo_win.domain.rules import DomainError
from yancuo_win.import_export.gmshare import HARD_DENY_FIELDS, GmshareService


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return bootstrap_runtime()


def _rewrite_entry(source: Path, destination: Path, name: str, payload: bytes) -> Path:
    with (
        zipfile.ZipFile(source, "r") as incoming,
        zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as outgoing,
    ):
        for item in incoming.infolist():
            outgoing.writestr(
                item, payload if item.filename == name else incoming.read(item)
            )
    return destination


def test_gmshare_excludes_private_fields(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    pid = services.create_problem(title="分享题").id
    services.update_problem(
        pid,
        {
            "question_markdown": "题目正文",
            "correct_answer": "答案",
            "solution_markdown": "解析公开",
            "user_answer": "我的错误过程",
            "notes": "私人备注勿分享",
            "mastery": 2,
        },
    )
    share = GmshareService(runtime)
    result = share.export_share([pid], dest=tmp_path / "out.gmshare", title="测试分享")
    assert result.path.is_file()

    with zipfile.ZipFile(result.path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["format"] == "graduate-mistake-book-gmshare"
        assert manifest["includes"]["user_answer"] is False
        assert manifest["includes"]["notes"] is False
        assert manifest["includes"]["review_history"] is False
        body = zf.read("problems.jsonl").decode("utf-8")
        line = json.loads(body.strip().splitlines()[0])
        for bad in HARD_DENY_FIELDS:
            assert bad not in line
        assert "user_answer" not in line
        assert "notes" not in line
        assert "我的错误过程" not in body
        assert "私人备注" not in body
        assert line["solution_markdown"] == "解析公开"
        assert "identity.json" not in zf.namelist()


def test_gmshare_roundtrips_only_derived_question_figures(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    problem = services.create_problem(title="带题图")
    source = tmp_path / "source.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nsource")
    crop = tmp_path / "crop.png"
    crop.write_bytes(b"\x89PNG\r\n\x1a\ncrop")
    original = services.store.store_copy(source, role="original")
    derived = services.store.store_copy(crop, role="derived_figure")
    derived_id = new_id("asset")
    with services.session() as session:
        session.add_all(
            [
                Asset(
                    id=new_id("asset"),
                    problem_id=problem.id,
                    role="original",
                    sha256=original.sha256,
                    relative_path=original.relative_path,
                    mime_type=original.mime_type,
                    size_bytes=original.size_bytes,
                    is_immutable=True,
                ),
                Asset(
                    id=derived_id,
                    problem_id=problem.id,
                    role="derived_figure",
                    sha256=derived.sha256,
                    relative_path=derived.relative_path,
                    mime_type=derived.mime_type,
                    size_bytes=derived.size_bytes,
                    is_immutable=True,
                ),
            ]
        )
        session.commit()
    services.update_problem(
        problem.id,
        {
            "question_content_json": json.dumps(
                [
                    {
                        "type": "figure",
                        "derived_asset_id": derived_id,
                        "source_asset_id": "must-not-leak",
                        "source_region": {"x": 0, "y": 0, "width": 1, "height": 1},
                    }
                ]
            )
        },
    )

    share = GmshareService(runtime)
    pack = share.export_share([problem.id], dest=tmp_path / "figure.gmshare")
    assert pack.asset_count == 1
    with zipfile.ZipFile(pack.path, "r") as archive:
        assert b"must-not-leak" not in archive.read("problems.jsonl")
        assert source.read_bytes() not in [
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("assets/objects/")
        ]

    imported = share.import_share(pack.path)
    imported_problem = services.get_problem(imported.created_ids[0])
    assert imported_problem is not None
    assert [asset.role for asset in imported_problem.assets] == ["derived_figure"]
    imported_blocks = json.loads(imported_problem.question_content_json)
    assert imported_blocks[0]["derived_asset_id"] == imported_problem.assets[0].id
    assert "source_region" not in imported_blocks[0]


def test_gmshare_export_streams_problems_in_bounded_batches(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = AppServices(runtime)
    for index in range(3):
        services.create_problem(title=f"分批分享题 {index}")
    batch_sizes: list[int] = []
    original_batches = GmshareService._problem_batches

    def record_batches(session, statement):
        for batch in original_batches(session, statement):
            batch_sizes.append(len(batch))
            yield batch

    monkeypatch.setattr(gmshare_module, "_PROBLEM_EXPORT_BATCH_SIZE", 2)
    monkeypatch.setattr(
        GmshareService, "_problem_batches", staticmethod(record_batches)
    )

    result = GmshareService(runtime).export_share(dest=tmp_path / "batched.gmshare")

    assert result.problem_count == 3
    assert batch_sizes == [2, 1]
    with zipfile.ZipFile(result.path, "r") as zf:
        assert len(zf.read("problems.jsonl").splitlines()) == 3


def test_gmshare_export_rejects_package_over_problem_budget(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = AppServices(runtime)
    for index in range(3):
        services.create_problem(title=f"超限分享题 {index}")
    destination = tmp_path / "oversized.gmshare"
    monkeypatch.setattr(gmshare_module, "MAX_SHARE_PROBLEMS", 2)

    with pytest.raises(DomainError, match="分享题目数超过上限"):
        GmshareService(runtime).export_share(dest=destination)

    assert not destination.exists()
    assert list(runtime.paths.cache_dir.glob("gmshare-export-*")) == []


def test_gmshare_export_failure_preserves_existing_destination(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem_id = AppServices(runtime).create_problem(title="atomic share").id
    destination = tmp_path / "existing.gmshare"
    destination.write_bytes(b"previous-share")

    def fail_write(
        self, filename, arcname=None, compress_type=None, compresslevel=None
    ):
        del self, filename, arcname, compress_type, compresslevel
        raise OSError("simulated gmshare write failure")

    monkeypatch.setattr(zipfile.ZipFile, "write", fail_write)

    with pytest.raises(OSError, match="simulated"):
        GmshareService(runtime).export_share([problem_id], dest=destination)

    assert destination.read_bytes() == b"previous-share"
    assert list(tmp_path.glob(".existing.gmshare.*.tmp")) == []
    assert list(runtime.paths.cache_dir.glob("gmshare-export-*")) == []


def test_gmshare_import_dedup(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = AppServices(runtime)
    pid = services.create_problem(title="去重题").id
    services.update_problem(pid, {"question_markdown": "Q"})
    share = GmshareService(runtime)
    pack = share.export_share([pid], dest=tmp_path / "dedup.gmshare").path

    first = share.import_share(pack)
    assert first.created == 1
    assert first.skipped_duplicates == 0
    with runtime.session_factory() as s:
        imported_create = s.scalar(
            select(SyncOperation).where(
                SyncOperation.entity_id == first.created_ids[0],
                SyncOperation.operation == "create",
            )
        )
        assert imported_create is not None
    second = share.import_share(pack)
    assert second.created == 0
    assert second.skipped_duplicates == 1


def test_gmshare_rejects_tampered_problem_payload(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    problem_id = services.create_problem(title="完整性题").id
    share = GmshareService(runtime)
    source = share.export_share([problem_id], dest=tmp_path / "source.gmshare").path
    with zipfile.ZipFile(source, "r") as zf:
        row = json.loads(zf.read("problems.jsonl"))
    row["title"] = "被篡改"
    tampered = _rewrite_entry(
        source,
        tmp_path / "tampered.gmshare",
        "problems.jsonl",
        (json.dumps(row, ensure_ascii=False) + "\n").encode(),
    )

    with pytest.raises(DomainError, match="校验失败"):
        share.import_share(tampered)


def test_gmshare_rejects_incomplete_checksum_coverage(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    problem_id = services.create_problem(title="覆盖题").id
    share = GmshareService(runtime)
    source = share.export_share([problem_id], dest=tmp_path / "source.gmshare").path
    with zipfile.ZipFile(source, "r") as zf:
        checksums = zf.read("checksums.sha256").decode()
    incomplete = "\n".join(
        line for line in checksums.splitlines() if not line.endswith("  problems.jsonl")
    )
    pack = _rewrite_entry(
        source,
        tmp_path / "incomplete.gmshare",
        "checksums.sha256",
        (incomplete + "\n").encode(),
    )

    with pytest.raises(DomainError, match="未完整覆盖"):
        share.import_share(pack)


def test_gmshare_rejects_oversized_manifest_before_json_decode(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem_id = AppServices(runtime).create_problem(title="metadata budget").id
    share = GmshareService(runtime)
    pack = share.export_share([problem_id], dest=tmp_path / "metadata.gmshare").path
    monkeypatch.setattr(gmshare_module, "MAX_SHARE_METADATA_BYTES", 4)

    with pytest.raises(DomainError, match="manifest.json 无效"):
        share.import_share(pack)


def test_gmshare_counts_blank_checksum_physical_lines(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    problem_id = AppServices(runtime).create_problem(title="physical lines").id
    share = GmshareService(runtime)
    source = share.export_share([problem_id], dest=tmp_path / "lines.gmshare").path
    with zipfile.ZipFile(source, "r") as archive:
        checksums = archive.read("checksums.sha256")
    pack = _rewrite_entry(
        source,
        tmp_path / "too-many-lines.gmshare",
        "checksums.sha256",
        b"\n\n\n" + checksums,
    )
    monkeypatch.setattr(gmshare_module, "MAX_SHARE_PHYSICAL_LINES", 2)

    with pytest.raises(DomainError, match="物理行数超限"):
        share.import_share(pack)
