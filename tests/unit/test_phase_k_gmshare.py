"""阶段 K：.gmshare 脱敏分享与 origin 去重。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path
from yancuo_win.import_export.gmshare import HARD_DENY_FIELDS, GmshareService


@pytest.fixture()
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    return bootstrap_runtime()


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


def test_gmshare_import_dedup(runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    services = AppServices(runtime)
    pid = services.create_problem(title="去重题").id
    services.update_problem(pid, {"question_markdown": "Q"})
    share = GmshareService(runtime)
    pack = share.export_share([pid], dest=tmp_path / "dedup.gmshare").path

    first = share.import_share(pack)
    assert first.created == 1
    assert first.skipped_duplicates == 0
    second = share.import_share(pack)
    assert second.created == 0
    assert second.skipped_duplicates == 1
