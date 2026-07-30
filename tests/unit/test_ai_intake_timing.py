from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile


SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "ai_intake_timing.py"
SPEC = importlib.util.spec_from_file_location("ai_intake_timing", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ai_intake_timing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ai_intake_timing)


def test_isolated_ai_timing_root_is_marked_and_removed() -> None:
    with ai_intake_timing.isolated_data_root() as root:
        assert root.parent == Path(tempfile.gettempdir()).resolve()
        assert (root / ai_intake_timing.ISOLATION_MARKER).is_file()
        captured = root

    assert not captured.exists()


def test_local_bottleneck_conclusion_only_flags_material_local_stage() -> None:
    remote_bound = [
        {
            "provider_calls": 1,
            "timings_ms": {
                "request": 500.0,
                "classification_match": 5.0,
                "ui_wait": 1.0,
            },
        }
    ]
    local_bound = [
        {
            "provider_calls": 1,
            "timings_ms": {
                "request": 20.0,
                "classification_match": 150.0,
                "ui_wait": 1.0,
            },
        }
    ]

    assert (
        ai_intake_timing.local_bottleneck_conclusion(remote_bound)
        == "no_local_bottleneck_in_isolated_sample"
    )
    assert (
        ai_intake_timing.local_bottleneck_conclusion(local_bound)
        == "evidence_of_local_bottleneck"
    )
