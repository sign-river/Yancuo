"""应用服务：科目、标签、错题、导入、备份、导出。UI 只依赖本层。"""

from __future__ import annotations

import json
import shutil
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    AiJob,
    AiJobItem,
    Asset,
    Chapter,
    Problem,
    ProblemOrigin,
    Prompt,
    ReviewItem,
    ReviewSession,
    Subject,
    Tag,
    Version,
    utcnow,
)
from yancuo_win.domain.rules import (
    DomainError,
    assert_transition,
    validate_priority,
    validate_status,
)
from yancuo_win.domain.review_rules import (
    REVIEW_GRADES,
    compute_next_review_at,
    is_due,
    mastery_from_grade,
    validate_grade,
)
from yancuo_win.domain.similarity import text_similarity
from yancuo_win.infrastructure.archive import (
    ArchiveSecurityError,
    iter_regular_files,
    safe_extract_zip,
    validate_zip_members,
)


@dataclass
class ProblemFilter:
    status: str | None = None  # None=非回收站日常；"all"=全部；具体状态；"library"=inbox+active
    subject_id: str | None = None
    chapter_id: str | None = None
    tag_id: str | None = None
    priority: int | None = None
    query: str | None = None
    include_trashed: bool = False
    due_for_review: bool = False


class AppServices:
    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime
        self.store = ObjectStore(runtime.paths.asset_objects_dir)

    def session(self) -> Session:
        return self.runtime.session_factory()

    def _record_sync_change(
        self,
        problem: Problem | str,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
        operation: str = "update",
    ) -> None:
        """将所有正式题目写操作统一登记到增量 Operation 日志。

        同步日志单独开事务，避免把 UI 用例的数据库事务和云端实现耦合在一起。
        """
        from yancuo_win.application.sync_service import SyncService

        SyncService(self.runtime).record_problem_update(
            problem, before=before, after=after, operation=operation
        )

    # ---- catalog ----

    def list_subjects(self) -> list[Subject]:
        with self.session() as s:
            rows = s.scalars(
                select(Subject).order_by(Subject.sort_order, Subject.name)
            ).all()
            s.expunge_all()
            return list(rows)

    def create_subject(self, name: str, sort_order: int = 0) -> Subject:
        name = name.strip()
        if not name:
            raise DomainError("科目名称不能为空")
        with self.session() as s:
            existing = s.scalar(select(Subject).where(Subject.name == name))
            if existing:
                raise DomainError(f"科目已存在：{name}")
            sub = Subject(id=new_id("sub"), name=name, sort_order=sort_order)
            s.add(sub)
            s.commit()
            s.refresh(sub)
            s.expunge(sub)
            return sub

    def rename_subject(self, subject_id: str, name: str) -> None:
        name = name.strip()
        if not name:
            raise DomainError("科目名称不能为空")
        with self.session() as s:
            sub = s.get(Subject, subject_id)
            if not sub:
                raise DomainError("科目不存在")
            sub.name = name
            sub.updated_at = utcnow()
            s.commit()

    def delete_subject(self, subject_id: str) -> None:
        with self.session() as s:
            sub = s.get(Subject, subject_id)
            if not sub:
                return
            chapter_count = s.scalar(
                select(func.count()).select_from(Chapter).where(Chapter.subject_id == subject_id)
            )
            problem_count = s.scalar(
                select(func.count()).select_from(Problem).where(Problem.subject_id == subject_id)
            )
            if chapter_count or problem_count:
                raise DomainError("科目下仍有章节或题目，无法删除")
            s.delete(sub)
            s.commit()

    def list_chapters(self, subject_id: str) -> list[Chapter]:
        with self.session() as s:
            rows = s.scalars(
                select(Chapter)
                .where(Chapter.subject_id == subject_id)
                .order_by(Chapter.sort_order, Chapter.name)
            ).all()
            s.expunge_all()
            return list(rows)

    def create_chapter(
        self, subject_id: str, name: str, parent_id: str | None = None, sort_order: int = 0
    ) -> Chapter:
        name = name.strip()
        if not name:
            raise DomainError("章节名称不能为空")
        with self.session() as s:
            if not s.get(Subject, subject_id):
                raise DomainError("科目不存在")
            ch = Chapter(
                id=new_id("ch"),
                subject_id=subject_id,
                parent_id=parent_id,
                name=name,
                sort_order=sort_order,
            )
            s.add(ch)
            s.commit()
            s.refresh(ch)
            s.expunge(ch)
            return ch

    def export_chapter_template(self, subject_id: str, dest: Path) -> Path:
        with self.session() as s:
            sub = s.get(Subject, subject_id)
            if not sub:
                raise DomainError("科目不存在")
            chapters = s.scalars(
                select(Chapter).where(Chapter.subject_id == subject_id)
            ).all()
            payload = {
                "format": "yancuo-chapter-template",
                "version": 1,
                "subject": {"name": sub.name},
                "chapters": [
                    {
                        "name": c.name,
                        "parent_name": next(
                            (p.name for p in chapters if p.id == c.parent_id), None
                        ),
                        "sort_order": c.sort_order,
                    }
                    for c in chapters
                ],
            }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return dest

    def import_chapter_template(self, path: Path) -> str:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("format") != "yancuo-chapter-template":
            raise DomainError("不是有效的章节模板")
        subject_name = str(raw["subject"]["name"])
        with self.session() as s:
            sub = s.scalar(select(Subject).where(Subject.name == subject_name))
            if not sub:
                sub = Subject(id=new_id("sub"), name=subject_name)
                s.add(sub)
                s.flush()
            name_to_id: dict[str, str] = {
                c.name: c.id
                for c in s.scalars(select(Chapter).where(Chapter.subject_id == sub.id))
            }
            for item in raw.get("chapters", []):
                name = str(item["name"])
                if name in name_to_id:
                    continue
                parent_name = item.get("parent_name")
                parent_id = name_to_id.get(parent_name) if parent_name else None
                ch = Chapter(
                    id=new_id("ch"),
                    subject_id=sub.id,
                    parent_id=parent_id,
                    name=name,
                    sort_order=int(item.get("sort_order") or 0),
                )
                s.add(ch)
                s.flush()
                name_to_id[name] = ch.id
            s.commit()
            return sub.id

    # ---- tags ----

    def list_tags(self) -> list[Tag]:
        with self.session() as s:
            rows = s.scalars(select(Tag).order_by(Tag.name)).all()
            s.expunge_all()
            return list(rows)

    def create_tag(self, name: str, color: str | None = None) -> Tag:
        name = name.strip()
        if not name:
            raise DomainError("标签名称不能为空")
        with self.session() as s:
            existing = s.scalar(select(Tag).where(Tag.name == name))
            if existing:
                raise DomainError(f"标签已存在：{name}")
            tag = Tag(id=new_id("tag"), name=name, color=color, is_system=False)
            s.add(tag)
            s.commit()
            s.refresh(tag)
            s.expunge(tag)
            return tag

    def delete_tag(self, tag_id: str) -> None:
        with self.session() as s:
            tag = s.get(Tag, tag_id)
            if not tag:
                return
            if tag.is_system:
                raise DomainError("系统标签不可删除")
            s.delete(tag)
            s.commit()

    def set_problem_tags(self, problem_id: str, tag_ids: list[str]) -> None:
        from yancuo_win.application.sync_service import sync_snapshot

        with self.session() as s:
            problem = s.scalars(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.tags))
            ).first()
            if not problem:
                raise DomainError("题目不存在")
            before = sync_snapshot(problem)
            tags = list(s.scalars(select(Tag).where(Tag.id.in_(tag_ids))).all()) if tag_ids else []
            problem.tags = tags
            problem.updated_at = utcnow()
            self._add_version(s, problem, source="manual", summary="更新标签")
            after = sync_snapshot(problem, [t.name for t in tags])
            s.commit()
            s.refresh(problem)
            s.expunge(problem)
        self._record_sync_change(problem, before=before, after=after)

    # ---- problems ----

    def _problem_query(self, filt: ProblemFilter) -> Select[tuple[Problem]]:
        stmt = select(Problem).options(
            selectinload(Problem.tags),
            selectinload(Problem.assets),
        )
        if filt.status == "library":
            stmt = stmt.where(Problem.status.in_(("inbox", "active")))
        elif filt.status == "all":
            pass
        elif filt.status:
            stmt = stmt.where(Problem.status == validate_status(filt.status))
        elif not filt.include_trashed:
            stmt = stmt.where(Problem.status != "trashed")

        if filt.subject_id:
            stmt = stmt.where(Problem.subject_id == filt.subject_id)
        if filt.chapter_id:
            stmt = stmt.where(Problem.chapter_id == filt.chapter_id)
        if filt.priority is not None:
            stmt = stmt.where(Problem.priority == filt.priority)
        if filt.tag_id:
            stmt = stmt.where(Problem.tags.any(Tag.id == filt.tag_id))
        if filt.query:
            q = f"%{filt.query.strip()}%"
            stmt = stmt.where(
                or_(
                    Problem.title.ilike(q),
                    Problem.question_markdown.ilike(q),
                    Problem.correct_answer.ilike(q),
                    Problem.notes.ilike(q),
                    Problem.source_book.ilike(q),
                    Problem.original_number.ilike(q),
                )
            )
        if filt.due_for_review:
            # 正式题库中到期或从未安排复习的题
            stmt = stmt.where(Problem.status == "active")
        return stmt.order_by(Problem.updated_at.desc())

    def list_problems(self, filt: ProblemFilter | None = None) -> list[Problem]:
        filt = filt or ProblemFilter(status="library")
        with self.session() as s:
            rows = list(s.scalars(self._problem_query(filt)).all())
            if filt.due_for_review:
                rows = [p for p in rows if is_due(p.next_review_at)]
            s.expunge_all()
            return list(rows)

    def get_problem(self, problem_id: str) -> Problem | None:
        with self.session() as s:
            problem = s.scalars(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.tags), selectinload(Problem.assets))
            ).first()
            if problem:
                s.expunge_all()
            return problem

    def count_problems(self, status: str | None = None) -> int:
        with self.session() as s:
            stmt = select(func.count()).select_from(Problem)
            if status:
                stmt = stmt.where(Problem.status == status)
            return int(s.scalar(stmt) or 0)

    def create_problem(
        self,
        *,
        title: str | None = None,
        question_markdown: str = "",
        status: str = "inbox",
        subject_id: str | None = None,
        chapter_id: str | None = None,
        priority: int = 3,
    ) -> Problem:
        from yancuo_win.application.sync_service import sync_snapshot

        validate_status(status)
        validate_priority(priority)
        with self.session() as s:
            problem = Problem(
                id=new_id("problem"),
                status=status,
                title=title,
                question_markdown=question_markdown,
                subject_id=subject_id,
                chapter_id=chapter_id,
                priority=priority,
                revision=1,
            )
            s.add(problem)
            s.flush()
            self._add_version(s, problem, source="manual", summary="创建题目", bump=False)
            # 创建操作需要完整快照，供另一台设备首次拉取时建立同一实体。
            after = sync_snapshot(problem, [])
            s.commit()
            s.refresh(problem)
            s.expunge(problem)
        self._record_sync_change(problem, before={}, after=after, operation="create")
        return problem

    def update_problem(self, problem_id: str, fields: dict[str, Any], *, summary: str = "编辑题目") -> Problem:
        from yancuo_win.application.sync_service import sync_snapshot

        with self.session() as s:
            problem = s.scalar(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.tags))
            )
            if not problem:
                raise DomainError("题目不存在")
            before = sync_snapshot(problem)
            allowed = {
                "title",
                "question_markdown",
                "question_latex",
                "user_answer",
                "correct_answer",
                "solution_markdown",
                "error_analysis",
                "notes",
                "source_book",
                "source_year",
                "page_number",
                "original_number",
                "problem_type",
                "subject_id",
                "chapter_id",
                "priority",
                "difficulty",
                "mastery",
                "is_favorite",
                "needs_redo",
                "allow_print",
                "human_confirmed",
            }
            changed = False
            for key, value in fields.items():
                if key not in allowed:
                    continue
                if key == "priority" and value is not None:
                    value = validate_priority(int(value))
                if getattr(problem, key) != value:
                    setattr(problem, key, value)
                    changed = True
            if changed:
                problem.updated_at = utcnow()
                self._add_version(s, problem, source="manual", summary=summary)
            after = sync_snapshot(problem)
            s.commit()
            s.refresh(problem)
            s.expunge(problem)
        if changed:
            self._record_sync_change(problem, before=before, after=after)
        return problem

    def set_problem_status(self, problem_id: str, status: str) -> None:
        from yancuo_win.application.sync_service import sync_snapshot

        with self.session() as s:
            problem = s.scalars(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.tags))
            ).first()
            if not problem:
                raise DomainError("题目不存在")
            before_status = problem.status
            before = sync_snapshot(problem)
            assert_transition(problem.status, status)
            problem.status = status
            problem.updated_at = utcnow()
            if status == "trashed":
                problem.deleted_at = utcnow()
            elif problem.deleted_at is not None:
                problem.deleted_at = None
            self._add_version(s, problem, source="manual", summary=f"状态 → {status}")
            after = sync_snapshot(problem)
            s.commit()
            s.refresh(problem)
            s.expunge(problem)
        operation = "update"
        if status == "trashed":
            operation = "delete"
        elif before_status == "trashed":
            operation = "undelete"
        self._record_sync_change(
            problem, before=before, after=after, operation=operation
        )

    def trash_problem(self, problem_id: str) -> None:
        self.set_problem_status(problem_id, "trashed")

    def restore_problem(self, problem_id: str, to_status: str = "inbox") -> None:
        if to_status not in {"inbox", "active"}:
            raise DomainError("恢复目标只能是 inbox 或 active")
        self.set_problem_status(problem_id, to_status)

    def purge_trashed(self) -> int:
        """Permanently delete trashed problems and their dependent workflow data.

        AI and review rows reference problems/assets without database-level cascade
        rules in schema v4.  Remove those rows first so the whole purge remains one
        atomic transaction.  Object-store files are removed only after commit and
        only when no surviving Asset row still references the same relative path.
        """

        relative_paths: set[str] = set()
        try:
            with self.session() as s:
                rows = list(
                    s.scalars(
                        select(Problem)
                        .where(Problem.status == "trashed")
                        .options(
                            selectinload(Problem.tags),
                            selectinload(Problem.assets),
                            selectinload(Problem.versions),
                        )
                    ).all()
                )
                if not rows:
                    return 0

                problem_ids = [problem.id for problem in rows]
                assets = [asset for problem in rows for asset in problem.assets]
                asset_ids = [asset.id for asset in assets]
                relative_paths = {asset.relative_path for asset in assets}

                item_scope = AiJobItem.problem_id.in_(problem_ids)
                if asset_ids:
                    item_scope = or_(item_scope, AiJobItem.asset_id.in_(asset_ids))
                affected_job_ids = set(
                    s.scalars(select(AiJobItem.job_id).where(item_scope)).all()
                )
                affected_session_ids = set(
                    s.scalars(
                        select(ReviewItem.session_id).where(
                            ReviewItem.problem_id.in_(problem_ids)
                        )
                    ).all()
                )

                s.execute(
                    delete(ReviewItem).where(ReviewItem.problem_id.in_(problem_ids))
                )
                s.execute(delete(AiJobItem).where(item_scope))
                s.execute(
                    delete(ProblemOrigin).where(
                        ProblemOrigin.problem_id.in_(problem_ids)
                    )
                )
                s.flush()

                for problem in rows:
                    problem.tags.clear()
                    s.delete(problem)
                s.flush()

                for session_id in affected_session_ids:
                    review_session = s.get(ReviewSession, session_id)
                    if review_session is None:
                        continue
                    has_items = s.scalar(
                        select(func.count(ReviewItem.id)).where(
                            ReviewItem.session_id == session_id
                        )
                    )
                    if not has_items:
                        s.delete(review_session)

                for job_id in affected_job_ids:
                    job = s.get(AiJob, job_id)
                    if job is None:
                        continue
                    remaining = list(
                        s.scalars(
                            select(AiJobItem).where(AiJobItem.job_id == job_id)
                        ).all()
                    )
                    if remaining:
                        job.total_items = len(remaining)
                        job.done_items = sum(item.status == "done" for item in remaining)
                        job.failed_items = sum(
                            item.status == "failed" for item in remaining
                        )
                        continue

                    for review_session in s.scalars(
                        select(ReviewSession).where(ReviewSession.job_id == job_id)
                    ).all():
                        has_items = s.scalar(
                            select(func.count(ReviewItem.id)).where(
                                ReviewItem.session_id == review_session.id
                            )
                        )
                        if has_items:
                            review_session.job_id = None
                        else:
                            s.delete(review_session)

                    # review_sessions.job_id has no ON DELETE cascade in schema v4;
                    # persist detach/delete decisions before removing the job.
                    s.flush()
                    prompt_key = job.prompt_key
                    s.delete(job)
                    s.flush()
                    if prompt_key == f"intake_{job_id}":
                        prompt_in_use = s.scalar(
                            select(func.count(AiJob.id)).where(
                                AiJob.prompt_key == prompt_key
                            )
                        )
                        if not prompt_in_use:
                            prompt = s.scalar(
                                select(Prompt).where(Prompt.key == prompt_key)
                            )
                            if prompt is not None:
                                s.delete(prompt)

                count = len(rows)
                s.commit()
        except SQLAlchemyError as exc:
            raise DomainError("清空回收站失败，所有删除操作均已回滚") from exc

        self._remove_unreferenced_asset_files(relative_paths)
        return count

    def _remove_unreferenced_asset_files(self, relative_paths: set[str]) -> None:
        """Best-effort cleanup after database references have been committed."""

        objects_root = self.store.objects_root.resolve()
        with self.session() as s:
            referenced = {
                path
                for path in relative_paths
                if s.scalar(
                    select(func.count(Asset.id)).where(Asset.relative_path == path)
                )
            }

        for relative_path in relative_paths - referenced:
            path = self.store.resolve(relative_path)
            try:
                path.relative_to(objects_root)
            except ValueError:
                self.runtime.logger.warning(
                    "skip unsafe asset cleanup path: %s", relative_path
                )
                continue
            try:
                if path.is_file():
                    path.chmod(path.stat().st_mode | stat.S_IWRITE)
                    path.unlink()
                if path.parent != objects_root:
                    path.parent.rmdir()
            except OSError as exc:
                self.runtime.logger.warning(
                    "orphan asset cleanup failed for %s: %s", path, exc
                )

    def promote_to_active(self, problem_id: str) -> None:
        self.set_problem_status(problem_id, "active")

    # ---- review ----

    def list_due_reviews(self) -> list[Problem]:
        return self.list_problems(ProblemFilter(status="active", due_for_review=True))

    def record_review(self, problem_id: str, grade: int) -> dict[str, Any]:
        """记录复习结果并安排下次日期。不自动删除任何题目。"""
        from yancuo_win.application.sync_service import sync_snapshot

        grade = validate_grade(grade)
        next_at = compute_next_review_at(grade)
        with self.session() as s:
            problem = s.scalars(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.tags))
            ).first()
            if not problem:
                raise DomainError("题目不存在")
            if problem.status == "trashed":
                raise DomainError("回收站题目不可复习")
            before = sync_snapshot(problem)
            problem.mastery = mastery_from_grade(grade)
            problem.next_review_at = next_at
            problem.review_count = int(problem.review_count or 0) + 1
            problem.updated_at = utcnow()
            if problem.status == "inbox":
                # 复习过的题进入正式库更合理
                problem.status = "active"
            self._add_version(
                s,
                problem,
                source="review",
                summary=f"复习打分 {grade}（{REVIEW_GRADES[grade]}）",
            )
            after = sync_snapshot(problem)
            s.commit()
            s.refresh(problem)
            s.expunge(problem)
        self._record_sync_change(problem, before=before, after=after)
        return {
            "problem_id": problem_id,
            "grade": grade,
            "label": REVIEW_GRADES[grade],
            "next_review_at": next_at.isoformat(),
            "interval_days": (next_at.date() - datetime.now(timezone.utc).date()).days,
            "review_count": problem.review_count,
        }

    def schedule_initial_review(self, problem_id: str) -> None:
        """将题目加入复习队列（下次=今天）。"""
        from yancuo_win.application.sync_service import sync_snapshot

        with self.session() as s:
            problem = s.scalars(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.tags))
            ).first()
            if not problem:
                raise DomainError("题目不存在")
            before = sync_snapshot(problem)
            problem.next_review_at = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            if problem.status == "inbox":
                problem.status = "active"
            problem.updated_at = utcnow()
            self._add_version(
                s,
                problem,
                source="review",
                summary="加入复习队列",
            )
            after = sync_snapshot(problem)
            s.commit()
            s.refresh(problem)
            s.expunge(problem)
        self._record_sync_change(problem, before=before, after=after)

    # ---- duplicates ----

    def find_hash_duplicates(self) -> list[dict[str, Any]]:
        """按原图 sha256 分组，仅提示不删除。"""
        with self.session() as s:
            assets = s.scalars(
                select(Asset).where(Asset.role == "original", Asset.problem_id.is_not(None))
            ).all()
            by_hash: dict[str, list[Asset]] = {}
            for a in assets:
                by_hash.setdefault(a.sha256, []).append(a)
            groups = []
            for sha, items in by_hash.items():
                if len(items) < 2:
                    continue
                groups.append(
                    {
                        "sha256": sha,
                        "problem_ids": [a.problem_id for a in items if a.problem_id],
                        "count": len(items),
                    }
                )
            return groups

    def find_text_similar(
        self, problem_id: str, *, threshold: float = 0.85, limit: int = 20
    ) -> list[dict[str, Any]]:
        """文本相似提示，不自动合并/删除。"""
        with self.session() as s:
            target = s.get(Problem, problem_id)
            if not target:
                raise DomainError("题目不存在")
            others = s.scalars(
                select(Problem).where(
                    Problem.id != problem_id,
                    Problem.status.in_(("inbox", "active", "archived")),
                )
            ).all()
            scored = []
            for p in others:
                score = text_similarity(
                    target.question_markdown or "", p.question_markdown or ""
                )
                if score >= threshold:
                    scored.append(
                        {
                            "problem_id": p.id,
                            "title": p.title,
                            "score": round(score, 4),
                        }
                    )
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:limit]

    def batch_update_problems(
        self,
        problem_ids: list[str],
        *,
        subject_id: str | None = None,
        chapter_id: str | None = None,
        priority: int | None = None,
        add_tag_id: str | None = None,
    ) -> int:
        from yancuo_win.application.sync_service import sync_snapshot

        if not problem_ids:
            return 0
        if priority is not None:
            validate_priority(priority)
        updated = 0
        sync_changes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        with self.session() as s:
            tag = s.get(Tag, add_tag_id) if add_tag_id else None
            for pid in problem_ids:
                problem = s.scalars(
                    select(Problem)
                    .where(Problem.id == pid)
                    .options(selectinload(Problem.tags))
                ).first()
                if not problem or problem.status == "trashed":
                    continue
                before = sync_snapshot(problem)
                changed = False
                if subject_id is not None and problem.subject_id != subject_id:
                    problem.subject_id = subject_id
                    changed = True
                if chapter_id is not None and problem.chapter_id != chapter_id:
                    problem.chapter_id = chapter_id
                    changed = True
                if priority is not None and problem.priority != priority:
                    problem.priority = priority
                    changed = True
                if tag is not None and tag not in problem.tags:
                    problem.tags = list(problem.tags) + [tag]
                    changed = True
                if changed:
                    problem.updated_at = utcnow()
                    self._add_version(s, problem, source="manual", summary="批量更新")
                    after = sync_snapshot(problem)
                    sync_changes.append((problem.id, before, after))
                    updated += 1
            s.commit()
        for problem_id, before, after in sync_changes:
            self._record_sync_change(problem_id, before=before, after=after)
        return updated

    def _add_version(
        self,
        session: Session,
        problem: Problem,
        *,
        source: str,
        summary: str,
        bump: bool = True,
    ) -> None:
        if bump:
            problem.revision += 1
        from yancuo_win.application.sync_service import sync_snapshot

        snap = sync_snapshot(problem)
        session.add(
            Version(
                id=new_id("ver"),
                problem_id=problem.id,
                revision=problem.revision,
                source=source,
                summary=summary,
                snapshot_json=json.dumps(snap, ensure_ascii=False),
                created_by=self.runtime.identity.user_id,
            )
        )

    # ---- image import ----

    def import_images(
        self,
        paths: Iterable[Path],
        *,
        into_status: str = "inbox",
        skip_duplicates: bool | None = None,
    ) -> dict[str, Any]:
        from yancuo_win.application.sync_service import sync_snapshot

        validate_status(into_status)
        skip = (
            self.runtime.settings.import_cfg.skip_duplicates
            if skip_duplicates is None
            else skip_duplicates
        )
        created: list[str] = []
        skipped: list[str] = []
        skipped_existing: list[dict[str, str]] = []
        sync_changes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        with self.session() as s:
            for path in paths:
                path = Path(path)
                stored = self.store.store_copy(path, role="original")
                if skip:
                    exists = s.scalar(
                        select(Asset)
                        .join(Problem, Problem.id == Asset.problem_id)
                        .where(
                            Asset.sha256 == stored.sha256,
                            Asset.role == "original",
                            Problem.status != "trashed",
                        )
                    )
                    if exists:
                        skipped.append(str(path))
                        skipped_existing.append(
                            {
                                "path": str(path),
                                "sha256": stored.sha256,
                                "existing_problem_id": exists.problem_id or "",
                                "existing_asset_id": exists.id,
                            }
                        )
                        continue
                problem = Problem(
                    id=new_id("problem"),
                    status=into_status,
                    title=path.stem,
                    question_markdown="",
                    revision=1,
                )
                s.add(problem)
                s.flush()
                asset = Asset(
                    id=new_id("asset"),
                    problem_id=problem.id,
                    role="original",
                    sha256=stored.sha256,
                    relative_path=stored.relative_path,
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                    is_immutable=True,
                )
                s.add(asset)
                self._add_version(
                    s, problem, source="import", summary=f"导入图片 {path.name}", bump=False
                )
                sync_changes.append((problem.id, {}, sync_snapshot(problem, [])))
                created.append(problem.id)
            s.commit()
        for problem_id, before, after in sync_changes:
            self._record_sync_change(
                problem_id, before=before, after=after, operation="create"
            )
        return {
            "created": created,
            "skipped": skipped,
            "skipped_existing": skipped_existing,
            "duplicate_tip": (
                f"检测到 {len(skipped)} 张重复原图，已跳过且未删除旧题"
                if skipped
                else ""
            ),
        }

    def import_folder(self, folder: Path, *, recursive: bool | None = None) -> dict[str, Any]:
        folder = Path(folder)
        if not folder.is_dir():
            raise DomainError(f"不是文件夹：{folder}")
        scan = (
            self.runtime.settings.import_cfg.scan_subfolders
            if recursive is None
            else recursive
        )
        exts = {e.lower() for e in self.runtime.settings.import_cfg.supported_extensions}
        files: list[Path] = []
        if scan:
            for p in folder.rglob("*"):
                if p.is_file() and p.suffix.lower() in exts and p.suffix.lower() != ".pdf":
                    files.append(p)
        else:
            for p in folder.iterdir():
                if p.is_file() and p.suffix.lower() in exts and p.suffix.lower() != ".pdf":
                    files.append(p)
        files.sort()
        return self.import_images(files)

    def attach_original_image(self, problem_id: str, path: Path) -> Asset:
        with self.session() as s:
            problem = s.get(Problem, problem_id)
            if not problem:
                raise DomainError("题目不存在")
            stored = self.store.store_copy(path, role="original")
            asset = Asset(
                id=new_id("asset"),
                problem_id=problem.id,
                role="original",
                sha256=stored.sha256,
                relative_path=stored.relative_path,
                mime_type=stored.mime_type,
                size_bytes=stored.size_bytes,
                is_immutable=True,
            )
            s.add(asset)
            problem.updated_at = utcnow()
            self._add_version(s, problem, source="import", summary="附加原图")
            s.commit()
            s.refresh(asset)
            s.expunge(asset)
            return asset

    def try_overwrite_original(self, asset_id: str) -> None:
        """供测试锁定：原图不可覆盖。"""
        with self.session() as s:
            asset = s.get(Asset, asset_id)
            if not asset:
                raise DomainError("资源不存在")
            self.store.assert_can_replace(asset.role, asset.is_immutable)

    # ---- backup ----

    def create_backup(self, dest_zip: Path | None = None) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = dest_zip or (self.runtime.paths.backup_dir / f"yancuo-backup-{stamp}.zip")
        dest.parent.mkdir(parents=True, exist_ok=True)
        db_path = self.runtime.paths.database
        asset_dir = self.runtime.paths.asset_dir
        identity = self.runtime.paths.identity_file

        # 释放连接以便复制 SQLite 文件
        self.runtime.engine.dispose()

        manifest = {
            "format": "yancuo-local-backup",
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database_id": self.runtime.identity.database_id,
            "schema_version": self.runtime.schema_version,
        }
        with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            zf.write(db_path, arcname="database/error_book.db")
            if identity.is_file():
                zf.write(identity, arcname="identity.json")
            if asset_dir.is_dir():
                try:
                    files = iter_regular_files(asset_dir)
                    for file in files:
                        zf.write(file, arcname=f"assets/{file.relative_to(asset_dir).as_posix()}")
                except ArchiveSecurityError as exc:
                    raise DomainError(f"备份失败，资源目录不安全：{exc}") from exc
        return dest

    def restore_backup(self, zip_path: Path, target_root: Path) -> Path:
        zip_path = Path(zip_path)
        target_root = Path(target_root)
        if not zip_path.is_file():
            raise DomainError("备份文件不存在")
        target_root.mkdir(parents=True, exist_ok=True)
        tmp = target_root / ".restore_tmp"
        final_staging = target_root / ".restore_final_staging"
        previous = target_root / ".restore_previous"
        for path in (tmp, final_staging, previous):
            if path.exists():
                shutil.rmtree(path)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                try:
                    infos = validate_zip_members(zf)
                except ArchiveSecurityError as exc:
                    raise DomainError(f"备份 ZIP 安全校验失败：{exc}") from exc
                names = {info.filename for info in infos}
                if "manifest.json" not in names or "database/error_book.db" not in names:
                    raise DomainError("无效的备份包")
                try:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise DomainError("备份 manifest.json 无效") from exc
                if not isinstance(manifest, dict) or manifest.get("format") != "yancuo-local-backup":
                    raise DomainError("备份格式不匹配")
                try:
                    backup_version = int(manifest.get("version") or 0)
                    package_schema = int(manifest.get("schema_version") or 0)
                except (TypeError, ValueError) as exc:
                    raise DomainError("备份 manifest 版本字段无效") from exc
                if backup_version != 1:
                    raise DomainError("备份版本不受支持")
                from yancuo_win.domain.identity import SCHEMA_VERSION

                if package_schema > SCHEMA_VERSION:
                    raise DomainError(
                        f"备份 schema_version={package_schema} 高于程序支持的 {SCHEMA_VERSION}，请升级软件"
                    )
                try:
                    safe_extract_zip(zf, tmp)
                except ArchiveSecurityError as exc:
                    raise DomainError(f"备份 ZIP 解压被拒绝：{exc}") from exc

            db_src = tmp / "database" / "error_book.db"
            assets_src = tmp / "assets"
            identity_src = tmp / "identity.json"
            if not db_src.is_file():
                raise DomainError("备份缺少数据库文件")

            final_staging.mkdir(parents=True)
            shutil.copy2(db_src, final_staging / "error_book.db")
            if assets_src.is_dir():
                try:
                    from yancuo_win.infrastructure.archive import copy_tree_no_symlinks

                    copy_tree_no_symlinks(assets_src, final_staging / "assets")
                except ArchiveSecurityError as exc:
                    raise DomainError(f"备份资源目录不安全：{exc}") from exc
            else:
                (final_staging / "assets" / "objects").mkdir(parents=True)
            if identity_src.is_file():
                shutil.copy2(identity_src, final_staging / "identity.json")

            # 在替换目标目录前打开 staging 数据库并执行迁移/核心表校验，
            # 这样损坏或过旧的普通 zip 也不会覆盖一个可用的数据根。
            from yancuo_win.data.db import make_engine
            from yancuo_win.data.migrate import migrate, verify_core_tables

            try:
                staged_engine = make_engine(final_staging / "error_book.db")
                try:
                    migrate(staged_engine)
                    missing = verify_core_tables(staged_engine)
                finally:
                    staged_engine.dispose()
            except DomainError:
                raise
            except Exception as exc:
                raise DomainError(f"备份数据库校验失败：{exc}") from exc
            if missing:
                raise DomainError(f"备份数据库缺少核心表：{', '.join(missing)}")

            db_dest = target_root / "error_book.db"
            assets_dest = target_root / "assets"
            identity_dest = target_root / "identity.json"
            destinations = [db_dest, assets_dest]
            if identity_src.is_file():
                destinations.append(identity_dest)
            previous.mkdir(parents=True)
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
                identity_final = final_staging / "identity.json"
                if identity_final.is_file():
                    shutil.move(str(identity_final), str(identity_dest))
                    moved_new.append(identity_dest)
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
            return target_root
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(final_staging, ignore_errors=True)
            shutil.rmtree(previous, ignore_errors=True)

    # ---- word export ----

    def export_problems_docx(self, problem_ids: list[str], dest: Path) -> Path:
        try:
            from docx import Document
        except ImportError as exc:  # pragma: no cover
            raise DomainError("未安装 python-docx，无法导出 Word") from exc

        problems = []
        for pid in problem_ids:
            p = self.get_problem(pid)
            if p and p.status != "trashed":
                problems.append(p)
        if not problems:
            raise DomainError("没有可导出的题目")

        doc = Document()
        doc.add_heading("研错库导出", level=0)
        for idx, p in enumerate(problems, start=1):
            title = p.title or f"题目 {idx}"
            doc.add_heading(f"{idx}. {title}", level=1)
            meta = f"优先级：{p.priority}　状态：{p.status}　ID：{p.id}"
            doc.add_paragraph(meta)
            doc.add_heading("原题", level=2)
            doc.add_paragraph(p.question_markdown or "（空）")
            if p.question_latex:
                doc.add_paragraph(f"LaTeX：{p.question_latex}")
            doc.add_heading("我的作答", level=2)
            doc.add_paragraph(p.user_answer or "（空）")
            doc.add_heading("正确答案", level=2)
            doc.add_paragraph(p.correct_answer or "（空）")
            doc.add_heading("解析", level=2)
            doc.add_paragraph(p.solution_markdown or "（空）")
            if p.error_analysis:
                doc.add_heading("错因", level=2)
                doc.add_paragraph(p.error_analysis)
            if p.notes:
                doc.add_heading("备注", level=2)
                doc.add_paragraph(p.notes)
            doc.add_paragraph("")

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.save(dest)
        return dest
