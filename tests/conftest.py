"""Shared test-process configuration."""

from __future__ import annotations

import os

# Native Windows Qt teardown can emit a late COM fatal diagnostic even when
# widget tests pass. The offscreen backend keeps these tests deterministic.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
