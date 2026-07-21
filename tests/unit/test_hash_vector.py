"""跨端哈希向量：与 protocol/test-vectors/hash-v1 及安卓 ObjectStoreTest 对齐。"""

from __future__ import annotations

from pathlib import Path

from yancuo_win.assets.object_store import ObjectStore

VECTOR = bytes([0xFF, 0xD8, 0xFF]) + b"yancuo-hash-vector"
EXPECTED = "bb35a354143fe5e6514b4c23ec0ac62f1f6c82d515c5d3989aa5b33eb3ea2bc6"


def test_shared_hash_vector(tmp_path: Path) -> None:
    store = ObjectStore(tmp_path / "objects")
    src = tmp_path / "vector.bin"
    src.write_bytes(VECTOR)
    assert ObjectStore.hash_file(src) == EXPECTED
    stored = store.store_copy(src, role="original")
    assert stored.sha256 == EXPECTED
    assert stored.relative_path == f"objects/bb/{EXPECTED}.bin"
    again = store.store_copy(src, role="original")
    assert again.already_existed is True
