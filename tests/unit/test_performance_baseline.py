from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "performance_baseline.py"
SPEC = importlib.util.spec_from_file_location("performance_baseline", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
performance_baseline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(performance_baseline)


def test_summary_reports_median_and_tukey_outliers() -> None:
    result = performance_baseline.summarize(
        [10.0, 10.2, 10.4, 10.6, 10.8, 11.0, 80.0]
    )

    assert result["sample_count"] == 7
    assert result["median_ms"] == 10.6
    assert result["outliers_ms"] == [80.0]


def test_isolated_data_root_is_marked_and_removed() -> None:
    with performance_baseline.isolated_data_root() as root:
        assert root.parent == Path(tempfile.gettempdir()).resolve()
        assert (root / performance_baseline.ISOLATION_MARKER).is_file()
        captured = root

    assert not captured.exists()


@pytest.mark.parametrize("samples", [[], None])
def test_summary_rejects_empty_samples(samples) -> None:
    with pytest.raises(ValueError):
        performance_baseline.summarize(samples)


def test_diagnostic_summary_reports_numeric_range() -> None:
    result = performance_baseline._diagnostic_summary([71.387, 68.852, 123.98])

    assert result == {
        "samples": [68.852, 71.387, 123.98],
        "median": 71.387,
        "min": 68.852,
        "max": 123.98,
    }


def test_ui203_mode_requires_ten_thousand_notes(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["performance_baseline.py", "--ui203", "--notes", "9999"],
    )

    with pytest.raises(SystemExit):
        performance_baseline._parse_args()


def test_ui203_mode_accepts_target_sample(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["performance_baseline.py", "--ui203", "--notes", "10000"],
    )

    args = performance_baseline._parse_args()

    assert args.ui203 is True
    assert args.notes == 10_000
