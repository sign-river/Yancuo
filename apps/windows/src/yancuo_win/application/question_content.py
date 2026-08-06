"""Question content-block helpers shared by readers and exporters."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Iterable

from yancuo_win.ai.base import normalize_content_blocks


_MAX_EMBEDDED_FIGURE_BYTES = 16 * 1024 * 1024
_MAX_EMBEDDED_FIGURE_TOTAL_BYTES = 32 * 1024 * 1024
_MAX_EMBEDDED_FIGURES = 50


def load_question_content(value: str | None) -> list[dict[str, Any]]:
    """Parse persisted content blocks, returning a safe legacy fallback on damage."""

    try:
        raw = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return normalize_content_blocks(raw)


def content_blocks_with_images(
    value: str | None,
    assets: Iterable[Any],
    resolve_path=None,
) -> list[dict[str, Any]]:
    """Attach self-contained image data to figure blocks without changing storage."""

    blocks = load_question_content(value)
    if not callable(resolve_path):
        return blocks
    by_id = {str(asset.id): asset for asset in assets}
    embedded_count = 0
    embedded_bytes = 0
    for block in blocks:
        if block.get("type") != "figure":
            continue
        asset = by_id.get(str(block.get("derived_asset_id") or ""))
        if asset is None:
            continue
        path = Path(resolve_path(asset.relative_path))
        if embedded_count >= _MAX_EMBEDDED_FIGURES:
            continue
        try:
            size = path.stat().st_size
            if size <= 0 or size > _MAX_EMBEDDED_FIGURE_BYTES:
                continue
            if embedded_bytes + size > _MAX_EMBEDDED_FIGURE_TOTAL_BYTES:
                continue
            with path.open("rb") as stream:
                payload = stream.read(_MAX_EMBEDDED_FIGURE_BYTES + 1)
        except OSError:
            continue
        if len(payload) != size or len(payload) > _MAX_EMBEDDED_FIGURE_BYTES:
            continue
        mime_type = str(asset.mime_type or "image/png")
        if not mime_type.startswith("image/"):
            continue
        block["image_data_uri"] = (
            f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"
        )
        embedded_count += 1
        embedded_bytes += len(payload)
    return blocks
