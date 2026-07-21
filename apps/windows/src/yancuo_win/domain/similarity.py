"""文本相似（轻量，不调用 AI）。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text, flags=re.UNICODE)
    return text


def text_similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()
