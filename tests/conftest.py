"""Shared test-process configuration."""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path

import pytest

# Always test the repository-local source tree, never a stale editable install
# (e.g. an old `pip install -e` pointing at another checkout). Every AI agent
# and every local run therefore tests the exact code under development.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_SRC = _REPO_ROOT / "apps" / "windows" / "src"
if str(_LOCAL_SRC) not in sys.path:
    sys.path.insert(0, str(_LOCAL_SRC))

# Native Windows Qt teardown can emit a late COM fatal diagnostic even when
# widget tests pass. The offscreen backend keeps these tests deterministic.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Reuse one fully migrated golden database across the test session instead
# of replaying all schema migrations for every fixture-based test.
os.environ.setdefault("YANCUO_TEST_FAST", "1")


@pytest.fixture(autouse=True)
def _collect_qt_wrapper_cycles() -> None:
    """Break PySide6 wrapper cycles after every test.

    Qt widgets keep C++ child objects alive through their parent chain, while
    PySide6 signal/slot connections keep Python wrappers alive through cycles
    that only Python's cyclic GC can collect.  If those cycles accumulate,
    invisible C++ widgets with dangling parent pointers survive into later
    tests and corrupt the heap when QApplication.setStyleSheet() walks the
    whole widget tree (Windows fatal exception 0xc0000374).
    """
    yield
    gc.collect()
