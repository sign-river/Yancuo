"""`.ebpack` v1：以 snapshot.sqlite 为权威恢复路径。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from yancuo_win import __version__
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.data.models import Asset, Problem
from yancuo_win.domain.identity import DATA_FORMAT_VERSION, SCHEMA_VERSION
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.archive import (
    ArchiveSecurityError,
    copy_tree_no_symlinks,
    iter_regular_files,
    safe_extract_zip,
    validate_relative_checksum_path,
    validate_zip_members,
)

FORMAT_NAME = "graduate-mistake-book-ebpack"
FORMAT_VERSION = 1


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EbpackService:
    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime

    def export_ebpack(self, dest: Path | None = None) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = Path(dest) if dest else (
            self.runtime.paths.backup_dir / f"yancuo-{stamp}.ebpack"
        )
        if dest.suffix.lower() != ".ebpack":
            dest = dest.with_suffix(".ebpack")
        dest.parent.mkdir(parents=True, exist_ok=True)

        staging = self.runtime.paths.cache_dir / f"ebpack-export-{stamp}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)

        try:
            self.runtime.engine.dispose()
            db_src = self.runtime.paths.database
            if not db_src.is_file():
                raise DomainError("数据库不存在，无法导出")

            (staging / "database").mkdir()
            snapshot = staging / "database" / "snapshot.sqlite"
            shutil.copy2(db_src, snapshot)
            # FTS5 trigram is a Windows-side disposable index. Older Android
            # SQLite builds may not provide that tokenizer, so portable
            # snapshots carry the canonical projection but omit the virtual
            # table. Windows recreates it on the next bootstrap.
            with closing(sqlite3.connect(snapshot)) as connection:
                connection.execute("DROP TABLE IF EXISTS search_documents_fts")
                connection.commit()

            migrations = {
                "schema_version_at_export": self.runtime.schema_version,
                "data_format_version": DATA_FORMAT_VERSION,
                "note": (
                    "Restore uses snapshot.sqlite then app migrate(); "
                    "platform-local FTS is rebuilt on startup."
                ),
            }
            (staging / "database" / "migrations.json").write_text(
                json.dumps(migrations, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            assets_dst = staging / "assets"
            assets_dst.mkdir()
            objects_dst = assets_dst / "objects"
            if self.runtime.paths.asset_dir.is_dir():
                # 复制整个 asset_dir 内容（含 objects）
                for item in self.runtime.paths.asset_dir.iterdir():
                    target = assets_dst / item.name
                    if item.is_symlink():
                        raise DomainError(f"导出失败，资源目录包含符号链接：{item}")
                    if item.is_dir():
                        try:
                            copy_tree_no_symlinks(item, target)
                        except ArchiveSecurityError as exc:
                            raise DomainError(f"导出失败，资源目录不安全：{exc}") from exc
                    elif item.is_file():
                        shutil.copy2(item, target)
            objects_dst.mkdir(parents=True, exist_ok=True)

            object_entries = []
            if objects_dst.is_dir():
                for file in iter_regular_files(objects_dst):
                    rel = file.relative_to(assets_dst).as_posix()
                    object_entries.append(
                        {
                            "sha256": _sha256_file(file),
                            "relative_path": rel,
                            "size": file.stat().st_size,
                        }
                    )
            (assets_dst / "index.json").write_text(
                json.dumps({"objects": object_entries}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )

            identity_src = self.runtime.paths.identity_file
            if identity_src.is_file():
                shutil.copy2(identity_src, staging / "identity.json")

            problem_count, asset_count = self._counts()
            manifest = {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "application": "Yancuo",
                "app_version": __version__,
                "database_id": self.runtime.identity.database_id,
                "schema_version": self.runtime.schema_version,
                "data_format_version": DATA_FORMAT_VERSION,
                "problem_count": problem_count,
                "asset_count": asset_count,
                "encrypted": False,
                "encryption": None,
                "authoritative_payload": "database/snapshot.sqlite",
                "chunk": {"index": 1, "total": 1},
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            checksum_lines = self._build_checksums(staging)
            (staging / "checksums.sha256").write_text(
                "\n".join(checksum_lines) + "\n", encoding="utf-8"
            )

            if dest.exists():
                dest.unlink()
            with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file in iter_regular_files(staging):
                    zf.write(file, arcname=file.relative_to(staging).as_posix())
            return dest
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _counts(self) -> tuple[int, int]:
        with self.runtime.session_factory() as s:
            pc = int(s.scalar(select(func.count()).select_from(Problem)) or 0)
            ac = int(s.scalar(select(func.count()).select_from(Asset)) or 0)
            return pc, ac

    def _build_checksums(self, staging: Path) -> list[str]:
        lines: list[str] = []
        for rel in (
            "database/snapshot.sqlite",
            "database/migrations.json",
            "assets/index.json",
            "manifest.json",
        ):
            path = staging / rel
            if path.is_file():
                lines.append(f"{_sha256_file(path)}  {rel}")
        objects = staging / "assets" / "objects"
        if objects.is_dir():
            for file in iter_regular_files(objects):
                rel = file.relative_to(staging).as_posix()
                lines.append(f"{_sha256_file(file)}  {rel}")
        identity = staging / "identity.json"
        if identity.is_file():
            lines.append(f"{_sha256_file(identity)}  identity.json")
        return lines

    def verify_ebpack(self, pack: Path) -> dict[str, Any]:
        """校验包完整性并返回 manifest；失败抛 DomainError。"""
        pack = Path(pack)
        if not pack.is_file():
            raise DomainError("ebpack 文件不存在")
        try:
            with zipfile.ZipFile(pack, "r") as zf:
                try:
                    validate_zip_members(zf)
                except ArchiveSecurityError as exc:
                    raise DomainError(f"ebpack ZIP 安全校验失败：{exc}") from exc
                names = set(zf.namelist())
                required = {
                    "manifest.json",
                    "checksums.sha256",
                    "database/snapshot.sqlite",
                    "database/migrations.json",
                    "assets/index.json",
                }
                missing = sorted(required - names)
                if missing:
                    raise DomainError(f"ebpack 缺少条目：{', '.join(missing)}")
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                self._validate_manifest(manifest)

                # 解压到临时目录做校验
                tmp = self.runtime.paths.cache_dir / f"ebpack-verify-{pack.stem}"
                if tmp.exists():
                    shutil.rmtree(tmp)
                tmp.mkdir(parents=True)
                try:
                    try:
                        safe_extract_zip(zf, tmp)
                    except ArchiveSecurityError as exc:
                        raise DomainError(f"ebpack ZIP 解压被拒绝：{exc}") from exc
                    self._verify_checksums(tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                return manifest
        except zipfile.BadZipFile as exc:
            raise DomainError("ebpack 不是有效的 ZIP 包") from exc

    def _validate_manifest(self, manifest: dict[str, Any]) -> None:
        if not isinstance(manifest, dict):
            raise DomainError("ebpack manifest 必须是对象")
        if manifest.get("format") != FORMAT_NAME:
            raise DomainError("不是研错库 ebpack（format 不匹配）")
        try:
            format_version = int(manifest.get("format_version") or 0)
            pkg_schema = int(manifest.get("schema_version") or 0)
        except (TypeError, ValueError) as exc:
            raise DomainError("ebpack manifest 版本字段无效") from exc
        if format_version != FORMAT_VERSION:
            raise DomainError(
                f"ebpack format_version={manifest.get('format_version')} 不受支持"
            )
        if manifest.get("encrypted"):
            raise DomainError("v1 尚未实现加密包解密，拒绝导入")
        if pkg_schema > SCHEMA_VERSION:
            raise DomainError(
                f"包 schema_version={pkg_schema} 高于程序支持的 {SCHEMA_VERSION}，请升级软件"
            )

    def _verify_checksums(self, root: Path) -> None:
        table = root / "checksums.sha256"
        if not table.is_file():
            raise DomainError("缺少 checksums.sha256")
        for line in table.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                raise DomainError(f"checksums 行格式错误：{line[:80]}")
            expected, rel = parts[0].strip(), parts[1].strip()
            try:
                path = validate_relative_checksum_path(root, rel)
            except ArchiveSecurityError as exc:
                raise DomainError(f"checksums 路径非法：{rel}") from exc
            if not path.is_file():
                raise DomainError(f"checksums 引用缺失：{rel}")
            actual = _sha256_file(path)
            if actual != expected:
                raise DomainError(f"校验失败：{rel}")

    def restore_ebpack(self, pack: Path, target_root: Path) -> dict[str, Any]:
        """校验后恢复到目标数据根；失败不留下半套数据。"""
        pack = Path(pack)
        target_root = Path(target_root)
        manifest = self.verify_ebpack(pack)

        tmp = target_root / ".ebpack_restore_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        target_root.mkdir(parents=True, exist_ok=True)
        tmp.mkdir(parents=True)

        try:
            with zipfile.ZipFile(pack, "r") as zf:
                try:
                    safe_extract_zip(zf, tmp)
                except ArchiveSecurityError as exc:
                    raise DomainError(f"ebpack ZIP 解压被拒绝：{exc}") from exc
            self._verify_checksums(tmp)

            db_src = tmp / "database" / "snapshot.sqlite"
            assets_src = tmp / "assets"
            identity_src = tmp / "identity.json"

            db_dest = target_root / "error_book.db"
            assets_dest = target_root / "assets"
            identity_dest = target_root / "identity.json"

            # 写入临时最终位置，成功后再替换，避免半导入
            final_staging = target_root / ".ebpack_final_staging"
            previous = target_root / ".ebpack_previous"
            if final_staging.exists():
                shutil.rmtree(final_staging)
            if previous.exists():
                shutil.rmtree(previous)
            final_staging.mkdir()
            previous.mkdir()
            shutil.copy2(db_src, final_staging / "error_book.db")
            if assets_src.is_dir():
                try:
                    copy_tree_no_symlinks(assets_src, final_staging / "assets")
                except ArchiveSecurityError as exc:
                    raise DomainError(f"恢复资源目录不安全：{exc}") from exc
            else:
                (final_staging / "assets" / "objects").mkdir(parents=True)
            if identity_src.is_file():
                shutil.copy2(identity_src, final_staging / "identity.json")

            destinations = [db_dest, assets_dest]
            if identity_src.is_file():
                destinations.append(identity_dest)
            moved_old: list[tuple[Path, Path]] = []
            moved_new: list[Path] = []
            try:
                for destination in destinations:
                    if destination.exists() or destination.is_symlink():
                        old = previous / destination.name
                        shutil.move(str(destination), str(old))
                        moved_old.append((destination, old))
                for name in ("error_book.db", "assets"):
                    source = final_staging / name
                    destination = target_root / name
                    shutil.move(str(source), str(destination))
                    moved_new.append(destination)
                id_final = final_staging / "identity.json"
                if id_final.is_file():
                    shutil.move(str(id_final), str(identity_dest))
                    moved_new.append(identity_dest)

                from yancuo_win.data.db import make_engine
                from yancuo_win.data.migrate import (
                    ensure_search_index_schema,
                    migrate,
                    verify_core_tables,
                )

                try:
                    engine = make_engine(db_dest)
                    try:
                        version = migrate(engine)
                        if version >= 7:
                            ensure_search_index_schema(engine)
                        missing = verify_core_tables(engine)
                    finally:
                        engine.dispose()
                except DomainError:
                    raise
                except Exception as exc:
                    raise DomainError(f"恢复后的数据库校验失败：{exc}") from exc
                if missing:
                    raise DomainError(f"恢复后缺少表：{', '.join(missing)}")
            except Exception:
                for destination in reversed(moved_new):
                    try:
                        if destination.is_dir() and not destination.is_symlink():
                            shutil.rmtree(destination)
                        else:
                            destination.unlink(missing_ok=True)
                    except OSError:
                        pass
                for destination, old in reversed(moved_old):
                    if old.exists() or old.is_symlink():
                        shutil.move(str(old), str(destination))
                raise
            else:
                shutil.rmtree(previous, ignore_errors=True)

            return {
                "target_root": str(target_root),
                "schema_version": version,
                "manifest": manifest,
            }
        except Exception:
            # 清理临时；已替换的目标由调用方处理——此处若在 move 前失败则目标未动
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(target_root / ".ebpack_final_staging", ignore_errors=True)
            shutil.rmtree(target_root / ".ebpack_previous", ignore_errors=True)
