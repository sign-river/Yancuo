from __future__ import annotations

import json
from pathlib import Path

import pytest

from yancuo_win.ai.base import AIProvider, JsonCompletionResult, StructuredResult
from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.note_ai_search_service import NoteAiSearchService
from yancuo_win.application.note_service import NoteService
from yancuo_win.config.settings import default_toml_path
from yancuo_win.domain.rules import DomainError


class QueueProvider(AIProvider):
    name = "queue"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.requests: list[dict] = []

    def validate_configuration(self) -> None: pass
    def structure_from_image(self, **_kwargs) -> StructuredResult: raise NotImplementedError
    def complete_json(self, *, request, model, timeout_seconds) -> JsonCompletionResult:
        self.requests.append(request)
        return JsonCompletionResult(raw_text=self.responses.pop(0), model=model)


@pytest.fixture()
def note_ai_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    runtime = bootstrap_runtime()
    note = NoteService(runtime).create_note(title="Taylor", status="active")
    NoteService(runtime).add_block(note.id, block_type="concept", content_markdown="equivalent infinitesimal private text")
    return runtime, note


def test_note_ai_search_reorders_only_locally_recalled_ids(note_ai_bundle) -> None:
    runtime, note = note_ai_bundle
    provider = QueueProvider([
        json.dumps({"keywords": ["infinitesimal"]}),
        json.dumps({"matches": [{"id": "note_fake", "score": 1.0, "reason": "fake"}, {"id": note.id, "score": 0.8, "reason": "match"}]}),
    ])
    result = NoteAiSearchService(runtime, provider=provider).search("find infinitesimal")
    assert [item.note_id for item in result] == [note.id]
    payload = provider.requests[1]["messages"][1]["content"]
    assert "private text" not in payload


def test_note_ai_search_rejects_problem_only_filters(note_ai_bundle) -> None:
    runtime, _note = note_ai_bundle
    provider = QueueProvider([json.dumps({"filters": [{"field": "priority", "operator": "gte", "value": 3}]})])
    with pytest.raises(DomainError, match="题目专属"):
        NoteAiSearchService(runtime, provider=provider).search("anything")
