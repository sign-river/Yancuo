"""Deterministic safety boundaries for reusable AI recognition results."""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from yancuo_win.data.models import AiRecognitionCache


def recognition_cache_key(
    *,
    asset_sha256: str,
    prompt_body: str,
    prompt_version: int,
    provider: str,
    model: str,
    allowed_fields: list[str] | tuple[str, ...],
) -> str:
    """Hash every input that can change a structured recognition result."""

    payload = {
        "asset_sha256": asset_sha256,
        "prompt_body": prompt_body,
        "prompt_version": prompt_version,
        "provider": provider,
        "model": model,
        "allowed_fields": sorted(set(allowed_fields)),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def is_reusable_recognition(status: str, structured_json: str) -> bool:
    """Only completed, non-empty structured outputs may satisfy a cache lookup."""

    return status == "completed" and structured_json.strip() not in {"", "{}", "null"}


def find_recognition_cache(session, cache_key: str) -> AiRecognitionCache | None:
    """Return one durable successful result by its fully specified cache key."""

    entry = session.scalar(
        select(AiRecognitionCache).where(AiRecognitionCache.cache_key == cache_key)
    )
    if entry is None or not is_reusable_recognition("completed", entry.structured_json):
        return None
    return entry


def load_cached_structure(entry: AiRecognitionCache) -> dict[str, object] | None:
    """Treat malformed persisted data as a cache miss, never as authoritative input."""

    try:
        value = json.loads(entry.structured_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) and value else None
