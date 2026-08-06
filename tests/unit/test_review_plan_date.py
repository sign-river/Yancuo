"""Review-plan date lookup should not repeat network I/O in batch workflows."""

from __future__ import annotations

from yancuo_win.application import services as services_module


class _DateResponse:
    headers = {"Date": "Sat, 01 Aug 2026 16:30:00 GMT"}

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


def test_review_plan_date_caches_network_result(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = 0

    def fake_open(_request, *, timeout):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        assert timeout == 2
        return _DateResponse()

    monkeypatch.setattr(services_module, "safe_urlopen", fake_open)
    service = object.__new__(services_module.AppServices)
    service._review_date_cache = None

    assert service.review_plan_date() == "2026-08-02"
    assert service.review_plan_date() == "2026-08-02"
    assert calls == 1
