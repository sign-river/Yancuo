"""Background model-list loading keeps provider calls out of the UI thread."""

from __future__ import annotations

from yancuo_win.tasks.model_worker import AIModelListWorker


class _ProviderStub:
    def __init__(self, *, models: list[str] | None = None, error: Exception | None = None):
        self.models = models or []
        self.error = error
        self.timeout_seconds: int | None = None

    def list_models(self, *, timeout_seconds: int) -> list[str]:
        self.timeout_seconds = timeout_seconds
        if self.error is not None:
            raise self.error
        return self.models


def test_model_list_worker_returns_models_with_configured_timeout() -> None:
    provider = _ProviderStub(models=["vision-b", "vision-a"])
    completed: list[object] = []
    worker = AIModelListWorker(provider, timeout_seconds=9)
    worker.finished_ok.connect(completed.append)

    worker.run()

    assert provider.timeout_seconds == 9
    assert completed == [["vision-b", "vision-a"]]


def test_model_list_worker_reports_provider_errors() -> None:
    provider = _ProviderStub(error=RuntimeError("service unavailable"))
    failures: list[str] = []
    worker = AIModelListWorker(provider)
    worker.failed.connect(failures.append)

    worker.run()

    assert failures == ["service unavailable"]
