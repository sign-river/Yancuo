"""Unified manual/AI problem intake workflow.

This application-facing façade keeps the UI focused on one user task:
recording a problem.  It coordinates the existing object store, AI jobs,
review candidates, catalog data, versions, and sync operations so pages do not
need to know how those technical modules are connected.

The current v1 implementation deliberately reuses ``inbox`` problems as AI
staging records for schema-v4 compatibility.  They are promoted to ``active``
only after confirmation.  A later schema can move staging records into
dedicated intake tables without changing this public workflow API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from yancuo_win.ai.factory import get_provider
from yancuo_win.application.ai_service import AIService
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.services import AppServices
from yancuo_win.application.sync_service import SyncService, sync_snapshot
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    AiJobItem,
    Asset,
    AuditLog,
    Chapter,
    Problem,
    ReviewItem,
    ReviewSession,
    Subject,
    Tag,
    Version,
)
from yancuo_win.domain.rules import DomainError, validate_priority


_INTAKE_AI_FIELDS = frozenset(
    {
        "title",
        "question_markdown",
        "question_latex",
        "user_answer",
        "correct_answer",
        "solution_markdown",
        "error_analysis",
        "notes",
        "tags",
        "subject_name",
        "chapter_name",
        "problem_type",
        "priority",
    }
)

_COMMIT_FIELDS = frozenset(
    {
        "title",
        "subject_id",
        "chapter_id",
        "problem_type",
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
        "priority",
    }
)

_REQUIRED_TEXT_FIELDS = frozenset(
    {
        "question_markdown",
        "question_latex",
        "user_answer",
        "correct_answer",
        "solution_markdown",
        "error_analysis",
        "notes",
    }
)


@dataclass(frozen=True)
class AiIntakeSession:
    job_id: str
    problem_ids: list[str]
    skipped_files: list[str]


@dataclass(frozen=True)
class IntakeProgress:
    job_id: str
    status: str
    total: int
    done: int
    failed: int


@dataclass(frozen=True)
class IntakeCandidate:
    review_item_id: str
    problem_id: str
    status: str
    fields: dict[str, Any]
    uncertain: list[dict[str, Any]]
    original_image: Path | None


def _normalized_tags(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in list(values or [])[:20]:
        name = str(raw).strip()
        if not name or name in seen or len(name) > 128:
            continue
        seen.add(name)
        result.append(name)
    return result


class ProblemIntakeService:
    """Use-case façade consumed by the dedicated intake UI."""

    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime
        self.app = AppServices(runtime)
        self.ai = AIService(runtime)
        self.store = ObjectStore(runtime.paths.asset_objects_dir)

    def _validate_catalog(
        self,
        session,
        subject_id: str | None,
        chapter_id: str | None,
    ) -> None:
        if subject_id and session.get(Subject, subject_id) is None:
            raise DomainError("所选科目不存在")
        if chapter_id:
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                raise DomainError("所选章节不存在")
            if subject_id and chapter.subject_id != subject_id:
                raise DomainError("所选章节不属于当前科目")

    @staticmethod
    def _normalize_fields(fields: dict[str, Any]) -> dict[str, Any]:
        payload = {key: fields.get(key) for key in _COMMIT_FIELDS if key in fields}
        priority = payload.get("priority", 3)
        try:
            payload["priority"] = validate_priority(int(priority or 3))
        except (TypeError, ValueError) as exc:
            raise DomainError("优先级必须是 1–5 的整数") from exc

        for key in _REQUIRED_TEXT_FIELDS:
            value = payload.get(key, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise DomainError(f"字段 {key} 必须是文本")
            payload[key] = value
        for key in _COMMIT_FIELDS - _REQUIRED_TEXT_FIELDS - {"priority"}:
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                raise DomainError(f"字段 {key} 必须是文本或留空")
        return payload

    def commit_manual(
        self,
        fields: dict[str, Any],
        *,
        tag_names: list[str] | None = None,
        image_paths: list[Path] | None = None,
    ) -> Problem:
        """Atomically create a confirmed problem from the inline form."""

        payload = self._normalize_fields(fields)
        images = [Path(path) for path in (image_paths or [])]
        if not (str(payload.get("title") or "").strip() or payload["question_markdown"].strip() or images):
            raise DomainError("请至少填写标题、题干或添加一张原图")
        tags = _normalized_tags(tag_names)

        with self.runtime.session_factory() as session:
            self._validate_catalog(
                session,
                payload.get("subject_id"),
                payload.get("chapter_id"),
            )
            problem = Problem(
                id=new_id("problem"),
                status="active",
                human_confirmed=True,
                revision=1,
                **payload,
            )
            session.add(problem)
            session.flush()

            for name in tags:
                tag = session.scalar(select(Tag).where(Tag.name == name))
                if tag is None:
                    tag = Tag(id=new_id("tag"), name=name, is_system=False)
                    session.add(tag)
                    session.flush()
                problem.tags.append(tag)

            seen_hashes: set[str] = set()
            for image_path in images:
                stored = self.store.store_copy(image_path, role="original")
                if stored.sha256 in seen_hashes:
                    continue
                seen_hashes.add(stored.sha256)
                problem.assets.append(
                    Asset(
                        id=new_id("asset"),
                        role="original",
                        sha256=stored.sha256,
                        relative_path=stored.relative_path,
                        mime_type=stored.mime_type,
                        size_bytes=stored.size_bytes,
                        is_immutable=True,
                    )
                )

            after = sync_snapshot(problem, tags)
            session.add(
                Version(
                    id=new_id("ver"),
                    problem_id=problem.id,
                    revision=1,
                    source="manual",
                    summary="手动录题并确认入库",
                    snapshot_json=json.dumps(after, ensure_ascii=False),
                    created_by=self.runtime.identity.user_id,
                )
            )
            session.add(
                AuditLog(
                    id=new_id("audit"),
                    action="problem_intake_committed",
                    entity_type="problem",
                    entity_id=problem.id,
                    detail_json=json.dumps(
                        {"mode": "manual", "image_count": len(seen_hashes)},
                        ensure_ascii=False,
                    ),
                    actor=self.runtime.identity.user_id,
                )
            )
            problem_id = problem.id
            session.commit()

            created = session.scalars(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.tags), selectinload(Problem.assets))
            ).one()
            session.expunge_all()

        SyncService(self.runtime).record_problem_update(
            problem_id,
            before={},
            after=after,
            operation="create",
        )
        return created

    def _taxonomy_instruction(self) -> str:
        lines = [
            "请额外输出 subject_name、chapter_name、problem_type 和 priority（1-5）。",
            "subject_name/chapter_name 优先从以下现有分类中选择；无法判断时留空，不要编造。",
        ]
        for subject in self.app.list_subjects():
            chapters = self.app.list_chapters(subject.id)
            chapter_text = "、".join(chapter.name for chapter in chapters) or "（暂无章节）"
            lines.append(f"- {subject.name}：{chapter_text}")
        return "\n".join(lines)

    def start_ai(
        self,
        image_paths: list[Path],
        *,
        user_instruction: str = "",
    ) -> AiIntakeSession:
        """Import images as staging records and start a job-scoped intake."""

        if not image_paths:
            raise DomainError("请先添加需要识别的图片")
        # Validate credentials before import creates any inbox staging records.
        get_provider(self.runtime.settings).validate_configuration()
        result = self.app.import_images([Path(path) for path in image_paths], into_status="inbox")
        problem_ids = list(result["created"])
        if not problem_ids:
            raise DomainError(result.get("duplicate_tip") or "没有可识别的新图片")
        instruction_parts = [self._taxonomy_instruction()]
        if user_instruction.strip():
            instruction_parts.append("用户对本批图片的说明：\n" + user_instruction.strip())
        job = self.ai.create_structure_job(
            problem_ids,
            user_instruction="\n\n".join(instruction_parts),
            allowed_fields=_INTAKE_AI_FIELDS,
        )
        return AiIntakeSession(
            job_id=job.id,
            problem_ids=problem_ids,
            skipped_files=list(result.get("skipped") or []),
        )

    def progress(self, job_id: str) -> IntakeProgress:
        job = self.ai.get_job(job_id)
        if job is None:
            raise DomainError("录题任务不存在")
        return IntakeProgress(
            job_id=job.id,
            status=job.status,
            total=int(job.total_items or 0),
            done=int(job.done_items or 0),
            failed=int(job.failed_items or 0),
        )

    def latest_resumable_ai_job(self) -> str | None:
        """Find the newest unfinished intake job after navigation/app restart."""

        for job in self.ai.list_jobs(limit=50):
            if not str(job.prompt_key or "").startswith("intake_job_"):
                continue
            open_items = any(
                item.status in {"pending", "conflict"}
                for item in self.ai.list_review_items_for_job(job.id)
            )
            if open_items or job.status in {"pending", "running"} or int(job.failed_items or 0):
                return job.id
        return None

    def list_candidates(self, job_id: str) -> list[IntakeCandidate]:
        candidates: list[IntakeCandidate] = []
        for item in self.ai.list_review_items_for_job(job_id):
            try:
                before = json.loads(item.before_json)
                proposed = json.loads(item.proposed_json)
                uncertain = json.loads(item.uncertain_json)
            except json.JSONDecodeError as exc:
                raise DomainError("AI 结果 JSON 无效") from exc
            if not isinstance(before, dict) or not isinstance(proposed, dict):
                raise DomainError("AI 结果必须是对象")
            fields = dict(before)
            fields.update(proposed)
            problem = self.app.get_problem(item.problem_id)
            original: Path | None = None
            if problem:
                asset = next((a for a in problem.assets if a.role == "original"), None)
                if asset:
                    original = self.store.resolve(asset.relative_path)
            candidates.append(
                IntakeCandidate(
                    review_item_id=item.id,
                    problem_id=item.problem_id,
                    status=item.status,
                    fields=fields,
                    uncertain=uncertain if isinstance(uncertain, list) else [],
                    original_image=original,
                )
            )
        return candidates

    def failed_items(self, job_id: str) -> list[str]:
        with self.runtime.session_factory() as session:
            rows = session.scalars(
                select(AiJobItem).where(
                    AiJobItem.job_id == job_id,
                    AiJobItem.status == "failed",
                )
            ).all()
            return [row.error_message or row.id for row in rows]

    def commit_ai_candidate(
        self,
        review_item_id: str,
        fields: dict[str, Any],
        *,
        tag_names: list[str] | None = None,
    ) -> Problem:
        """Apply the edited candidate, then promote its staging problem."""

        payload = self._normalize_fields(fields)
        tags = _normalized_tags(tag_names)
        payload["tags"] = tags
        payload["human_confirmed"] = True

        with self.runtime.session_factory() as session:
            item = session.scalars(
                select(ReviewItem)
                .join(ReviewSession, ReviewSession.id == ReviewItem.session_id)
                .where(
                    ReviewItem.id == review_item_id,
                    ReviewSession.source == "ai",
                )
            ).first()
            if item is None or item.status not in {"pending", "conflict"}:
                raise DomainError("该 AI 候选题已经处理或不存在")
            self._validate_catalog(
                session,
                payload.get("subject_id"),
                payload.get("chapter_id"),
            )
            problem_id = item.problem_id
            item.proposed_json = json.dumps(payload, ensure_ascii=False)
            session.commit()

        self.ai.accept_review_item(review_item_id)
        problem = self.app.get_problem(problem_id)
        if problem is None:
            raise DomainError("候选题写入后不存在")
        if problem.status == "inbox":
            self.app.promote_to_active(problem_id)
        committed = self.app.get_problem(problem_id)
        if committed is None:
            raise DomainError("题目入库失败")
        return committed

    def reject_ai_candidate(self, review_item_id: str) -> None:
        item = self.ai.get_review_item(review_item_id)
        if item is None:
            return
        self.ai.reject_review_item(review_item_id)
        problem = self.app.get_problem(item.problem_id)
        if problem and problem.status != "trashed":
            self.app.trash_problem(problem.id)
