"""Measure the AI intake stages against an isolated local HTTP provider.

The command never opens the formal Yancuo profile. It creates a marked
temporary data root, starts a loopback OpenAI-compatible endpoint, exercises
single/multiple images, cache reuse and an automatic retry, then removes all
temporary data.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from time import perf_counter
from typing import Any, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SOURCE = REPOSITORY_ROOT / "apps" / "windows" / "src"
ISOLATION_MARKER = ".yancuo-ai-timing-isolation"


def _configure_imports() -> None:
    source = str(WINDOWS_SOURCE)
    if source not in sys.path:
        sys.path.insert(0, source)


@contextmanager
def isolated_data_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="yancuo-ai-timing-") as temporary:
        root = Path(temporary).resolve()
        marker = root / ISOLATION_MARKER
        marker.write_text("ai-timing-only\n", encoding="utf-8")
        if root.parent != Path(tempfile.gettempdir()).resolve() or not marker.is_file():
            raise RuntimeError("AI 耗时采样目录未通过隔离校验")
        try:
            yield root
        finally:
            logging.shutdown()


class ProviderState:
    def __init__(self) -> None:
        self.calls = 0
        self.failures_remaining = 0
        self.image_counts: list[int] = []
        self.subject_id = ""
        self.chapter_id = ""


def _handler(state: ProviderState) -> type[BaseHTTPRequestHandler]:
    class TimingProviderHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            state.calls += 1
            content = payload["messages"][0]["content"]
            state.image_counts.append(
                sum(item.get("type") == "image_url" for item in content)
            )
            if state.failures_remaining:
                state.failures_remaining -= 1
                body = b'{"error":{"message":"controlled retry"}}'
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            response = {
                "model": "local-timing-vision",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "problems": [
                                        {
                                            "title": "本机隔离采样题",
                                            "question_markdown": "求矩阵的特征值。",
                                            "subject_id": state.subject_id,
                                            "chapter_id": state.chapter_id,
                                            "problem_type": "计算题",
                                            "priority": 3,
                                            "uncertain_fields": [],
                                            "region": {
                                                "x": 0,
                                                "y": 0,
                                                "width": 1,
                                                "height": 1,
                                            },
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 40,
                    "total_tokens": 160,
                },
            }
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Server-Timing", "model;dur=37.5")
            self.send_header("OpenAI-Processing-Ms", "37.5")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return TimingProviderHandler


@contextmanager
def local_provider(state: ProviderState) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _set_environment(data_root: Path, base_url: str) -> None:
    os.environ["YANCUO_DATA_ROOT"] = str(data_root)
    os.environ["YANCUO_CONFIG_FILE"] = str(
        WINDOWS_SOURCE / "yancuo_win" / "resources" / "config" / "default.toml"
    )
    os.environ["YANCUO_AI__DEFAULT_PROVIDER"] = "openai_compatible"
    os.environ[
        "YANCUO_AI__PROVIDERS__OPENAI_COMPATIBLE__BASE_URL"
    ] = base_url
    os.environ[
        "YANCUO_AI__PROVIDERS__OPENAI_COMPATIBLE__API_KEY_ENV"
    ] = "YANCUO_TIMING_API_KEY"
    os.environ["YANCUO_TIMING_API_KEY"] = "isolated-loopback-only"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _write_image(path: Path, marker: bytes) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + marker * 64)


def _run_worker(intake: Any, job_id: str) -> tuple[float, float]:
    from PySide6.QtCore import QEventLoop
    from yancuo_win.tasks.worker import AIJobWorker

    loop = QEventLoop()
    failure: list[str] = []
    worker = AIJobWorker(intake.ai, job_id)
    worker.finished_ok.connect(lambda _job_id: loop.quit())
    worker.failed.connect(lambda _job_id, message: (failure.append(message), loop.quit()))
    worker.start()
    loop.exec()
    worker.wait()
    if failure:
        raise RuntimeError(failure[0])
    received_at = perf_counter()
    ui_wait_ms = max(
        0.0,
        (received_at - (worker.service_finished_at or received_at)) * 1000,
    )
    classification_started = perf_counter()
    intake.list_candidates(job_id)
    classification_match_ms = (perf_counter() - classification_started) * 1000
    intake.ai.record_ui_delivery_timings(
        job_id,
        ui_wait_ms=ui_wait_ms,
        classification_match_ms=classification_match_ms,
    )
    return ui_wait_ms, classification_match_ms


def _case(
    intake: Any,
    state: ProviderState,
    *,
    name: str,
    images: list[Path],
    recognition_mode: str,
    fail_once: bool = False,
) -> dict[str, Any]:
    calls_before = state.calls
    if fail_once:
        state.failures_remaining = 1
    started = intake.start_ai(images, recognition_mode=recognition_mode)
    _run_worker(intake, started.job_id)
    progress = intake.progress(started.job_id)
    calls = state.calls - calls_before
    return {
        "case": name,
        "provider_calls": calls,
        "images_per_call": state.image_counts[-calls:] if calls else [],
        "cache_hits": progress.cache_hits,
        "automatic_retries": progress.retry_count,
        "provider_token_usage": progress.provider_token_usage,
        "provider_server_timing": progress.provider_server_timing,
        "timings_ms": progress.timings_ms,
    }


def run() -> dict[str, Any]:
    _configure_imports()
    from PySide6.QtCore import QCoreApplication
    from yancuo_win.application.bootstrap import bootstrap_runtime
    from yancuo_win.application.intake_service import ProblemIntakeService

    application = QCoreApplication.instance() or QCoreApplication([])
    del application
    state = ProviderState()
    with isolated_data_root() as root, local_provider(state) as base_url:
        _set_environment(root, base_url)
        runtime = bootstrap_runtime()
        try:
            intake = ProblemIntakeService(runtime)
            subject = intake.app.create_subject("线性代数")
            chapter = intake.app.create_chapter(subject.id, "矩阵")
            state.subject_id = subject.id
            state.chapter_id = chapter.id
            first = root / "single.png"
            second = root / "multi-a.png"
            third = root / "multi-b.png"
            retry = root / "retry.png"
            _write_image(first, b"single")
            _write_image(second, b"multi-a")
            _write_image(third, b"multi-b")
            _write_image(retry, b"retry")
            cases = [
                _case(
                    intake,
                    state,
                    name="single_cache_miss",
                    images=[first],
                    recognition_mode="one_to_one",
                ),
                _case(
                    intake,
                    state,
                    name="single_cache_hit",
                    images=[first],
                    recognition_mode="one_to_one",
                ),
                _case(
                    intake,
                    state,
                    name="multiple_images_cache_miss",
                    images=[second, third],
                    recognition_mode="many_to_one",
                ),
                _case(
                    intake,
                    state,
                    name="single_request_with_retry",
                    images=[retry],
                    recognition_mode="one_to_one",
                    fail_once=True,
                ),
            ]
            cache_case = cases[1]
            if cache_case["provider_calls"] != 0 or cache_case["cache_hits"] != 1:
                raise RuntimeError("缓存命中仍触发了 Provider 请求")
            return {
                "method": {
                    "data": "marked operating-system temporary directory, removed after run",
                    "provider": "loopback OpenAI-compatible HTTP server",
                    "retry": "first response HTTP 503, then success",
                    "ui_wait": "QThread finished signal to main Qt event loop",
                    "classification_match": "candidate loading and local taxonomy validation/inference",
                },
                "cases": cases,
                "conclusion": local_bottleneck_conclusion(cases),
            }
        finally:
            runtime.engine.dispose()


def local_bottleneck_conclusion(cases: list[dict[str, Any]]) -> str:
    local_keys = {
        "preflight",
        "cache_lookup",
        "image_encode",
        "response_parse",
        "validation",
        "candidate_write",
        "classification_match",
        "ui_wait",
    }
    local_values = [
        float(value)
        for case in cases
        for key, value in case["timings_ms"].items()
        if key in local_keys
    ]
    request_values = [
        float(case["timings_ms"].get("request", 0.0))
        for case in cases
        if case["provider_calls"]
    ]
    largest_local = max(local_values, default=0.0)
    largest_request = max(request_values, default=0.0)
    if largest_local > largest_request and largest_local >= 100:
        return "evidence_of_local_bottleneck"
    return "no_local_bottleneck_in_isolated_sample"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
