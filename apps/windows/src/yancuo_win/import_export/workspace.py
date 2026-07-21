"""外部编辑工作区导出 / 导入（阶段 D）。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.assets.object_store import ObjectStore
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
from yancuo_win.import_export.markdown_problem import parse_problem_md, render_problem_md
from yancuo_win.review.changeset import snapshot_problem_fields

FORMAT_NAME = "yancuo-workspace"
FORMAT_VERSION = 1

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

    def _audit(self, session: Session, action: str, entity_type: str, entity_id: str, detail: dict) -> None:
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
        root = dest_dir or (
            self.runtime.paths.workspace_dir / f"workspace-{stamp}"
        )
        if root.exists():
            raise DomainError(f"目标目录已存在：{root}")
        root.mkdir(parents=True)
        problems_dir = root / "problems"
        schemas_dir = root / "schemas"
        problems_dir.mkdir()
        schemas_dir.mkdir()

        # 复制协议 schema
        repo_schema = (
            Path(__file__).resolve().parents[5] / "protocol" / "schemas" / "problem.schema.json"
        )
        if repo_schema.is_file():
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
                            "role": asset.role,
                            "filename": candidate.name,
                            "sha256": asset.sha256,
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
            "app_version": "0.1.0c1",
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
        if not manifest_path.is_file():
            raise DomainError("缺少 manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DomainError(f"manifest.json 无效：{exc}") from exc
        if manifest.get("format") != FORMAT_NAME:
            raise DomainError("不是研错库工作区（format 不匹配）")
        if int(manifest.get("format_version") or 0) != FORMAT_VERSION:
            raise DomainError(
                f"工作区 format_version={manifest.get('format_version')} 不受支持（需要 {FORMAT_VERSION}）"
            )

        problems_root = workspace_dir / "problems"
        if not problems_root.is_dir():
            raise DomainError("缺少 problems/ 目录")

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

            for pdir in sorted(p for p in problems_root.iterdir() if p.is_dir()):
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

    def _import_one_problem(
        self, s: Session, session_id: str, pdir: Path
    ) -> tuple[str, bool]:
        meta_path = pdir / "metadata.json"
        md_path = pdir / "problem.md"
        if not meta_path.is_file():
            raise DomainError("缺少 metadata.json")
        if not md_path.is_file():
            raise DomainError("缺少 problem.md")
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DomainError(f"metadata.json 无效：{exc}") from exc
        if not isinstance(metadata, dict) or "id" not in metadata or "revision" not in metadata:
            raise DomainError("metadata 缺少 id/revision")

        problem_id = str(metadata["id"])
        base_revision = int(metadata["revision"])
        # 校验资源引用存在（不写回原图）
        assets_dir = pdir / "assets"
        for af in metadata.get("asset_files") or []:
            filename = af.get("filename")
            if not filename:
                raise DomainError("asset_files 缺少 filename")
            if not (assets_dir / str(filename)).is_file():
                raise DomainError(f"资源文件缺失：{filename}")

        fm, sections = parse_problem_md(md_path.read_text(encoding="utf-8"))
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
