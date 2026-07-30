"""Generate and measure an isolated Yancuo performance baseline.

The command always creates its database below the operating-system temporary
directory and removes the complete directory when the run finishes.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
from typing import Callable, Iterator


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SOURCE = REPOSITORY_ROOT / "apps" / "windows" / "src"
DEFAULT_PROBLEMS = 1200
DEFAULT_NOTES = 300
ISOLATION_MARKER = ".yancuo-performance-isolation"


def _configure_imports() -> None:
    source = str(WINDOWS_SOURCE)
    if source not in sys.path:
        sys.path.insert(0, source)


def _set_isolated_environment(data_root: Path) -> None:
    os.environ["YANCUO_DATA_ROOT"] = str(data_root)
    os.environ["YANCUO_CONFIG_FILE"] = str(
        WINDOWS_SOURCE / "yancuo_win" / "resources" / "config" / "default.toml"
    )
    os.environ["YANCUO_AI__DEFAULT_PROVIDER"] = "mock"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@contextmanager
def isolated_data_root() -> Iterator[Path]:
    """Yield a marked temporary data root and guarantee recursive cleanup."""

    with tempfile.TemporaryDirectory(prefix="yancuo-performance-") as temporary:
        root = Path(temporary).resolve()
        marker = root / ISOLATION_MARKER
        marker.write_text("performance-only\n", encoding="utf-8")
        if root.parent != Path(tempfile.gettempdir()).resolve() or not marker.is_file():
            raise RuntimeError("性能资料目录未通过隔离校验")
        try:
            yield root
        finally:
            # FileHandler keeps yancuo.log locked on Windows until explicitly
            # closed, which would otherwise defeat the cleanup guarantee.
            logging.shutdown()


def summarize(samples: list[float]) -> dict[str, object]:
    """Return stable summary statistics and Tukey outliers in milliseconds."""

    if not samples:
        raise ValueError("samples must not be empty")
    ordered = sorted(round(value, 3) for value in samples)
    if len(ordered) < 4:
        outliers: list[float] = []
    else:
        lower = ordered[: len(ordered) // 2]
        upper = ordered[(len(ordered) + 1) // 2 :]
        q1 = statistics.median(lower)
        q3 = statistics.median(upper)
        spread = q3 - q1
        outliers = [
            value
            for value in ordered
            if value < q1 - 1.5 * spread or value > q3 + 1.5 * spread
        ]
    return {
        "samples": ordered,
        "sample_count": len(ordered),
        "median_ms": round(statistics.median(ordered), 3),
        "min_ms": ordered[0],
        "max_ms": ordered[-1],
        "outliers_ms": outliers,
    }


def _measure(operation: Callable[[], object], samples: int) -> list[float]:
    values: list[float] = []
    for _ in range(samples):
        started = perf_counter()
        operation()
        values.append((perf_counter() - started) * 1000)
    return values


def generate_dataset(data_root: Path, problem_count: int, note_count: int) -> None:
    """Create canonical rows directly, then rebuild disposable search indexes."""

    _configure_imports()
    _set_isolated_environment(data_root)
    from sqlalchemy import insert

    from yancuo_win.application.bootstrap import bootstrap_runtime
    from yancuo_win.application.search_service import SearchIndexService
    from yancuo_win.application.unified_search_service import UnifiedSearchIndexService
    from yancuo_win.data.models import Chapter, NoteBlock, NoteDocument, Problem, Subject

    runtime = bootstrap_runtime()
    subject_id = "subject_perf"
    chapters = [
        {
            "id": f"chapter_perf_{index:02d}",
            "subject_id": subject_id,
            "name": f"性能章节 {index + 1}",
            "sort_order": index,
        }
        for index in range(12)
    ]
    now = datetime.now(timezone.utc)
    problems = [
        {
            "id": f"problem_perf_{index:05d}",
            "status": "active",
            "subject_id": subject_id,
            "chapter_id": chapters[index % len(chapters)]["id"],
            "title": f"性能基线题目 {index + 1}",
            "question_markdown": (
                f"第 {index + 1} 题：研究函数极值与积分，"
                f"并说明步骤。公式 $x^{index % 7 + 1}+1$。"
            ),
            "solution_markdown": "先求导，再检查边界与驻点。",
            "priority": index % 5 + 1,
            "human_confirmed": True,
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        for index in range(problem_count)
    ]
    notes = [
        {
            "id": f"note_perf_{index:05d}",
            "status": "active",
            "subject_id": subject_id,
            "chapter_id": chapters[index % len(chapters)]["id"],
            "title": f"性能基线笔记 {index + 1}",
            "summary": "极值、积分与复习方法的隔离性能样本。",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        }
        for index in range(note_count)
    ]
    blocks = [
        {
            "id": f"nblock_perf_{index:05d}",
            "note_document_id": f"note_perf_{index:05d}",
            "sort_order": 0,
            "block_type": "text",
            "content_markdown": f"笔记 {index + 1} 的极值与积分要点。",
            "source_region_json": "{}",
            "uncertain_json": "[]",
            "created_at": now,
            "updated_at": now,
        }
        for index in range(note_count)
    ]
    with runtime.engine.begin() as connection:
        connection.execute(
            insert(Subject),
            [{"id": subject_id, "name": "隔离性能科目", "sort_order": 0}],
        )
        connection.execute(insert(Chapter), chapters)
        for offset in range(0, len(problems), 500):
            connection.execute(insert(Problem), problems[offset : offset + 500])
        if notes:
            connection.execute(insert(NoteDocument), notes)
            connection.execute(insert(NoteBlock), blocks)
    SearchIndexService(runtime).rebuild()
    UnifiedSearchIndexService(runtime).rebuild_notes()
    runtime.engine.dispose()


def _startup_probe(data_root: Path) -> dict[str, float]:
    """Measure first and repeated bootstrap in a newly spawned interpreter."""

    env = os.environ.copy()
    env["YANCUO_DATA_ROOT"] = str(data_root)
    env["YANCUO_CONFIG_FILE"] = str(
        WINDOWS_SOURCE / "yancuo_win" / "resources" / "config" / "default.toml"
    )
    env["YANCUO_AI__DEFAULT_PROVIDER"] = "mock"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(WINDOWS_SOURCE), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    command = [sys.executable, str(Path(__file__).resolve()), "--startup-probe"]
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(completed.stdout)


def measure_runtime(data_root: Path, sample_count: int) -> dict[str, dict[str, object]]:
    _configure_imports()
    _set_isolated_environment(data_root)
    from PySide6.QtWidgets import QApplication

    from yancuo_win.application.bootstrap import bootstrap_runtime
    from yancuo_win.application.note_service import NoteService
    from yancuo_win.application.search_service import SearchIndexService
    from yancuo_win.application.services import AppServices, ProblemFilter
    from yancuo_win.ui.main_window import MainWindow

    cold_start: list[float] = []
    hot_start: list[float] = []
    for _ in range(sample_count):
        startup = _startup_probe(data_root)
        cold_start.append(startup["cold_ms"])
        hot_start.append(startup["hot_ms"])

    runtime = bootstrap_runtime()
    services = AppServices(runtime)
    notes = NoteService(runtime)
    search = SearchIndexService(runtime)
    app = QApplication.instance() or QApplication([])

    main_window: list[float] = []
    library_refresh: list[float] = []
    scrolling: list[float] = []
    for _ in range(sample_count):
        started = perf_counter()
        window = MainWindow(runtime)
        app.processEvents()
        main_window.append((perf_counter() - started) * 1000)

        library_refresh.extend(
            _measure(lambda: (window.refresh_problems(), app.processEvents()), 1)
        )

        def scroll_round_trip() -> None:
            window.problem_list.scrollToBottom()
            app.processEvents()
            window.problem_list.scrollToTop()
            app.processEvents()

        scrolling.extend(_measure(scroll_round_trip, 1))
        window.close()
        app.processEvents()

    result = {
        "cold_start": summarize(cold_start),
        "hot_start": summarize(hot_start),
        "main_window_construction": summarize(main_window),
        "library_refresh": summarize(library_refresh),
        "local_search": summarize(
            _measure(lambda: search.search("极值 积分", limit=50), sample_count)
        ),
        "problem_list_query": summarize(
            _measure(
                lambda: services.list_problems(ProblemFilter(status="active")),
                sample_count,
            )
        ),
        "note_list_query": summarize(
            _measure(lambda: notes.list_notes(status="active"), sample_count)
        ),
        "problem_list_scroll_round_trip": summarize(scrolling),
    }
    runtime.engine.dispose()
    return result


def _memory_gib() -> float | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round(status.total_phys / 1024**3, 2)
    except (AttributeError, OSError):
        return None


def environment_metadata() -> dict[str, object]:
    _configure_imports()
    import PySide6
    import sqlalchemy

    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "platform": platform.platform(),
        "processor": platform.processor() or "unknown",
        "logical_cpu_count": os.cpu_count(),
        "memory_gib": _memory_gib(),
        "python": platform.python_version(),
        "qt": PySide6.__version__,
        "sqlalchemy": sqlalchemy.__version__,
        "qt_platform": os.environ.get("QT_QPA_PLATFORM", "default"),
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    with isolated_data_root() as data_root:
        _set_isolated_environment(data_root)
        generation_started = perf_counter()
        generate_dataset(data_root, args.problems, args.notes)
        generation_ms = (perf_counter() - generation_started) * 1000
        metrics = measure_runtime(data_root, args.samples)
        report = {
            "environment": environment_metadata(),
            "dataset": {
                "problems": args.problems,
                "notes": args.notes,
                "generation_ms": round(generation_ms, 3),
                "location": "temporary isolated directory (cleaned)",
            },
            "method": {
                "samples_per_metric": args.samples,
                "cold_start": (
                    "first bootstrap in a new interpreter; operating-system caches "
                    "are not forcibly purged"
                ),
                "hot_start": "second bootstrap in the same interpreter",
                "outliers": "Tukey 1.5×IQR; fewer than four samples report none",
            },
            "metrics_ms": metrics,
            "cleanup": "completed after this report is serialized",
        }
        return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--problems", type=int, default=DEFAULT_PROBLEMS)
    parser.add_argument("--notes", type=int, default=DEFAULT_NOTES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--startup-probe", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    if args.problems < DEFAULT_PROBLEMS or args.notes < DEFAULT_NOTES:
        parser.error(
            f"baseline requires at least {DEFAULT_PROBLEMS} problems and "
            f"{DEFAULT_NOTES} notes"
        )
    return args


def main() -> int:
    args = _parse_args()
    if args.startup_probe:
        _configure_imports()
        started = perf_counter()
        from yancuo_win.application.bootstrap import bootstrap_runtime

        first = bootstrap_runtime()
        cold_ms = (perf_counter() - started) * 1000
        first.engine.dispose()
        started = perf_counter()
        second = bootstrap_runtime()
        hot_ms = (perf_counter() - started) * 1000
        second.engine.dispose()
        print(json.dumps({"cold_ms": cold_ms, "hot_ms": hot_ms}))
        return 0

    report = run(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
