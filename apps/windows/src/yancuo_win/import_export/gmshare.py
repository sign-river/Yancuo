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

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import Asset, Problem, ProblemOrigin, Tag
from yancuo_win.domain.identity import DATA_FORMAT_VERSION
from yancuo_win.domain.rules import DomainError

FORMAT_NAME = "graduate-mistake-book-gmshare"
FORMAT_VERSION = 1

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
        dest = Path(dest) if dest else (
            self.runtime.paths.backup_dir / f"yancuo-share-{stamp}.gmshare"
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
                "app_version": "0.1.0",
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

    def _serialize_problem(
        self, problem: Problem, includes: ShareIncludeOptions
    ) -> dict[str, Any]:
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
                zf.extractall(staging)
            manifest = self._validate(staging)
            package_id = str(manifest["package_id"])
            lines = (staging / "problems.jsonl").read_text(encoding="utf-8").splitlines()
            created = 0
            skipped = 0
            created_ids: list[str] = []
            with self.runtime.session_factory() as s:
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    raw = json.loads(line)
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
                        priority=int(raw.get("priority") or 3),
                        revision=1,
                    )
                    s.add(problem)
                    s.flush()
                    for name in raw.get("tags") or []:
                        name = str(name).strip()
                        if not name:
                            continue
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
                        src = staging / "assets" / rel
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
                    created += 1
                    created_ids.append(problem.id)
                s.commit()
            return GmshareImportResult(
                created=created,
                skipped_duplicates=skipped,
                package_id=package_id,
                created_ids=created_ids,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _validate(self, root: Path) -> dict[str, Any]:
        for name in ("manifest.json", "checksums.sha256", "problems.jsonl"):
            if not (root / name).is_file():
                raise DomainError(f"分享包缺少 {name}")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != FORMAT_NAME:
            raise DomainError("不是研错库 .gmshare 包")
        if int(manifest.get("format_version") or 0) != FORMAT_VERSION:
            raise DomainError("gmshare format_version 不受支持")
        # 校验 checksums
        for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                continue
            digest, rel = parts
            path = root / rel
            if not path.is_file():
                raise DomainError(f"checksum 指向缺失文件：{rel}")
            if _sha256_file(path) != digest:
                raise DomainError(f"校验失败：{rel}")
        return manifest

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
