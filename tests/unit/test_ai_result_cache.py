from yancuo_win.application.ai_result_cache import recognition_cache_key, is_reusable_recognition


def test_recognition_cache_key_includes_every_result_affecting_input() -> None:
    base = dict(asset_sha256="a" * 64, prompt_body="prompt", prompt_version=1, provider="faro", model="vision", allowed_fields=["title", "solution"])
    assert recognition_cache_key(**base) == recognition_cache_key(**{**base, "allowed_fields": ["solution", "title"]})
    assert recognition_cache_key(**base) != recognition_cache_key(**{**base, "model": "other"})
    assert recognition_cache_key(**base) != recognition_cache_key(**{**base, "prompt_version": 2})


def test_only_completed_nonempty_results_are_reusable() -> None:
    assert is_reusable_recognition("completed", '{"title":"x"}')
    assert not is_reusable_recognition("failed", '{"title":"x"}')
    assert not is_reusable_recognition("completed", "{}")
