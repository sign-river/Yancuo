"""阶段 F：.ebpack 导出、校验与恢复。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.data.db import make_engine
from yancuo_win.data.migrate import get_schema_version
from yancuo_win.domain.identity import SCHEMA_VERSION
from yancuo_win.domain.rules import DomainError
from yancuo_win.import_export.ebpack import EbpackService


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

    pack = eb.export_ebpack(tmp_path / "out.ebpack")
    assert pack.suffix == ".ebpack"
    manifest = eb.verify_ebpack(pack)
    assert manifest["format"] == "graduate-mistake-book-ebpack"
    assert manifest["format_version"] == 1
    assert manifest["encrypted"] is False
    assert manifest["authoritative_payload"] == "database/snapshot.sqlite"

    target = tmp_path / "restored"
    result = eb.restore_ebpack(pack, target)
    assert result["schema_version"] == SCHEMA_VERSION
    assert (target / "error_book.db").is_file()
    assert any((target / "assets").rglob("*"))

    monkeypatch.setenv("YANCUO_DATA_ROOT", str(target))
    restored_rt = bootstrap_runtime()
    restored = AppServices(restored_rt)
    got = restored.get_problem(pid)
    assert got is not None
    assert "ebpack题目内容" in (got.question_markdown or "")


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


def test_schema_too_new_rejected(runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    services = AppServices(runtime)
    eb = EbpackService(runtime)
    services.create_problem(title="y")
    pack = eb.export_ebpack(tmp_path / "schema.ebpack")

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
