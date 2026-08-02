"""阶段 F：.ebpack 导出、校验与恢复。"""

from __future__ import annotations

import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_intake_service import (
    NoteDraftBlockInput,
    NoteDraftGroupInput,
    NoteIntakeService,
)
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.identity import SCHEMA_VERSION
from yancuo_win.domain.rules import DomainError
from yancuo_win.import_export.ebpack import EbpackService
import yancuo_win.import_export.ebpack as ebpack_module


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return bootstrap_runtime()


def test_ebpack_roundtrip_consistent(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = AppServices(runtime)
    eb = EbpackService(runtime)
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"ebpack-bytes")
    pid = services.import_images([img])["created"][0]
    services.update_problem(pid, {"question_markdown": "ebpack题目内容"})
    note_img = tmp_path / "note.jpg"
    note_img.write_bytes(b"\xff\xd8\xff" + b"ebpack-note-draft")
    note_intake = NoteIntakeService(runtime)
    draft = note_intake.start_session(
        [note_img],
        classification_mode="custom",
    )
    note_intake.save_extraction(
        draft.id,
        metadata={"title": "可恢复草稿"},
        groups=[
            NoteDraftGroupInput(
                title="未分类",
                blocks=(
                    NoteDraftBlockInput(
                        block_type="concept",
                        content_markdown="包内保留的概念块",
                        source_asset_id=draft.assets[0].id,
                    ),
                ),
            )
        ],
    )

    pack = eb.export_ebpack(tmp_path / "out.ebpack")
    assert pack.suffix == ".ebpack"
    manifest = eb.verify_ebpack(pack)
    assert manifest["format"] == "graduate-mistake-book-ebpack"
    assert manifest["format_version"] == 1
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["asset_count"] == 2
    assert manifest["encrypted"] is False
    assert manifest["authoritative_payload"] == "database/snapshot.sqlite"
    portable_snapshot = tmp_path / "portable-snapshot.sqlite"
    with zipfile.ZipFile(pack, "r") as archive:
        portable_snapshot.write_bytes(archive.read("database/snapshot.sqlite"))
    with closing(sqlite3.connect(portable_snapshot)) as connection:
        portable_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "search_documents" in portable_tables
    assert "search_documents_fts" not in portable_tables
    assert {
        "note_intake_sessions",
        "note_intake_assets",
        "note_draft_groups",
        "note_draft_blocks",
    } <= portable_tables

    target = tmp_path / "restored"
    result = eb.restore_ebpack(pack, target)
    assert result["schema_version"] == SCHEMA_VERSION
    assert (target / "error_book.db").is_file()
    assert any((target / "assets").rglob("*"))
    with closing(sqlite3.connect(target / "error_book.db")) as connection:
        exported_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"search_documents", "search_documents_fts"} <= exported_tables

    monkeypatch.setenv("YANCUO_DATA_ROOT", str(target))
    restored_rt = bootstrap_runtime()
    with restored_rt.engine.connect() as connection:
        restored_tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "search_documents_fts" in restored_tables
    restored = AppServices(restored_rt)
    got = restored.get_problem(pid)
    assert got is not None
    assert "ebpack题目内容" in (got.question_markdown or "")
    restored_draft = NoteIntakeService(restored_rt).get_session(draft.id)
    assert restored_draft is not None
    assert restored_draft.groups[0].blocks[0].content_markdown == "包内保留的概念块"
    assert (
        NoteIntakeService(restored_rt)
        .resolve_source_path(restored_draft.assets[0])
        .is_file()
    )


def test_ebpack_export_failure_preserves_existing_destination(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = AppServices(runtime)
    services.create_problem(title="atomic export")
    destination = tmp_path / "existing.ebpack"
    destination.write_bytes(b"previous-backup")

    def fail_write(
        self, filename, arcname=None, compress_type=None, compresslevel=None
    ):
        del self, filename, arcname, compress_type, compresslevel
        raise OSError("simulated archive write failure")

    monkeypatch.setattr(zipfile.ZipFile, "write", fail_write)

    with pytest.raises(OSError, match="simulated"):
        EbpackService(runtime).export_ebpack(destination)

    assert destination.read_bytes() == b"previous-backup"
    assert list(tmp_path.glob(".existing.ebpack.*.tmp")) == []
    assert list(runtime.paths.cache_dir.glob("ebpack-export-*")) == []


def test_ebpack_export_includes_committed_wal_changes(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    problem = services.create_problem(title="before WAL")
    writer = sqlite3.connect(runtime.paths.database)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "UPDATE problems SET title = ? WHERE id = ?",
            ("committed in WAL", problem.id),
        )
        writer.commit()
        assert runtime.paths.database.with_name("error_book.db-wal").is_file()

        pack = EbpackService(runtime).export_ebpack(tmp_path / "wal.ebpack")
        snapshot = tmp_path / "wal-snapshot.sqlite"
        with zipfile.ZipFile(pack, "r") as archive:
            snapshot.write_bytes(archive.read("database/snapshot.sqlite"))
        with closing(sqlite3.connect(snapshot)) as connection:
            title = connection.execute(
                "SELECT title FROM problems WHERE id = ?", (problem.id,)
            ).fetchone()[0]
    finally:
        writer.close()

    assert title == "committed in WAL"


def test_corrupt_ebpack_rejected(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    eb = EbpackService(runtime)
    services.create_problem(title="x")
    pack = eb.export_ebpack(tmp_path / "ok.ebpack")

    # 篡改 zip 内 snapshot 而不改 checksums
    bad = tmp_path / "bad.ebpack"
    with zipfile.ZipFile(pack, "r") as zin, zipfile.ZipFile(bad, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "database/snapshot.sqlite":
                data = data + b"CORRUPT"
            zout.writestr(item, data)

    with pytest.raises(DomainError, match="校验失败|checksum"):
        eb.verify_ebpack(bad)
    with pytest.raises(DomainError):
        eb.restore_ebpack(bad, tmp_path / "should_not")


def test_ebpack_rejects_oversized_manifest_before_json_decode(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = AppServices(runtime)
    services.create_problem(title="metadata budget")
    eb = EbpackService(runtime)
    pack = eb.export_ebpack(tmp_path / "metadata-budget.ebpack")
    monkeypatch.setattr(ebpack_module, "MAX_EBPACK_METADATA_BYTES", 4)

    with pytest.raises(DomainError, match="manifest.json 过大"):
        eb.verify_ebpack(pack)


def test_ebpack_rejects_oversized_extracted_checksum_table(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "extracted"
    root.mkdir()
    (root / "checksums.sha256").write_bytes(b"12345")
    monkeypatch.setattr(ebpack_module, "MAX_EBPACK_METADATA_BYTES", 4)

    with pytest.raises(DomainError, match="checksums.sha256 过大"):
        EbpackService(runtime)._verify_checksums(root)


def test_incomplete_checksum_table_is_rejected(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    eb = EbpackService(runtime)
    services.create_problem(title="checksum coverage")
    pack = eb.export_ebpack(tmp_path / "checksums.ebpack")
    incomplete = tmp_path / "checksums-incomplete.ebpack"

    with (
        zipfile.ZipFile(pack, "r") as source,
        zipfile.ZipFile(incomplete, "w") as target,
    ):
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename == "checksums.sha256":
                payload = b""
            target.writestr(item, payload)

    with pytest.raises(DomainError, match="未覆盖"):
        eb.verify_ebpack(incomplete)


def test_schema_too_new_rejected(
    runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    services = AppServices(runtime)
    eb = EbpackService(runtime)
    services.create_problem(title="y")
    eb.export_ebpack(tmp_path / "schema.ebpack")

    # 改 manifest schema_version 为未来版本，并重算该文件 checksum 会使整体复杂；
    # 直接单测 _validate_manifest
    with pytest.raises(DomainError, match="升级软件"):
        eb._validate_manifest(
            {
                "format": "graduate-mistake-book-ebpack",
                "format_version": 1,
                "encrypted": False,
                "schema_version": SCHEMA_VERSION + 10,
            }
        )

    with pytest.raises(DomainError, match="schema_version 无效"):
        eb._validate_manifest(
            {
                "format": "graduate-mistake-book-ebpack",
                "format_version": 1,
                "encrypted": False,
                "schema_version": 0,
            }
        )


def test_manifest_schema_must_match_snapshot(runtime, tmp_path: Path) -> None:
    services = AppServices(runtime)
    eb = EbpackService(runtime)
    services.create_problem(title="schema mismatch")
    pack = eb.export_ebpack(tmp_path / "mismatch.ebpack")
    extracted = tmp_path / "mismatch"
    with zipfile.ZipFile(pack, "r") as archive:
        archive.extractall(extracted)

    with pytest.raises(DomainError, match="不一致"):
        eb._validate_snapshot_schema(
            extracted,
            {
                "schema_version": SCHEMA_VERSION - 1,
                "problem_count": 1,
            },
        )

    with pytest.raises(DomainError, match="problem_count 不一致"):
        eb._validate_snapshot_schema(
            extracted,
            {
                "schema_version": SCHEMA_VERSION,
                "problem_count": 2,
            },
        )


def test_encrypted_rejected(runtime) -> None:
    eb = EbpackService(runtime)
    with pytest.raises(DomainError, match="加密"):
        eb._validate_manifest(
            {
                "format": "graduate-mistake-book-ebpack",
                "format_version": 1,
                "encrypted": True,
                "schema_version": 1,
            }
        )
