"""Question figure materialization resource budgets."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from yancuo_win.application import question_content


def _figure(asset_id: str) -> dict[str, object]:
    return {
        "type": "figure",
        "derived_asset_id": asset_id,
        "content": asset_id,
        "source_region": {"x": 0, "y": 0, "width": 1, "height": 1},
    }


def test_content_blocks_embed_small_figure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    image = tmp_path / "figure.png"
    image.write_bytes(b"small-image")
    asset = SimpleNamespace(
        id="asset-small", relative_path="small", mime_type="image/png"
    )

    blocks = question_content.content_blocks_with_images(
        json.dumps([_figure("asset-small")]),
        [asset],
        lambda _relative: image,
    )

    encoded = blocks[0]["image_data_uri"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"small-image"


def test_content_blocks_skip_oversized_figure(
    tmp_path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    image = tmp_path / "figure.png"
    image.write_bytes(b"12345")
    asset = SimpleNamespace(
        id="asset-large", relative_path="large", mime_type="image/png"
    )
    monkeypatch.setattr(question_content, "_MAX_EMBEDDED_FIGURE_BYTES", 4)

    blocks = question_content.content_blocks_with_images(
        json.dumps([_figure("asset-large")]),
        [asset],
        lambda _relative: image,
    )

    assert "image_data_uri" not in blocks[0]
