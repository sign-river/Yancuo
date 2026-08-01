"""Question content-block helpers shared by readers and exporters."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Iterable

from yancuo_win.ai.base import normalize_content_blocks


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
    for block in blocks:
        if block.get("type") != "figure":
            continue
        asset = by_id.get(str(block.get("derived_asset_id") or ""))
        if asset is None:
            continue
        path = Path(resolve_path(asset.relative_path))
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        mime_type = str(asset.mime_type or "image/png")
        if not mime_type.startswith("image/"):
            continue
        block["image_data_uri"] = (
            f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"
        )
    return blocks
