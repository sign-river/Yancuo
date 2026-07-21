"""基于 SHA-256 的图片对象库。原图不可覆盖。"""

from __future__ import annotations

import hashlib
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path

from yancuo_win.domain.rules import DomainError, assert_asset_writable


@dataclass(frozen=True)
class StoredObject:
    sha256: str
    relative_path: str
    absolute_path: Path
    size_bytes: int
    mime_type: str | None
    already_existed: bool


class ObjectStore:
    def __init__(self, objects_root: Path) -> None:
        self.objects_root = objects_root
        self.objects_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def object_path(self, sha256: str, suffix: str) -> Path:
        return self.objects_root / sha256[:2] / f"{sha256}{suffix}"

    def relative_of(self, sha256: str, suffix: str) -> str:
        return f"objects/{sha256[:2]}/{sha256}{suffix}"

    def store_copy(self, source: Path, *, role: str = "original") -> StoredObject:
        if not source.is_file():
            raise DomainError(f"文件不存在：{source}")
        sha = self.hash_file(source)
        suffix = source.suffix.lower() or ".bin"
        dest = self.object_path(sha, suffix)
        rel = self.relative_of(sha, suffix)
        already = dest.is_file()
        if already:
            # 内容寻址：相同哈希视为同一对象，不覆盖写入
            pass
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            # 原图只读保护（文件系统层尽力而为）
            if role == "original":
                try:
                    dest.chmod(dest.stat().st_mode & ~0o222)
                except OSError:
                    pass

        mime, _ = mimetypes.guess_type(str(source))
        return StoredObject(
            sha256=sha,
            relative_path=rel,
            absolute_path=dest,
            size_bytes=dest.stat().st_size,
            mime_type=mime,
            already_existed=already,
        )

    def resolve(self, relative_path: str) -> Path:
        # relative_path 形如 objects/ab/ab….jpg，根为 asset_dir
        # objects_root 即 asset_dir/objects，故相对路径若含 objects/ 前缀需剥掉
        rel = relative_path.replace("\\", "/")
        if rel.startswith("objects/"):
            return (self.objects_root.parent / rel).resolve()
        return (self.objects_root / rel).resolve()

    def assert_can_replace(self, role: str, is_immutable: bool) -> None:
        assert_asset_writable(role, is_immutable)
