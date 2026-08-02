"""外部编辑工作区导出 / 导入（阶段 D）。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from yancuo_win import __version__
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.config.settings import resource_path
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    AuditLog,
    Chapter,
    Problem,
    ReviewItem,
    ReviewSession,
    Subject,
    utcnow,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.archive import (
    ArchiveSecurityError,
    read_regular_file_limited,
    safe_relative_path,
)
from yancuo_win.import_export.markdown_problem import parse_problem_md, render_problem_md
from yancuo_win.review.changeset import snapshot_problem_fields

FORMAT_NAME = "yancuo-workspace"
FORMAT_VERSION = 1
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_MARKDOWN_BYTES = 16 * 1024 * 1024
MAX_WORKSPACE_PROBLEMS = 10_000

INSTRUCTIONS = """# 研错库外部编辑工作区

1. **不要**直接修改软件的 SQLite 数据库。
2. 只编辑 `problems/*/problem.md` 与（如需要）对照 `metadata.json`。
3. 不要删除或替换 `assets/` 中标记为 original 的文件期望；导入不会用其覆盖库内原图。
4. 编辑完成后，在研错库中选择「导入工作区」，在审核界面接受或拒绝变更。
5. 若导出后又在软件内改了同一题，导入会出现冲突，需手工选择保留哪一侧。
"""


class WorkspaceService:
    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime
        self.store = ObjectStore(runtime.paths.asset_objects_dir)

    def session(self) -> Session:
        return self.runtime.session_factory()

    @staticmethod
    def _read_workspace_text(path: Path, label: str, max_bytes: int) -> str:
        try:
            payload = read_regular_file_limited(path, max_bytes=max_bytes)
        except ArchiveSecurityError as exc:
            raise DomainError(f"无法读取 {label}：{exc}") from exc
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DomainError(f"{label} 不是有效 UTF-8 文本") from exc

    def _audit(
        self, session: Session, action: str, entity_type: str, entity_id: str, detail: dict
    ) -> None:
        session.add(
            AuditLog(
                id=new_id("audit"),
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                detail_json=json.dumps(detail, ensure_ascii=False),
                actor=self.runtime.identity.user_id,
            )
        )

    def export_workspace(self, problem_ids: list[str], dest_dir: Path | None = None) -> Path:
        if not problem_ids:
            raise DomainError("未选择题目")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        root = dest_dir or (self.runtime.paths.workspace_dir / f"workspace-{stamp}")
        if root.exists():
            raise DomainError(f"目标目录已存在：{root}")
        root.mkdir(parents=True)
        problems_dir = root / "problems"
        schemas_dir = root / "schemas"
        problems_dir.mkdir()
        schemas_dir.mkdir()

        # 复制协议 schema
        # Resolve from the checkout during development and from bundled
        # package resources after a wheel install.  A fixed ``parents[5]``
        # path breaks the latter case.
        repo_schema = resource_path("protocol", "schemas", "problem.schema.json")
        if repo_schema is not None and repo_schema.is_file():
            shutil.copy2(repo_schema, schemas_dir / "problem.schema.json")
        else:
            (schemas_dir / "problem.schema.json").write_text(
                json.dumps({"title": "problem metadata", "type": "object"}, indent=2),
                encoding="utf-8",
            )

        exported_ids: list[str] = []
        with self.session() as s:
            for pid in problem_ids:
                problem = s.scalars(
                    select(Problem)
                    .where(Problem.id == pid)
                    .options(selectinload(Problem.tags), selectinload(Problem.assets))
                ).first()
                if not problem or problem.status == "trashed":
                    continue
                subject_name = None
                chapter_name = None
                if problem.subject_id:
                    sub = s.get(Subject, problem.subject_id)
                    subject_name = sub.name if sub else None
                if problem.chapter_id:
                    ch = s.get(Chapter, problem.chapter_id)
                    chapter_name = ch.name if ch else None

                pdir = problems_dir / problem.id
                assets_dir = pdir / "assets"
                assets_dir.mkdir(parents=True)
                asset_files = []
                for asset in problem.assets:
                    src = self.store.resolve(asset.relative_path)
                    if not src.is_file():
                        raise DomainError(f"导出失败，资源缺失：{asset.relative_path}")
                    filename = f"{asset.role}{src.suffix.lower() or '.bin'}"
                    # 避免同 role 多文件覆盖
                    candidate = assets_dir / filename
                    n = 1
                    while candidate.exists():
                        candidate = assets_dir / f"{asset.role}_{n}{src.suffix.lower() or '.bin'}"
                        n += 1
                    shutil.copy2(src, candidate)
                    asset_files.append(
                        {
                            "id": asset.id,
                            "role": asset.role,
                            "filename": candidate.name,
                            "sha256": asset.sha256,
                            "mime_type": asset.mime_type,
                        }
                    )

                tags = [t.name for t in (problem.tags or [])]
                metadata = {
                    "id": problem.id,
                    "revision": problem.revision,
                    "status": problem.status,
                    "priority": problem.priority,
                    "title": problem.title,
                    "subject_name": subject_name,
                    "chapter_name": chapter_name,
                    "tags": tags,
                    "asset_files": asset_files,
                    "content_blocks": json.loads(problem.question_content_json or "[]"),
                }
                (pdir / "metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                md = render_problem_md(
                    front={
                        "id": problem.id,
                        "revision": problem.revision,
                        "priority": problem.priority,
                        "title": problem.title or "",
                        "status": problem.status,
                        "tags": tags,
                    },
                    sections={
                        "question_markdown": problem.question_markdown or "",
                        "user_answer": problem.user_answer or "",
                        "correct_answer": problem.correct_answer or "",
                        "solution_markdown": problem.solution_markdown or "",
                        "question_latex": problem.question_latex or "",
                        "error_analysis": problem.error_analysis or "",
                        "notes": problem.notes or "",
                    },
                )
                (pdir / "problem.md").write_text(md, encoding="utf-8")
                exported_ids.append(problem.id)

            self._audit(
                s,
                "workspace_exported",
                "workspace",
                str(root),
                {"problem_ids": exported_ids},
            )
            s.commit()

        if not exported_ids:
            shutil.rmtree(root, ignore_errors=True)
            raise DomainError("没有可导出的题目")

        manifest = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "database_id": self.runtime.identity.database_id,
            "app_version": __version__,
            "problem_ids": exported_ids,
            "warning": "Do not edit the SQLite database. Import changes via the app.",
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (root / "instructions.md").write_text(INSTRUCTIONS, encoding="utf-8")
        return root

    def import_workspace(self, workspace_dir: Path) -> dict[str, Any]:
        workspace_dir = Path(workspace_dir)
        manifest_path = workspace_dir / "manifest.json"
        try:
            manifest = json.loads(
                self._read_workspace_text(manifest_path, "manifest.json", MAX_MANIFEST_BYTES)
            )
        except json.JSONDecodeError as exc:
            raise DomainError(f"manifest.json 无效：{exc}") from exc
        if manifest.get("format") != FORMAT_NAME:
            raise DomainError("不是研错库工作区（format 不匹配）")
        if int(manifest.get("format_version") or 0) != FORMAT_VERSION:
            raise DomainError(
                f"工作区 format_version={manifest.get('format_version')} 不受支持（需要 {FORMAT_VERSION}）"
            )

        problems_root = workspace_dir / "problems"
        if problems_root.is_symlink() or not problems_root.is_dir():
            raise DomainError("缺少 problems/ 目录")
        problem_dirs = sorted(p for p in problems_root.iterdir() if p.is_dir())
        if len(problem_dirs) > MAX_WORKSPACE_PROBLEMS:
            raise DomainError(f"工作区题目目录过多（上限 {MAX_WORKSPACE_PROBLEMS}）")

        created_items: list[str] = []
        conflicts: list[str] = []
        errors: list[str] = []

        with self.session() as s:
            session = ReviewSession(
                id=new_id("rsess"),
                source="workspace",
                job_id=None,
                status="open",
                summary=f"外部工作区导入 · {workspace_dir.name}",
            )
            s.add(session)
            s.flush()

            for pdir in problem_dirs:
                try:
                    item_id, is_conflict = self._import_one_problem(s, session.id, pdir)
                    created_items.append(item_id)
                    if is_conflict:
                        conflicts.append(item_id)
                except DomainError as exc:
                    errors.append(f"{pdir.name}: {exc}")

            if not created_items and errors:
                s.rollback()
                raise DomainError("导入失败：\n" + "\n".join(errors))

            self._audit(
                s,
                "workspace_imported",
                "workspace",
                str(workspace_dir),
                {
                    "review_session_id": session.id,
                    "items": len(created_items),
                    "conflicts": len(conflicts),
                    "errors": errors,
                },
            )
            s.commit()
            return {
                "session_id": session.id,
                "items": created_items,
                "conflicts": conflicts,
                "errors": errors,
            }

    def _import_one_problem(self, s: Session, session_id: str, pdir: Path) -> tuple[str, bool]:
        meta_path = pdir / "metadata.json"
        md_path = pdir / "problem.md"
        if pdir.is_symlink():
            raise DomainError("题目目录不能是符号链接")
        try:
            metadata = json.loads(
                self._read_workspace_text(meta_path, "metadata.json", MAX_METADATA_BYTES)
            )
        except json.JSONDecodeError as exc:
            raise DomainError(f"metadata.json 无效：{exc}") from exc
        if not isinstance(metadata, dict) or "id" not in metadata or "revision" not in metadata:
            raise DomainError("metadata 缺少 id/revision")

        problem_id = str(metadata["id"])
        base_revision = int(metadata["revision"])
        # 校验资源引用存在（不写回原图）
        assets_dir = pdir / "assets"
        if assets_dir.is_symlink():
            raise DomainError("assets 目录不能是符号链接")
        for af in metadata.get("asset_files") or []:
            filename = af.get("filename")
            if not filename:
                raise DomainError("asset_files 缺少 filename")
            try:
                asset_path = safe_relative_path(assets_dir, str(filename))
            except ArchiveSecurityError as exc:
                raise DomainError(f"资源文件路径非法：{filename}") from exc
            if asset_path.is_symlink() or not asset_path.is_file():
                raise DomainError(f"资源文件缺失：{filename}")

        fm, sections = parse_problem_md(
            self._read_workspace_text(md_path, "problem.md", MAX_MARKDOWN_BYTES)
        )
        if fm.get("id") and str(fm["id"]) != problem_id:
            raise DomainError("problem.md 与 metadata.json 的 id 不一致")

        problem = s.scalars(
            select(Problem)
            .where(Problem.id == problem_id)
            .options(selectinload(Problem.tags), selectinload(Problem.assets))
        ).first()
        if not problem:
            raise DomainError(f"题库中不存在题目 {problem_id}")

        proposed: dict[str, Any] = {}
        if "content_blocks" in metadata:
            from yancuo_win.ai.base import normalize_content_blocks

            proposed["question_content_json"] = json.dumps(
                normalize_content_blocks(metadata.get("content_blocks")),
                ensure_ascii=False,
            )
        for field in (
            "question_markdown",
            "user_answer",
            "correct_answer",
            "solution_markdown",
            "question_latex",
            "error_analysis",
            "notes",
        ):
            if field in sections:
                proposed[field] = sections[field]
        if "title" in metadata and metadata["title"] is not None:
            proposed["title"] = metadata["title"]
        elif "title" in fm:
            proposed["title"] = fm.get("title")
        if "priority" in metadata:
            proposed["priority"] = int(metadata["priority"])
        elif "priority" in fm:
            proposed["priority"] = int(fm["priority"])
        tags = metadata.get("tags")
        if tags is None:
            tags = fm.get("tags")
        if isinstance(tags, list):
            proposed["tags"] = [str(t) for t in tags]

        before = snapshot_problem_fields(problem)
        is_conflict = problem.revision != base_revision
        status = "conflict" if is_conflict else "pending"
        uncertain: list[dict[str, str]] = []
        if is_conflict:
            uncertain.append(
                {
                    "field": "revision",
                    "content": f"导出 r{base_revision} / 库内 r{problem.revision}",
                    "reason": "导出后题库内题目已变更，不能静默覆盖",
                }
            )

        item = ReviewItem(
            id=new_id("ritem"),
            session_id=session_id,
            problem_id=problem.id,
            status=status,
            base_revision=base_revision,
            before_json=json.dumps(before, ensure_ascii=False),
            proposed_json=json.dumps(proposed, ensure_ascii=False),
            uncertain_json=json.dumps(uncertain, ensure_ascii=False),
        )
        s.add(item)
        s.flush()
        return item.id, is_conflict
