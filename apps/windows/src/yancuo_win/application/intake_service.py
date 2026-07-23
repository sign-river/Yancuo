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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QImage
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from yancuo_win.ai.base import normalize_region
from yancuo_win.ai.factory import get_provider
from yancuo_win.application.ai_service import AIService
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.services import AppServices
from yancuo_win.application.sync_service import SyncService, sync_snapshot
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    AiJob,
    AiJobItem,
    Asset,
    AuditLog,
    Chapter,
    IntakeAsset,
    IntakeCandidateRecord,
    IntakeSession,
    Problem,
    ReviewItem,
    ReviewSession,
    Subject,
    Tag,
    Version,
    utcnow,
)
from yancuo_win.domain.rules import DomainError, validate_priority
from yancuo_win.review.changeset import validate_and_filter_proposal


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
    intake_session_id: str
    problem_ids: list[str]
    skipped_files: list[str]


@dataclass(frozen=True)
class IntakeProgress:
    job_id: str
    status: str
    total: int
    done: int
    failed: int
    stage: str = "queued"
    stage_label: str = "等待处理"
    timings_ms: dict[str, float] = field(default_factory=dict)
    timing_samples: int = 0
    retry_count: int = 0


@dataclass(frozen=True)
class ResumableIntakeBatch:
    job_id: str
    session_id: str
    state: str
    pending_candidates: int
    failed_items: int
    instruction: str


@dataclass(frozen=True)
class RegionRecognitionProposal:
    proposal_id: str
    candidate_id: str
    old_fields: dict[str, Any]
    new_fields: dict[str, Any]
    uncertain: list[dict[str, Any]]
    region: dict[str, float]


@dataclass(frozen=True)
class IntakeCandidate:
    review_item_id: str
    problem_id: str
    status: str
    fields: dict[str, Any]
    uncertain: list[dict[str, Any]]
    original_image: Path | None
    region: dict[str, float]


@dataclass(frozen=True)
class ManualDraft:
    fields: dict[str, Any]
    tag_names: list[str]
    image_paths: list[Path]


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


def _split_region(region: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    box = normalize_region(region) or {
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 1.0,
    }
    first = dict(box)
    second = dict(box)
    if box["height"] >= box["width"]:
        half = box["height"] / 2
        first["height"] = half
        second["y"] = box["y"] + half
        second["height"] = half
    else:
        half = box["width"] / 2
        first["width"] = half
        second["x"] = box["x"] + half
        second["width"] = half
    return first, second


def _union_region(
    first: dict[str, float], second: dict[str, float]
) -> dict[str, float]:
    a = normalize_region(first) or {
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 1.0,
    }
    b = normalize_region(second) or {
        "x": 0.0,
        "y": 0.0,
        "width": 1.0,
        "height": 1.0,
    }
    x = min(a["x"], b["x"])
    y = min(a["y"], b["y"])
    right = max(a["x"] + a["width"], b["x"] + b["width"])
    bottom = max(a["y"] + a["height"], b["y"] + b["height"])
    return {"x": x, "y": y, "width": right - x, "height": bottom - y}


def _merge_candidate_fields(
    primary: dict[str, Any], secondary: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(primary)
    multiline = {
        "question_markdown",
        "question_latex",
        "user_answer",
        "correct_answer",
        "solution_markdown",
        "error_analysis",
        "notes",
    }
    for key, value in secondary.items():
        if key == "tags":
            merged[key] = _normalized_tags(
                [*list(merged.get(key) or []), *list(value or [])]
            )
            continue
        if key in multiline and str(value or "").strip():
            existing = str(merged.get(key) or "").strip()
            addition = str(value).strip()
            merged[key] = (
                existing
                if existing == addition
                else f"{existing}\n\n{addition}".strip()
            )
        elif not merged.get(key) and value not in (None, ""):
            merged[key] = value
    return merged


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
        source: str = "manual",
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
                    source=source,
                    summary=(
                        "AI 候选确认入库"
                        if source == "ai_intake"
                        else "手动录题并确认入库"
                    ),
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
                        {"mode": source, "image_count": len(seen_hashes)},
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

    def load_manual_draft(self) -> ManualDraft | None:
        with self.runtime.session_factory() as session:
            draft = session.scalars(
                select(IntakeSession)
                .where(
                    IntakeSession.mode == "manual",
                    IntakeSession.status == "draft",
                )
                .order_by(IntakeSession.updated_at.desc())
            ).first()
            if draft is None:
                return None
            try:
                payload = json.loads(draft.draft_json)
            except json.JSONDecodeError:
                payload = {}
            assets = session.scalars(
                select(IntakeAsset)
                .where(IntakeAsset.session_id == draft.id)
                .order_by(IntakeAsset.created_at)
            ).all()
            return ManualDraft(
                fields=(
                    payload.get("fields", {})
                    if isinstance(payload, dict)
                    else {}
                ),
                tag_names=(
                    _normalized_tags(payload.get("tags", []))
                    if isinstance(payload, dict)
                    else []
                ),
                image_paths=[
                    self.store.resolve(asset.relative_path)
                    for asset in assets
                    if self.store.resolve(asset.relative_path).is_file()
                ],
            )

    def save_manual_draft(
        self,
        fields: dict[str, Any],
        *,
        tag_names: list[str] | None = None,
        image_paths: list[Path] | None = None,
    ) -> str:
        images = [Path(path) for path in (image_paths or [])]
        removed_paths: set[str] = set()
        with self.runtime.session_factory() as session:
            draft = session.scalars(
                select(IntakeSession)
                .where(
                    IntakeSession.mode == "manual",
                    IntakeSession.status == "draft",
                )
                .order_by(IntakeSession.updated_at.desc())
            ).first()
            if draft is None:
                draft = IntakeSession(
                    id=new_id("intake"),
                    mode="manual",
                    status="draft",
                )
                session.add(draft)
                session.flush()
            draft.draft_json = json.dumps(
                {
                    "fields": fields,
                    "tags": _normalized_tags(tag_names),
                },
                ensure_ascii=False,
            )
            existing = {
                asset.sha256: asset
                for asset in session.scalars(
                    select(IntakeAsset).where(
                        IntakeAsset.session_id == draft.id
                    )
                ).all()
            }
            selected_hashes: set[str] = set()
            for path in images:
                if not path.is_file():
                    continue
                stored = self.store.store_copy(path, role="original")
                selected_hashes.add(stored.sha256)
                if stored.sha256 in existing:
                    continue
                session.add(
                    IntakeAsset(
                        id=new_id("iasset"),
                        session_id=draft.id,
                        role="original",
                        original_name=path.name,
                        sha256=stored.sha256,
                        relative_path=stored.relative_path,
                        mime_type=stored.mime_type,
                        size_bytes=stored.size_bytes,
                    )
                )
            for sha256, asset in existing.items():
                if sha256 not in selected_hashes:
                    removed_paths.add(asset.relative_path)
                    session.delete(asset)
            draft_id = draft.id
            session.commit()
        self.app._remove_unreferenced_asset_files(removed_paths)
        return draft_id

    def clear_manual_draft(self) -> None:
        removed_paths: set[str] = set()
        with self.runtime.session_factory() as session:
            drafts = session.scalars(
                select(IntakeSession).where(
                    IntakeSession.mode == "manual",
                    IntakeSession.status == "draft",
                )
            ).all()
            for draft in drafts:
                for asset in session.scalars(
                    select(IntakeAsset).where(
                        IntakeAsset.session_id == draft.id
                    )
                ).all():
                    removed_paths.add(asset.relative_path)
                    session.delete(asset)
                session.delete(draft)
            session.commit()
        self.app._remove_unreferenced_asset_files(removed_paths)

    def _taxonomy_instruction(self) -> str:
        lines = [
            "这是新题录入任务。请识别图片中的所有目标题目，并严格输出以下根结构：",
            '{"problems": [{"title": "题目1", "question_markdown": "...", '
            '"region": {"x": 0.05, "y": 0.10, "width": 0.90, "height": 0.35}, '
            '"uncertain_fields": []}, {"title": "题目2", "question_markdown": "...", '
            '"region": {"x": 0.05, "y": 0.50, "width": 0.90, "height": 0.40}, '
            '"uncertain_fields": []}]}',
            "即使只有一道题也使用 problems 数组；不要把多道题拼进同一个题干。",
            "region 是该题在原图中的归一化矩形坐标，左上角为原点，四个值均为 0 到 1；无法判断时使用整图 {\"x\":0,\"y\":0,\"width\":1,\"height\":1}。",
            "请额外输出 subject_name、chapter_name、problem_type 和 priority（1-5）。",
            "question_markdown、user_answer、correct_answer、solution_markdown、error_analysis 等 Markdown 字段中的公式必须使用 $...$ 或 $$...$$ 定界；不要输出无定界符的裸公式。",
            "question_latex 只写裸 LaTeX，不要再包 $、$$、\\(\\) 或 \\[\\] 定界符。",
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
        instruction_parts = [self._taxonomy_instruction()]
        if user_instruction.strip():
            instruction_parts.append("用户对本批图片的说明：\n" + user_instruction.strip())
        skipped: list[str] = []
        with self.runtime.session_factory() as session:
            intake_session = IntakeSession(
                id=new_id("intake"),
                mode="ai",
                status="draft",
                user_instruction=user_instruction.strip(),
            )
            session.add(intake_session)
            session.flush()
            asset_ids: list[str] = []
            batch_hashes: set[str] = set()
            for raw_path in image_paths:
                path = Path(raw_path)
                stored = self.store.store_copy(path, role="original")
                duplicate_problem = session.scalar(
                    select(Asset)
                    .join(Problem, Problem.id == Asset.problem_id)
                    .where(
                        Asset.sha256 == stored.sha256,
                        Asset.role == "original",
                        Problem.status != "trashed",
                    )
                )
                if duplicate_problem or stored.sha256 in batch_hashes:
                    skipped.append(str(path))
                    continue
                batch_hashes.add(stored.sha256)
                intake_asset = IntakeAsset(
                    id=new_id("iasset"),
                    session_id=intake_session.id,
                    role="original",
                    original_name=path.name,
                    sha256=stored.sha256,
                    relative_path=stored.relative_path,
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                )
                session.add(intake_asset)
                asset_ids.append(intake_asset.id)
            if not asset_ids:
                raise DomainError("没有可识别的新图片；所选图片可能已在题库中")
            intake_session_id = intake_session.id
            session.commit()
        try:
            job = self.ai.create_intake_structure_job(
                intake_session_id,
                asset_ids,
                user_instruction="\n\n".join(instruction_parts),
                allowed_fields=_INTAKE_AI_FIELDS,
            )
        except Exception:
            with self.runtime.session_factory() as session:
                failed_session = session.get(IntakeSession, intake_session_id)
                if failed_session:
                    failed_session.status = "cancelled"
                    failed_session.completed_at = utcnow()
                    session.commit()
            raise
        return AiIntakeSession(
            job_id=job.id,
            intake_session_id=intake_session_id,
            problem_ids=[],
            skipped_files=skipped,
        )

    def progress(self, job_id: str) -> IntakeProgress:
        job = self.ai.get_job(job_id)
        if job is None:
            raise DomainError("录题任务不存在")
        diagnostics = self.ai.get_job_diagnostics(job_id)
        return IntakeProgress(
            job_id=job.id,
            status=job.status,
            total=int(job.total_items or 0),
            done=int(job.done_items or 0),
            failed=int(job.failed_items or 0),
            stage=str(diagnostics["stage"]),
            stage_label=str(diagnostics["stage_label"]),
            timings_ms=dict(diagnostics["timings_ms"]),
            timing_samples=int(diagnostics["timing_samples"]),
            retry_count=int(diagnostics["retry_count"]),
        )

    def list_resumable_ai_batches(self) -> list[ResumableIntakeBatch]:
        """Return only dedicated intake batches that still require user action."""

        result: list[ResumableIntakeBatch] = []
        with self.runtime.session_factory() as session:
            rows = session.scalars(
                select(IntakeSession)
                .where(
                    IntakeSession.mode == "ai",
                    IntakeSession.status.in_(
                        {"draft", "processing", "review"}
                    ),
                    IntakeSession.job_id.is_not(None),
                )
                .order_by(IntakeSession.updated_at.desc())
            ).all()
            repaired = False
            for intake_session in rows:
                job = session.get(AiJob, intake_session.job_id)
                if job is None:
                    intake_session.status = "cancelled"
                    intake_session.completed_at = utcnow()
                    repaired = True
                    continue
                pending = int(
                    session.scalar(
                        select(func.count())
                        .select_from(IntakeCandidateRecord)
                        .where(
                            IntakeCandidateRecord.session_id == intake_session.id,
                            IntakeCandidateRecord.status == "pending",
                        )
                    )
                    or 0
                )
                failed = int(job.failed_items or 0)
                if pending:
                    state = "review"
                elif job.status in {"pending", "running"}:
                    state = "processing"
                elif failed:
                    state = "failed"
                else:
                    # Repair stale rows left by older versions instead of
                    # advertising an already completed batch forever.
                    intake_session.status = "completed"
                    intake_session.completed_at = (
                        intake_session.completed_at or utcnow()
                    )
                    repaired = True
                    continue
                result.append(
                    ResumableIntakeBatch(
                        job_id=job.id,
                        session_id=intake_session.id,
                        state=state,
                        pending_candidates=pending,
                        failed_items=failed,
                        instruction=intake_session.user_instruction,
                    )
                )
            if repaired:
                session.commit()
        return result

    def latest_resumable_ai_job(self) -> str | None:
        """Find the newest unfinished dedicated intake job."""

        batches = self.list_resumable_ai_batches()
        return batches[0].job_id if batches else None

    def abandon_ai_batch(self, job_id: str) -> None:
        """Close a resumable batch without touching already committed problems."""

        with self.runtime.session_factory() as session:
            intake_session = session.scalar(
                select(IntakeSession).where(
                    IntakeSession.mode == "ai",
                    IntakeSession.job_id == job_id,
                )
            )
            if intake_session is None:
                raise DomainError("待处理录题批次不存在")
            if intake_session.status in {"completed", "cancelled"}:
                return
            intake_session.status = "cancelled"
            intake_session.completed_at = utcnow()
            for candidate in session.scalars(
                select(IntakeCandidateRecord).where(
                    IntakeCandidateRecord.session_id == intake_session.id,
                    IntakeCandidateRecord.status == "pending",
                )
            ).all():
                candidate.status = "rejected"
                candidate.decided_at = utcnow()
            job = session.get(AiJob, job_id)
            if job is not None:
                job.status = "cancelled"
                job.finished_at = utcnow()
                job.updated_at = utcnow()
                for item in session.scalars(
                    select(AiJobItem).where(
                        AiJobItem.job_id == job_id,
                        AiJobItem.status.in_(
                            {"pending", "running", "failed"}
                        ),
                    )
                ).all():
                    item.status = "cancelled"
            session.commit()

    def list_candidates(self, job_id: str) -> list[IntakeCandidate]:
        candidates: list[IntakeCandidate] = []
        with self.runtime.session_factory() as session:
            intake_session = session.scalar(
                select(IntakeSession).where(IntakeSession.job_id == job_id)
            )
            if intake_session:
                rows = session.scalars(
                    select(IntakeCandidateRecord)
                    .where(
                        IntakeCandidateRecord.session_id == intake_session.id
                    )
                    .order_by(IntakeCandidateRecord.sort_order)
                ).all()
                assets = {
                    asset.id: asset
                    for asset in session.scalars(
                        select(IntakeAsset).where(
                            IntakeAsset.session_id == intake_session.id
                        )
                    ).all()
                }
                for item in rows:
                    try:
                        fields = json.loads(item.fields_json)
                        uncertain = json.loads(item.uncertain_json)
                        region = json.loads(item.region_json)
                    except json.JSONDecodeError as exc:
                        raise DomainError("AI 录题候选 JSON 无效") from exc
                    asset = assets.get(item.intake_asset_id)
                    candidates.append(
                        IntakeCandidate(
                            review_item_id=item.id,
                            problem_id=item.problem_id or "",
                            status=item.status,
                            fields=fields if isinstance(fields, dict) else {},
                            uncertain=(
                                uncertain
                                if isinstance(uncertain, list)
                                else []
                            ),
                            original_image=(
                                self.store.resolve(asset.relative_path)
                                if asset
                                else None
                            ),
                            region=normalize_region(region),
                        )
                    )
                return candidates
        for item in self.ai.list_review_items_for_job(job_id):
            try:
                before = json.loads(item.before_json)
                proposed = json.loads(item.proposed_json)
                uncertain = json.loads(item.uncertain_json)
                region = json.loads(item.region_json)
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
                    region=normalize_region(region),
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

    def update_ai_candidate_region(
        self, review_item_id: str, region: dict[str, Any] | None
    ) -> dict[str, float]:
        """Persist a human-corrected normalized source-image rectangle."""

        normalized = normalize_region(region)
        with self.runtime.session_factory() as session:
            intake_candidate = session.get(
                IntakeCandidateRecord, review_item_id
            )
            if intake_candidate is not None:
                if intake_candidate.status != "pending":
                    raise DomainError("该候选题已经处理或不存在")
                intake_candidate.region_json = json.dumps(
                    normalized, ensure_ascii=False
                )
                session.add(
                    AuditLog(
                        id=new_id("audit"),
                        action="intake_candidate_region_updated",
                        entity_type="intake_candidate",
                        entity_id=intake_candidate.id,
                        detail_json=json.dumps(
                            {"region": normalized}, ensure_ascii=False
                        ),
                        actor=self.runtime.identity.user_id,
                    )
                )
                session.commit()
                return normalized
            item = session.scalars(
                select(ReviewItem).where(
                    ReviewItem.id == review_item_id,
                    ReviewItem.status.in_({"pending", "conflict"}),
                )
            ).first()
            if item is None:
                raise DomainError("该候选题已经处理或不存在")
            item.region_json = json.dumps(normalized, ensure_ascii=False)
            session.add(
                AuditLog(
                    id=new_id("audit"),
                    action="ai_candidate_region_updated",
                    entity_type="review_item",
                    entity_id=item.id,
                    detail_json=json.dumps(
                        {"region": normalized}, ensure_ascii=False
                    ),
                    actor=self.runtime.identity.user_id,
                )
            )
            session.commit()
        return normalized

    def rerecognize_ai_candidate_region(
        self,
        review_item_id: str,
        current_fields: dict[str, Any],
        *,
        tag_names: list[str] | None = None,
    ) -> RegionRecognitionProposal:
        """Recognize a temporary crop and persist a comparison proposal."""

        with self.runtime.session_factory() as session:
            candidate = session.get(IntakeCandidateRecord, review_item_id)
            if candidate is None or candidate.status != "pending":
                raise DomainError("仅支持重新识别待确认的专用录题候选")
            asset = session.get(IntakeAsset, candidate.intake_asset_id)
            intake_session = session.get(IntakeSession, candidate.session_id)
            job = (
                session.get(AiJob, intake_session.job_id)
                if intake_session and intake_session.job_id
                else None
            )
            if asset is None or intake_session is None or job is None:
                raise DomainError("候选题的原图或 AI 任务不存在")
            try:
                region = normalize_region(json.loads(candidate.region_json))
                old_uncertain = json.loads(candidate.uncertain_json)
            except json.JSONDecodeError:
                region = {}
                old_uncertain = []
            if not region or (
                region["x"] <= 0.001
                and region["y"] <= 0.001
                and region["width"] >= 0.998
                and region["height"] >= 0.998
            ):
                raise DomainError("请先在原图上框选一个小于整图的题目区域")
            image_path = self.store.resolve(asset.relative_path)
            prompt_key = job.prompt_key
            model = job.model

        image = QImage(str(image_path))
        if image.isNull():
            raise DomainError("原图无法读取，不能按区域重新识别")
        crop_rect = QRect(
            round(region["x"] * image.width()),
            round(region["y"] * image.height()),
            max(1, round(region["width"] * image.width())),
            max(1, round(region["height"] * image.height())),
        ).intersected(image.rect())
        if crop_rect.width() < 8 or crop_rect.height() < 8:
            raise DomainError("当前框选区域太小，请扩大后再重新识别")

        crop_dir = self.runtime.paths.cache_dir / "region_recognition"
        crop_dir.mkdir(parents=True, exist_ok=True)
        crop_path = crop_dir / f"{new_id('crop')}.png"
        cropped = image.copy(crop_rect)
        if not cropped.save(str(crop_path), "PNG"):
            raise DomainError("无法生成区域识别临时图片")
        try:
            provider = get_provider(self.runtime.settings)
            provider.validate_configuration()
            base_prompt = self.ai.get_prompt(prompt_key).body
            result = provider.structure_from_image(
                image_path=str(crop_path),
                prompt=(
                    base_prompt
                    + "\n\n这是一张由用户明确框选的单题裁切图。"
                    "只识别裁切图中的这一道目标题，不要补充裁切区域以外的内容；"
                    "仍按既定结构输出。"
                ),
                model=model,
                timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
            )
        finally:
            crop_path.unlink(missing_ok=True)

        candidates = result.candidate_results()
        if not candidates:
            raise DomainError("区域重新识别没有返回题目")
        selected = candidates[0]
        filtered, validation_uncertain = validate_and_filter_proposal(
            selected.fields,
            allowed_fields=set(_INTAKE_AI_FIELDS),
            allow_delete=False,
        )
        old_fields = dict(current_fields)
        old_fields["tags"] = _normalized_tags(tag_names)
        new_fields = dict(old_fields)
        new_fields.update(filtered)
        uncertain = [*validation_uncertain, *selected.uncertain_fields]
        if len(candidates) > 1:
            uncertain.append(
                {
                    "field": "question_markdown",
                    "content": "",
                    "reason": "框选区域仍识别出多道题，当前仅展示第一道结果。",
                }
            )

        proposal_id = new_id("audit")
        with self.runtime.session_factory() as session:
            current = session.get(IntakeCandidateRecord, review_item_id)
            if current is None or current.status != "pending":
                raise DomainError("重新识别期间候选题状态已变化")
            session.add(
                AuditLog(
                    id=proposal_id,
                    action="intake_region_rerecognition_proposed",
                    entity_type="intake_candidate",
                    entity_id=review_item_id,
                    detail_json=json.dumps(
                        {
                            "old_fields": old_fields,
                            "old_uncertain": old_uncertain,
                            "new_fields": new_fields,
                            "uncertain": uncertain,
                            "region": region,
                            "model": result.model,
                        },
                        ensure_ascii=False,
                    ),
                    actor=self.runtime.identity.user_id,
                )
            )
            session.commit()
        return RegionRecognitionProposal(
            proposal_id=proposal_id,
            candidate_id=review_item_id,
            old_fields=old_fields,
            new_fields=new_fields,
            uncertain=uncertain,
            region=region,
        )

    def decide_region_rerecognition(
        self,
        proposal_id: str,
        *,
        apply_new: bool,
    ) -> None:
        with self.runtime.session_factory() as session:
            proposal = session.get(AuditLog, proposal_id)
            if (
                proposal is None
                or proposal.action != "intake_region_rerecognition_proposed"
            ):
                raise DomainError("区域重新识别提案不存在")
            already_decided = session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action.in_(
                        {
                            "intake_region_rerecognition_applied",
                            "intake_region_rerecognition_discarded",
                        }
                    ),
                    AuditLog.detail_json.like(
                        f'%"proposal_id": "{proposal_id}"%'
                    ),
                )
            )
            if already_decided:
                raise DomainError("区域重新识别提案已经处理")
            try:
                detail = json.loads(proposal.detail_json)
            except json.JSONDecodeError as exc:
                raise DomainError("区域重新识别提案损坏") from exc
            candidate = session.get(
                IntakeCandidateRecord, proposal.entity_id
            )
            if candidate is None or candidate.status != "pending":
                raise DomainError("候选题已经处理，无法应用重新识别结果")
            if apply_new:
                before_fields = detail.get("old_fields", {})
                before_uncertain = detail.get("old_uncertain", [])
                candidate.fields_json = json.dumps(
                    detail.get("new_fields", {}), ensure_ascii=False
                )
                candidate.uncertain_json = json.dumps(
                    detail.get("uncertain", []), ensure_ascii=False
                )
                decision_action = "intake_region_rerecognition_applied"
                decision_detail = {
                    "proposal_id": proposal_id,
                    "before_fields": before_fields,
                    "before_uncertain": before_uncertain,
                }
            else:
                decision_action = "intake_region_rerecognition_discarded"
                decision_detail = {"proposal_id": proposal_id}
            session.add(
                AuditLog(
                    id=new_id("audit"),
                    action=decision_action,
                    entity_type="intake_candidate",
                    entity_id=candidate.id,
                    detail_json=json.dumps(
                        decision_detail, ensure_ascii=False
                    ),
                    actor=self.runtime.identity.user_id,
                )
            )
            session.commit()

    def can_undo_region_rerecognition(self, candidate_id: str) -> bool:
        return self._latest_region_apply(candidate_id) is not None

    def undo_region_rerecognition(self, candidate_id: str) -> None:
        applied = self._latest_region_apply(candidate_id)
        if applied is None:
            raise DomainError("没有可撤回的区域重新识别结果")
        with self.runtime.session_factory() as session:
            candidate = session.get(IntakeCandidateRecord, candidate_id)
            current_apply = session.get(AuditLog, applied.id)
            if candidate is None or candidate.status != "pending" or current_apply is None:
                raise DomainError("候选题已经处理，无法撤回")
            detail = json.loads(current_apply.detail_json)
            candidate.fields_json = json.dumps(
                detail.get("before_fields", {}), ensure_ascii=False
            )
            candidate.uncertain_json = json.dumps(
                detail.get("before_uncertain", []), ensure_ascii=False
            )
            session.add(
                AuditLog(
                    id=new_id("audit"),
                    action="intake_region_rerecognition_undone",
                    entity_type="intake_candidate",
                    entity_id=candidate_id,
                    detail_json=json.dumps(
                        {"apply_audit_id": current_apply.id},
                        ensure_ascii=False,
                    ),
                    actor=self.runtime.identity.user_id,
                )
            )
            session.commit()

    def _latest_region_apply(self, candidate_id: str) -> AuditLog | None:
        with self.runtime.session_factory() as session:
            undone_ids: set[str] = set()
            for undo in session.scalars(
                select(AuditLog).where(
                    AuditLog.action == "intake_region_rerecognition_undone",
                    AuditLog.entity_id == candidate_id,
                )
            ).all():
                try:
                    value = json.loads(undo.detail_json).get("apply_audit_id")
                except json.JSONDecodeError:
                    continue
                if value:
                    undone_ids.add(str(value))
            rows = session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action == "intake_region_rerecognition_applied",
                    AuditLog.entity_id == candidate_id,
                )
                .order_by(AuditLog.created_at.desc())
            ).all()
            current = next((row for row in rows if row.id not in undone_ids), None)
            if current is not None:
                session.expunge(current)
            return current

    def split_ai_candidate(
        self,
        review_item_id: str,
        fields: dict[str, Any],
        *,
        tag_names: list[str] | None = None,
    ) -> tuple[str, str]:
        """Split one pending candidate into two independently editable regions."""

        payload = self._normalize_fields(fields)
        payload["tags"] = _normalized_tags(tag_names)
        with self.runtime.session_factory() as session:
            intake_candidate = session.get(
                IntakeCandidateRecord, review_item_id
            )
            if intake_candidate is not None:
                if intake_candidate.status != "pending":
                    raise DomainError("该候选题已经处理或不存在")
                try:
                    current_region = json.loads(
                        intake_candidate.region_json
                    )
                except json.JSONDecodeError:
                    current_region = {}
                first_region, second_region = _split_region(current_region)
                intake_candidate.fields_json = json.dumps(
                    payload, ensure_ascii=False
                )
                intake_candidate.region_json = json.dumps(
                    first_region, ensure_ascii=False
                )
                later = session.scalars(
                    select(IntakeCandidateRecord).where(
                        IntakeCandidateRecord.session_id
                        == intake_candidate.session_id,
                        IntakeCandidateRecord.sort_order
                        > intake_candidate.sort_order,
                    )
                ).all()
                for row in later:
                    row.sort_order += 1
                new_candidate = IntakeCandidateRecord(
                    id=new_id("icand"),
                    session_id=intake_candidate.session_id,
                    intake_asset_id=intake_candidate.intake_asset_id,
                    status="pending",
                    fields_json=json.dumps(payload, ensure_ascii=False),
                    uncertain_json=intake_candidate.uncertain_json,
                    region_json=json.dumps(
                        second_region, ensure_ascii=False
                    ),
                    sort_order=intake_candidate.sort_order + 1,
                )
                session.add(new_candidate)
                session.add(
                    AuditLog(
                        id=new_id("audit"),
                        action="intake_candidate_split",
                        entity_type="intake_candidate",
                        entity_id=intake_candidate.id,
                        detail_json=json.dumps(
                            {"new_candidate_id": new_candidate.id},
                            ensure_ascii=False,
                        ),
                        actor=self.runtime.identity.user_id,
                    )
                )
                new_id_value = new_candidate.id
                session.commit()
                return review_item_id, new_id_value
            item = session.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.id == review_item_id,
                    ReviewItem.status.in_({"pending", "conflict"}),
                )
            ).first()
            if item is None:
                raise DomainError("该候选题已经处理或不存在")
            problem = session.scalars(
                select(Problem)
                .where(Problem.id == item.problem_id)
                .options(selectinload(Problem.assets))
            ).first()
            if problem is None:
                raise DomainError("候选题暂存记录不存在")

            try:
                current_region = json.loads(item.region_json)
            except json.JSONDecodeError:
                current_region = {}
            first_region, second_region = _split_region(current_region)
            item.proposed_json = json.dumps(payload, ensure_ascii=False)
            item.region_json = json.dumps(first_region, ensure_ascii=False)

            clone = Problem(
                id=new_id("problem"),
                status="inbox",
                revision=1,
                human_confirmed=False,
            )
            for asset in problem.assets:
                clone.assets.append(
                    Asset(
                        id=new_id("asset"),
                        role=asset.role,
                        sha256=asset.sha256,
                        relative_path=asset.relative_path,
                        mime_type=asset.mime_type,
                        size_bytes=asset.size_bytes,
                        width=asset.width,
                        height=asset.height,
                        is_immutable=asset.is_immutable,
                    )
                )
            session.add(clone)
            session.flush()
            clone_snapshot = sync_snapshot(clone, [])
            session.add(
                Version(
                    id=new_id("ver"),
                    problem_id=clone.id,
                    revision=1,
                    source="ai_staging",
                    summary="人工拆分 AI 候选",
                    snapshot_json=json.dumps(clone_snapshot, ensure_ascii=False),
                    created_by=self.runtime.identity.user_id,
                )
            )
            new_item = ReviewItem(
                id=new_id("ritem"),
                session_id=item.session_id,
                problem_id=clone.id,
                status="pending",
                base_revision=clone.revision,
                before_json=json.dumps(clone_snapshot, ensure_ascii=False),
                proposed_json=json.dumps(payload, ensure_ascii=False),
                uncertain_json=item.uncertain_json,
                region_json=json.dumps(second_region, ensure_ascii=False),
            )
            session.add(new_item)
            session.add(
                AuditLog(
                    id=new_id("audit"),
                    action="ai_candidate_split",
                    entity_type="review_item",
                    entity_id=item.id,
                    detail_json=json.dumps(
                        {"new_review_item_id": new_item.id}, ensure_ascii=False
                    ),
                    actor=self.runtime.identity.user_id,
                )
            )
            new_item_id = new_item.id
            session.commit()
        return review_item_id, new_item_id

    def merge_ai_candidates(
        self,
        primary_review_item_id: str,
        secondary_review_item_id: str,
        primary_fields: dict[str, Any],
        *,
        tag_names: list[str] | None = None,
    ) -> str:
        """Merge two pending candidates from the same source image."""

        if primary_review_item_id == secondary_review_item_id:
            raise DomainError("不能合并同一个候选题")
        primary_payload = self._normalize_fields(primary_fields)
        primary_payload["tags"] = _normalized_tags(tag_names)
        with self.runtime.session_factory() as session:
            intake_primary = session.get(
                IntakeCandidateRecord, primary_review_item_id
            )
            intake_secondary = session.get(
                IntakeCandidateRecord, secondary_review_item_id
            )
            if intake_primary is not None or intake_secondary is not None:
                if (
                    intake_primary is None
                    or intake_secondary is None
                    or intake_primary.status != "pending"
                    or intake_secondary.status != "pending"
                ):
                    raise DomainError("待合并候选题已经处理或不存在")
                if (
                    intake_primary.session_id != intake_secondary.session_id
                    or intake_primary.intake_asset_id
                    != intake_secondary.intake_asset_id
                ):
                    raise DomainError("只能合并同一张原图的候选题")
                try:
                    secondary_fields = json.loads(
                        intake_secondary.fields_json
                    )
                    first_region = json.loads(intake_primary.region_json)
                    second_region = json.loads(
                        intake_secondary.region_json
                    )
                except json.JSONDecodeError as exc:
                    raise DomainError("候选题数据损坏，无法合并") from exc
                intake_primary.fields_json = json.dumps(
                    _merge_candidate_fields(
                        primary_payload, secondary_fields
                    ),
                    ensure_ascii=False,
                )
                intake_primary.region_json = json.dumps(
                    _union_region(first_region, second_region),
                    ensure_ascii=False,
                )
                intake_secondary.status = "rejected"
                intake_secondary.decided_at = utcnow()
                session.add(
                    AuditLog(
                        id=new_id("audit"),
                        action="intake_candidates_merged",
                        entity_type="intake_candidate",
                        entity_id=intake_primary.id,
                        detail_json=json.dumps(
                            {
                                "merged_candidate_id": (
                                    intake_secondary.id
                                )
                            },
                            ensure_ascii=False,
                        ),
                        actor=self.runtime.identity.user_id,
                    )
                )
                session.commit()
                return primary_review_item_id
            items = session.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.id.in_(
                        {primary_review_item_id, secondary_review_item_id}
                    ),
                    ReviewItem.status.in_({"pending", "conflict"}),
                )
            ).all()
            by_id = {item.id: item for item in items}
            primary = by_id.get(primary_review_item_id)
            secondary = by_id.get(secondary_review_item_id)
            if primary is None or secondary is None:
                raise DomainError("待合并候选题已经处理或不存在")
            if primary.session_id != secondary.session_id:
                raise DomainError("只能合并同一批次的候选题")
            problems = session.scalars(
                select(Problem)
                .where(Problem.id.in_({primary.problem_id, secondary.problem_id}))
                .options(selectinload(Problem.assets))
            ).all()
            problem_by_id = {problem.id: problem for problem in problems}
            first_problem = problem_by_id.get(primary.problem_id)
            second_problem = problem_by_id.get(secondary.problem_id)
            if first_problem is None or second_problem is None:
                raise DomainError("候选题暂存记录不存在")
            first_hashes = {asset.sha256 for asset in first_problem.assets}
            second_hashes = {asset.sha256 for asset in second_problem.assets}
            if not first_hashes.intersection(second_hashes):
                raise DomainError("只能合并来自同一张原图的候选题")
            try:
                secondary_fields = json.loads(secondary.proposed_json)
                first_region = json.loads(primary.region_json)
                second_region = json.loads(secondary.region_json)
            except json.JSONDecodeError as exc:
                raise DomainError("候选题数据损坏，无法合并") from exc
            if not isinstance(secondary_fields, dict):
                raise DomainError("候选题字段无效")

            primary.proposed_json = json.dumps(
                _merge_candidate_fields(primary_payload, secondary_fields),
                ensure_ascii=False,
            )
            primary.region_json = json.dumps(
                _union_region(first_region, second_region), ensure_ascii=False
            )
            secondary.status = "rejected"
            secondary.decided_at = utcnow()
            second_problem.status = "trashed"
            second_problem.deleted_at = utcnow()
            session.add(
                AuditLog(
                    id=new_id("audit"),
                    action="ai_candidates_merged",
                    entity_type="review_item",
                    entity_id=primary.id,
                    detail_json=json.dumps(
                        {"merged_review_item_id": secondary.id},
                        ensure_ascii=False,
                    ),
                    actor=self.runtime.identity.user_id,
                )
            )
            session.commit()
        return primary_review_item_id

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
            intake_candidate = session.get(
                IntakeCandidateRecord, review_item_id
            )
            if intake_candidate is not None:
                if intake_candidate.status != "pending":
                    raise DomainError("该 AI 候选题已经处理或不存在")
                intake_asset = session.get(
                    IntakeAsset, intake_candidate.intake_asset_id
                )
                if intake_asset is None:
                    raise DomainError("候选题原图不存在")
                original_image = self.store.resolve(
                    intake_asset.relative_path
                )
        if intake_candidate is not None:
            problem = self.commit_manual(
                payload,
                tag_names=tags,
                image_paths=[original_image],
                source="ai_intake",
            )
            with self.runtime.session_factory() as session:
                current = session.get(
                    IntakeCandidateRecord, review_item_id
                )
                if current is None or current.status != "pending":
                    raise DomainError("候选题入库状态发生变化")
                current.status = "committed"
                current.problem_id = problem.id
                current.fields_json = json.dumps(payload, ensure_ascii=False)
                current.decided_at = utcnow()
                intake_session = session.get(
                    IntakeSession, current.session_id
                )
                remaining = session.scalar(
                    select(func.count())
                    .select_from(IntakeCandidateRecord)
                    .where(
                        IntakeCandidateRecord.session_id
                        == current.session_id,
                        IntakeCandidateRecord.status == "pending",
                        IntakeCandidateRecord.id != current.id,
                    )
                )
                if intake_session and not remaining:
                    job = (
                        session.get(AiJob, intake_session.job_id)
                        if intake_session.job_id
                        else None
                    )
                    if job and int(job.failed_items or 0):
                        intake_session.status = "processing"
                        intake_session.completed_at = None
                    else:
                        intake_session.status = "completed"
                        intake_session.completed_at = utcnow()
                session.commit()
            return problem

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
        with self.runtime.session_factory() as session:
            candidate = session.get(
                IntakeCandidateRecord, review_item_id
            )
            if candidate is not None:
                if candidate.status != "pending":
                    return
                candidate.status = "rejected"
                candidate.decided_at = utcnow()
                intake_session = session.get(
                    IntakeSession, candidate.session_id
                )
                remaining = session.scalar(
                    select(func.count())
                    .select_from(IntakeCandidateRecord)
                    .where(
                        IntakeCandidateRecord.session_id
                        == candidate.session_id,
                        IntakeCandidateRecord.status == "pending",
                        IntakeCandidateRecord.id != candidate.id,
                    )
                )
                if intake_session and not remaining:
                    job = (
                        session.get(AiJob, intake_session.job_id)
                        if intake_session.job_id
                        else None
                    )
                    if job and int(job.failed_items or 0):
                        intake_session.status = "processing"
                        intake_session.completed_at = None
                    else:
                        intake_session.status = "completed"
                        intake_session.completed_at = utcnow()
                session.commit()
                return
        item = self.ai.get_review_item(review_item_id)
        if item is None:
            return
        self.ai.reject_review_item(review_item_id)
        problem = self.app.get_problem(item.problem_id)
        if problem and problem.status != "trashed":
            self.app.trash_problem(problem.id)
