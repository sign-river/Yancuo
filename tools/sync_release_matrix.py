"""Run the isolated pre-release regression matrix for supported sync channels."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_APP = ROOT / "apps" / "windows"
MATRIX_MODULES = (
    "../../tests/unit/test_phase_f_ebpack.py",
    "../../tests/unit/test_phase_g_cloud.py",
    "../../tests/unit/test_cloudbase_provider.py",
    "../../tests/unit/test_phase_j_sync.py",
    "../../tests/unit/test_archive_security.py",
)


def main() -> int:
    """Run only simulated-provider tests with a fresh, disposable data root."""
    with tempfile.TemporaryDirectory(prefix="yancuo-sync-release-") as data_root:
        env = os.environ.copy()
        env["YANCUO_DATA_ROOT"] = data_root
        env["YANCUO_CONFIG_FILE"] = str(ROOT / "config" / "default.toml")
        command = [sys.executable, "-m", "pytest", "-q", *MATRIX_MODULES]
        return subprocess.run(command, cwd=WINDOWS_APP, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
