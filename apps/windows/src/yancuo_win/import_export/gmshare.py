"""`.gmshare` 朋友分享包：脱敏导出 / 溯源去重导入。"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from yancuo_win import __version__
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import Asset, Problem, ProblemOrigin, Tag
from yancuo_win.domain.identity import DATA_FORMAT_VERSION
from yancuo_win.domain.rules import DomainError, validate_priority
from yancuo_win.infrastructure.archive import (
    ArchiveSecurityError,
    iter_regular_files,
    read_regular_file_limited,
    safe_extract_zip,
    safe_relative_path,
    validate_relative_checksum_path,
)

FORMAT_NAME = "graduate-mistake-book-gmshare"
FORMAT_VERSION = 1
MAX_SHARE_PROBLEMS = 10_000
MAX_SHARE_ASSET_REFERENCES = 10_000
MAX_SHARE_JSONL_LINE_BYTES = 4 * 1024 * 1024
MAX_CHECKSUM_LINE_BYTES = 4 * 1024
MAX_SHARE_METADATA_BYTES = 8 * 1024 * 1024
MAX_SHARE_PHYSICAL_LINES = 20_000

# 默认拒绝：无论 includes 如何，这些键不得写入 problems.jsonl
HARD_DENY_FIELDS = frozenset(
    {
        "user_answer",
        "notes",
        "next_review_at",
        "review_count",
        "mastery",
        "id",
        "device_id",
        "user_id",
        "database_id",
    }
)


@dataclass
class ShareIncludeOptions:
    question: bool = True
    correct_answer: bool = True
    solution: bool = True
    tags: bool = True
    source: bool = True
    original_images: bool = True
    error_analysis: bool = False
    user_answer: bool = False  # 即使 True 也被 HARD_DENY 挡住
    notes: bool = False
    review_history: bool = False


@dataclass
class GmshareExportResult:
    path: Path
    package_id: str
    problem_count: int
    asset_count: int


@dataclass
class GmshareImportResult:
    created: int
    skipped_duplicates: int
    package_id: str
    created_ids: list[str] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GmshareService:
    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime
        self.store = ObjectStore(runtime.paths.asset_objects_dir)

    def export_share(
        self,
        problem_ids: list[str] | None = None,
        *,
        dest: Path | None = None,
        title: str = "研错库分享",
        includes: ShareIncludeOptions | None = None,
    ) -> GmshareExportResult:
        includes = includes or ShareIncludeOptions()
        # 硬拒绝：不允许打开私人字段
        includes.user_answer = False
        includes.notes = False
        includes.review_history = False

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = (
            Path(dest)
            if dest
            else (self.runtime.paths.backup_dir / f"yancuo-share-{stamp}.gmshare")
        )
        if dest.suffix.lower() != ".gmshare":
            dest = dest.with_suffix(".gmshare")
        dest.parent.mkdir(parents=True, exist_ok=True)

        package_id = new_id("share")
        staging = self.runtime.paths.cache_dir / f"gmshare-export-{stamp}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        assets_root = staging / "assets"
        objects_dst = assets_root / "objects"
        objects_dst.mkdir(parents=True)

        try:
            rows: list[dict[str, Any]] = []
            asset_index: list[dict[str, Any]] = []
            with self.runtime.session_factory() as s:
                stmt = select(Problem).options(
                    selectinload(Problem.tags), selectinload(Problem.assets)
                )
                if problem_ids:
                    stmt = stmt.where(Problem.id.in_(problem_ids))
                else:
                    stmt = stmt.where(Problem.deleted_at.is_(None))
                problems = list(s.scalars(stmt).all())
                if not problems:
                    raise DomainError("没有可分享的题目")

                for problem in problems:
                    if problem.status == "trashed":
                        continue
                    rec = self._serialize_problem(problem, includes)
                    # 复制原图
                    asset_refs: list[dict[str, Any]] = []
                    if includes.original_images:
                        for asset in problem.assets:
                            if asset.role != "original":
                                continue
                            src = self.store.resolve(asset.relative_path)
                            if not src.is_file():
                                continue
                            rel = asset.relative_path.replace("\\", "/")
                            if rel.startswith("objects/"):
                                out = assets_root / rel
                            else:
                                out = objects_dst / rel
                            out.parent.mkdir(parents=True, exist_ok=True)
                            if not out.is_file():
                                shutil.copy2(src, out)
                            ref = {
                                "role": "original",
                                "sha256": asset.sha256,
                                "relative_path": rel
                                if rel.startswith("objects/")
                                else f"objects/{rel}",
                                "mime_type": asset.mime_type,
                            }
                            asset_refs.append(ref)
                            asset_index.append(ref)
                    rec["assets"] = asset_refs
                    # 最终清洗硬拒绝字段
                    for bad in HARD_DENY_FIELDS:
                        rec.pop(bad, None)
                    rows.append(rec)

            if not rows:
                raise DomainError("没有可分享的题目")

            (staging / "problems.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8",
            )
            (assets_root / "index.json").write_text(
                json.dumps({"assets": asset_index}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest = {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "package_id": package_id,
                "created_at": _utcnow_iso(),
                "title": title,
                "app_version": __version__,
                "data_format_version": DATA_FORMAT_VERSION,
                "problem_count": len(rows),
                "asset_count": len(asset_index),
                "includes": {
                    "question": includes.question,
                    "correct_answer": includes.correct_answer,
                    "solution": includes.solution,
                    "tags": includes.tags,
                    "source": includes.source,
                    "original_images": includes.original_images,
                    "error_analysis": includes.error_analysis,
                    "user_answer": False,
                    "notes": False,
                    "review_history": False,
                },
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._write_checksums(staging)
            self._zip_staging(staging, dest)
            return GmshareExportResult(
                path=dest,
                package_id=package_id,
                problem_count=len(rows),
                asset_count=len(asset_index),
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _serialize_problem(self, problem: Problem, includes: ShareIncludeOptions) -> dict[str, Any]:
        rec: dict[str, Any] = {"origin_problem_id": problem.id}
        if includes.question:
            rec["title"] = problem.title
            rec["question_markdown"] = problem.question_markdown or ""
            rec["question_latex"] = problem.question_latex or ""
        if includes.correct_answer:
            rec["correct_answer"] = problem.correct_answer or ""
        if includes.solution:
            rec["solution_markdown"] = problem.solution_markdown or ""
        if includes.error_analysis:
            rec["error_analysis"] = problem.error_analysis or ""
        if includes.tags:
            rec["tags"] = [t.name for t in (problem.tags or [])]
        if includes.source:
            rec["source_book"] = problem.source_book
            rec["source_year"] = problem.source_year
            rec["page_number"] = problem.page_number
            rec["original_number"] = problem.original_number
        rec["priority"] = problem.priority
        # 明确不写私人字段
        return rec

    def import_share(self, pack: Path) -> GmshareImportResult:
        pack = Path(pack)
        if not pack.is_file():
            raise DomainError("分享包不存在")
        staging = self.runtime.paths.cache_dir / f"gmshare-import-{pack.stem}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            with zipfile.ZipFile(pack, "r") as zf:
                try:
                    safe_extract_zip(zf, staging)
                except ArchiveSecurityError as exc:
                    raise DomainError(f"gmshare ZIP 解压被拒绝：{exc}") from exc
            manifest, rows = self._validate(staging)
            package_id = str(manifest["package_id"])
            created = 0
            skipped = 0
            created_ids: list[str] = []
            sync_changes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
            from yancuo_win.application.sync_service import sync_snapshot

            with self.runtime.session_factory() as s:
                for raw in rows:
                    origin_pid = str(raw.get("origin_problem_id") or "")
                    if not origin_pid:
                        continue
                    # 去重
                    exists = s.scalar(
                        select(ProblemOrigin).where(
                            ProblemOrigin.origin_package_id == package_id,
                            ProblemOrigin.origin_problem_id == origin_pid,
                        )
                    )
                    if exists:
                        skipped += 1
                        continue
                    for bad in HARD_DENY_FIELDS:
                        raw.pop(bad, None)
                    raw.pop("user_answer", None)
                    raw.pop("notes", None)

                    try:
                        priority = validate_priority(int(raw.get("priority") or 3))
                    except (TypeError, ValueError) as exc:
                        raise DomainError("分享包 priority 字段无效") from exc

                    problem = Problem(
                        id=new_id("problem"),
                        status="inbox",
                        title=raw.get("title"),
                        question_markdown=str(raw.get("question_markdown") or ""),
                        question_latex=str(raw.get("question_latex") or ""),
                        correct_answer=str(raw.get("correct_answer") or ""),
                        solution_markdown=str(raw.get("solution_markdown") or ""),
                        error_analysis=str(raw.get("error_analysis") or ""),
                        user_answer="",
                        notes="",
                        source_book=raw.get("source_book"),
                        source_year=raw.get("source_year"),
                        page_number=raw.get("page_number"),
                        original_number=raw.get("original_number"),
                        priority=priority,
                        revision=1,
                    )
                    s.add(problem)
                    s.flush()
                    raw_tags = raw.get("tags") or []
                    if not isinstance(raw_tags, list):
                        raise DomainError("分享包 tags 字段必须是数组")
                    seen_tags: set[str] = set()
                    for name in raw_tags[:20]:
                        name = str(name).strip()
                        if not name or name in seen_tags or len(name) > 128:
                            continue
                        seen_tags.add(name)
                        tag = s.scalar(select(Tag).where(Tag.name == name))
                        if not tag:
                            tag = Tag(id=new_id("tag"), name=name)
                            s.add(tag)
                            s.flush()
                        problem.tags.append(tag)
                    for ref in raw.get("assets") or []:
                        if not isinstance(ref, dict) or ref.get("role") != "original":
                            continue
                        rel = str(ref.get("relative_path") or "").replace("\\", "/")
                        try:
                            src = safe_relative_path(staging / "assets", rel)
                        except ArchiveSecurityError as exc:
                            raise DomainError(f"分享包资源路径非法：{rel}") from exc
                        if not src.is_file():
                            continue
                        stored = self.store.store_copy(src, role="original")
                        s.add(
                            Asset(
                                id=new_id("asset"),
                                problem_id=problem.id,
                                role="original",
                                sha256=stored.sha256,
                                relative_path=stored.relative_path,
                                mime_type=stored.mime_type,
                                size_bytes=stored.size_bytes,
                                is_immutable=True,
                            )
                        )
                    s.add(
                        ProblemOrigin(
                            problem_id=problem.id,
                            origin_package_id=package_id,
                            origin_problem_id=origin_pid,
                            imported_from="shared-package",
                        )
                    )
                    sync_changes.append(
                        (problem.id, {}, sync_snapshot(problem, [t.name for t in problem.tags]))
                    )
                    created += 1
                    created_ids.append(problem.id)
                s.commit()
            if sync_changes:
                from yancuo_win.application.sync_service import SyncService

                sync = SyncService(self.runtime)
                for problem_id, before, after in sync_changes:
                    sync.record_problem_update(
                        problem_id,
                        before=before,
                        after=after,
                        operation="create",
                    )
            return GmshareImportResult(
                created=created,
                skipped_duplicates=skipped,
                package_id=package_id,
                created_ids=created_ids,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _validate(self, root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        for name in ("manifest.json", "checksums.sha256", "problems.jsonl"):
            if not (root / name).is_file():
                raise DomainError(f"分享包缺少 {name}")
        try:
            manifest = json.loads(
                read_regular_file_limited(
                    root / "manifest.json", max_bytes=MAX_SHARE_METADATA_BYTES
                ).decode("utf-8")
            )
        except (ArchiveSecurityError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError("分享包 manifest.json 无效") from exc
        if not isinstance(manifest, dict):
            raise DomainError("分享包 manifest.json 必须是对象")
        if manifest.get("format") != FORMAT_NAME:
            raise DomainError("不是研错库 .gmshare 包")
        try:
            format_version = int(manifest.get("format_version") or 0)
            problem_count = int(manifest.get("problem_count") or 0)
            asset_count = int(manifest.get("asset_count") or 0)
        except (TypeError, ValueError) as exc:
            raise DomainError("gmshare manifest 数量或版本字段无效") from exc
        if format_version != FORMAT_VERSION:
            raise DomainError("gmshare format_version 不受支持")
        package_id = manifest.get("package_id")
        if not isinstance(package_id, str) or not package_id.strip() or len(package_id) > 128:
            raise DomainError("gmshare package_id 无效")
        if not 1 <= problem_count <= MAX_SHARE_PROBLEMS:
            raise DomainError("gmshare 题目数量无效或超限")
        if not 0 <= asset_count <= MAX_SHARE_ASSET_REFERENCES:
            raise DomainError("gmshare 资源引用数量无效或超限")

        # 校验 checksums
        checksummed_paths: set[str] = set()
        checksum_physical_lines = 0
        try:
            with (root / "checksums.sha256").open("rb") as checksum_stream:
                while line_bytes := checksum_stream.readline(MAX_CHECKSUM_LINE_BYTES + 1):
                    checksum_physical_lines += 1
                    if checksum_physical_lines > MAX_SHARE_PHYSICAL_LINES:
                        raise DomainError("checksum 物理行数超限")
                    if len(line_bytes) > MAX_CHECKSUM_LINE_BYTES:
                        raise DomainError("checksum 行过长")
                    line = line_bytes.decode("utf-8").strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("  ", 1)
                    if len(parts) != 2:
                        raise DomainError(f"checksum 行格式错误：{line[:80]}")
                    digest, rel = (part.strip() for part in parts)
                    if len(digest) != 64 or any(
                        char not in "0123456789abcdefABCDEF" for char in digest
                    ):
                        raise DomainError(f"checksum 摘要格式错误：{digest[:80]}")
                    try:
                        path = validate_relative_checksum_path(root, rel)
                    except ArchiveSecurityError as exc:
                        raise DomainError(f"checksum 路径非法：{rel}") from exc
                    canonical = path.relative_to(root.resolve()).as_posix()
                    if rel.replace("\\", "/") != canonical:
                        raise DomainError(f"checksum 路径不是规范相对路径：{rel}")
                    if canonical in checksummed_paths:
                        raise DomainError(f"checksum 路径重复：{rel}")
                    checksummed_paths.add(canonical)
                    if len(checksummed_paths) > 10_000:
                        raise DomainError("checksum 条目数量超限")
                    if not path.is_file():
                        raise DomainError(f"checksum 指向缺失文件：{rel}")
                    if _sha256_file(path) != digest.lower():
                        raise DomainError(f"校验失败：{rel}")
        except (OSError, UnicodeDecodeError) as exc:
            raise DomainError("分享包 checksums.sha256 无效") from exc

        actual_paths = {
            path.relative_to(root).as_posix()
            for path in iter_regular_files(root)
            if path.relative_to(root).as_posix() != "checksums.sha256"
        }
        if checksummed_paths != actual_paths:
            missing = sorted(actual_paths - checksummed_paths)
            extra = sorted(checksummed_paths - actual_paths)
            detail = []
            if missing:
                detail.append("缺少 " + "、".join(missing[:5]))
            if extra:
                detail.append("多余 " + "、".join(extra[:5]))
            raise DomainError("checksum 未完整覆盖分享包文件：" + "；".join(detail))

        rows: list[dict[str, Any]] = []
        problem_physical_lines = 0
        try:
            with (root / "problems.jsonl").open("rb") as problem_stream:
                while line_bytes := problem_stream.readline(MAX_SHARE_JSONL_LINE_BYTES + 1):
                    problem_physical_lines += 1
                    if problem_physical_lines > MAX_SHARE_PHYSICAL_LINES:
                        raise DomainError("分享包题目物理行数超限")
                    if len(line_bytes) > MAX_SHARE_JSONL_LINE_BYTES:
                        raise DomainError("分享包单条题目记录过大")
                    if not line_bytes.strip():
                        continue
                    row = json.loads(line_bytes.decode("utf-8"))
                    if not isinstance(row, dict):
                        raise DomainError("分享包题目记录必须是对象")
                    rows.append(row)
                    if len(rows) > MAX_SHARE_PROBLEMS:
                        raise DomainError("分享包题目记录数量超限")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError("分享包 problems.jsonl 无效") from exc
        if len(rows) != problem_count:
            raise DomainError("gmshare manifest 与题目记录数量不一致")

        index_path = root / "assets" / "index.json"
        try:
            asset_index = json.loads(
                read_regular_file_limited(index_path, max_bytes=MAX_SHARE_METADATA_BYTES).decode(
                    "utf-8"
                )
            )
        except (ArchiveSecurityError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError("分享包 assets/index.json 无效") from exc
        indexed_assets = asset_index.get("assets") if isinstance(asset_index, dict) else None
        if not isinstance(indexed_assets, list) or not all(
            isinstance(item, dict) for item in indexed_assets
        ):
            raise DomainError("分享包资源索引必须包含 assets 对象数组")
        if len(indexed_assets) != asset_count:
            raise DomainError("gmshare manifest 与资源索引数量不一致")
        referenced_assets = 0
        for row in rows:
            assets = row.get("assets") or []
            if not isinstance(assets, list) or not all(isinstance(item, dict) for item in assets):
                raise DomainError("分享包题目资源引用必须是对象数组")
            referenced_assets += len(assets)
            if referenced_assets > MAX_SHARE_ASSET_REFERENCES:
                raise DomainError("分享包题目资源引用数量超限")
        if referenced_assets != asset_count:
            raise DomainError("gmshare 题目资源引用与资源索引数量不一致")
        return manifest, rows

    def _write_checksums(self, root: Path) -> None:
        lines: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel == "checksums.sha256":
                continue
            lines.append(f"{_sha256_file(path)}  {rel}")
        (root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _zip_staging(self, staging: Path, dest: Path) -> None:
        if dest.exists():
            dest.unlink()
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in staging.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(staging).as_posix())
