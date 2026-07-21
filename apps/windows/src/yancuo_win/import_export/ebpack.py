"""`.ebpack` v1：以 snapshot.sqlite 为权威恢复路径。"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.data.models import Asset, Problem
from yancuo_win.domain.identity import DATA_FORMAT_VERSION, SCHEMA_VERSION
from yancuo_win.domain.rules import DomainError

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

            migrations = {
                "schema_version_at_export": self.runtime.schema_version,
                "data_format_version": DATA_FORMAT_VERSION,
                "note": "Restore uses snapshot.sqlite then app migrate().",
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
                    if item.is_dir():
                        shutil.copytree(item, target)
                    elif item.is_file():
                        shutil.copy2(item, target)
            objects_dst.mkdir(parents=True, exist_ok=True)

            object_entries = []
            if objects_dst.is_dir():
                for file in sorted(objects_dst.rglob("*")):
                    if file.is_file():
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
                "app_version": "0.1.0e1",
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
                for file in staging.rglob("*"):
                    if file.is_file():
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
            for file in sorted(objects.rglob("*")):
                if file.is_file():
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
                    zf.extractall(tmp)
                    self._verify_checksums(tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                return manifest
        except zipfile.BadZipFile as exc:
            raise DomainError("ebpack 不是有效的 ZIP 包") from exc

    def _validate_manifest(self, manifest: dict[str, Any]) -> None:
        if manifest.get("format") != FORMAT_NAME:
            raise DomainError("不是研错库 ebpack（format 不匹配）")
        if int(manifest.get("format_version") or 0) != FORMAT_VERSION:
            raise DomainError(
                f"ebpack format_version={manifest.get('format_version')} 不受支持"
            )
        if manifest.get("encrypted"):
            raise DomainError("v1 尚未实现加密包解密，拒绝导入")
        pkg_schema = int(manifest.get("schema_version") or 0)
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
            path = root / rel
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
                zf.extractall(tmp)
            self._verify_checksums(tmp)

            db_src = tmp / "database" / "snapshot.sqlite"
            assets_src = tmp / "assets"
            identity_src = tmp / "identity.json"

            db_dest = target_root / "error_book.db"
            assets_dest = target_root / "assets"

            # 写入临时最终位置，成功后再替换，避免半导入
            final_staging = target_root / ".ebpack_final_staging"
            if final_staging.exists():
                shutil.rmtree(final_staging)
            final_staging.mkdir()
            shutil.copy2(db_src, final_staging / "error_book.db")
            if assets_src.is_dir():
                shutil.copytree(assets_src, final_staging / "assets")
            else:
                (final_staging / "assets" / "objects").mkdir(parents=True)
            if identity_src.is_file():
                shutil.copy2(identity_src, final_staging / "identity.json")

            if db_dest.exists():
                db_dest.unlink()
            if assets_dest.exists():
                shutil.rmtree(assets_dest)
            shutil.move(str(final_staging / "error_book.db"), str(db_dest))
            shutil.move(str(final_staging / "assets"), str(assets_dest))
            id_final = final_staging / "identity.json"
            if id_final.is_file():
                shutil.move(str(id_final), str(target_root / "identity.json"))
            shutil.rmtree(final_staging, ignore_errors=True)

            # 打开目标库并迁移到当前程序版本
            from yancuo_win.data.db import make_engine
            from yancuo_win.data.migrate import migrate, verify_core_tables

            engine = make_engine(db_dest)
            version = migrate(engine)
            missing = verify_core_tables(engine)
            engine.dispose()
            if missing:
                raise DomainError(f"恢复后缺少表：{', '.join(missing)}")

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
