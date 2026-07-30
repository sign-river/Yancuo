from __future__ import annotations

import json
from pathlib import Path

from yancuo_win.__main__ import _run_packaging_smoke_test
from yancuo_win.config.settings import default_toml_path


def test_packaging_smoke_bootstraps_isolated_data_and_reports_resources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = tmp_path / "data"
    report = tmp_path / "smoke.json"
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(data_root))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    monkeypatch.setenv("YANCUO_PACKAGING_SMOKE_REPORT", str(report))

    assert _run_packaging_smoke_test() == 0

    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["passed"] is True
    assert result["data_root"] == str(data_root)
    assert Path(result["database"]).is_file()
    assert Path(result["default_config"]).is_file()
    assert Path(result["problem_schema"]).is_file()
