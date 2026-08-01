"""跨端哈希向量：与 protocol/test-vectors/hash-v1 及安卓 ObjectStoreTest 对齐。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.domain.rules import DomainError

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


@pytest.mark.parametrize(
    "value",
    ("../secret.txt", "objects/../../secret.txt", "C:/secret.txt", "", "objects/"),
)
def test_object_store_rejects_paths_outside_the_content_addressed_root(
    tmp_path: Path, value: str
) -> None:
    store = ObjectStore(tmp_path / "assets" / "objects")

    with pytest.raises(DomainError):
        store.resolve(value)


def test_object_store_refuses_to_trust_a_corrupted_existing_hash_path(
    tmp_path: Path,
) -> None:
    store = ObjectStore(tmp_path / "objects")
    source = tmp_path / "source.bin"
    source.write_bytes(VECTOR)
    stored = store.store_copy(source, role="derived")
    stored.absolute_path.write_bytes(b"corrupted")

    with pytest.raises(DomainError, match="已损坏"):
        store.store_copy(source, role="derived")


def test_object_store_writes_through_a_verified_temporary_file(tmp_path: Path) -> None:
    store = ObjectStore(tmp_path / "objects")
    source = tmp_path / "source.bin"
    source.write_bytes(VECTOR)

    stored = store.store_copy(source, role="derived")

    assert stored.absolute_path.read_bytes() == VECTOR
    assert not list(store.objects_root.rglob("*.tmp"))
