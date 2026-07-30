from __future__ import annotations

import importlib.util
from pathlib import Path
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
