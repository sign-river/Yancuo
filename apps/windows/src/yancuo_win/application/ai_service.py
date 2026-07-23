"""AI 任务与审核应用服务。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from yancuo_win.ai.base import AIProvider, normalize_region
from yancuo_win.ai.factory import get_provider
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    AiJob,
    AiJobItem,
    Asset,
    AuditLog,
    IntakeAsset,
    IntakeCandidateRecord,
    IntakeSession,
    Problem,
    Prompt,
    ReviewItem,
    ReviewSession,
    Tag,
    Version,
    utcnow,
)
from yancuo_win.domain.rules import DomainError, validate_priority, validate_status
from yancuo_win.review.changeset import (
    DEFAULT_ALLOWED_FIELDS,
    field_diffs,
    validate_and_filter_proposal,
)


# Fields that may be materialized from a human-reviewed proposal.  In
# particular, never accept identity, revision, or audit timestamps from a
# package/remote operation: the service owns those values and advances the
# revision exactly once when the proposal is accepted.
_REVIEW_MUTABLE_FIELDS = frozenset(
    {
        "status",
        "subject_id",
        "chapter_id",
        "problem_type",
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
        "priority",
        "difficulty",
        "is_favorite",
        "needs_redo",
        "allow_print",
        "human_confirmed",
        "mastery",
        "next_review_at",
        "review_count",
        "deleted_at",
    }
)
_REVIEW_INT_FIELDS = frozenset(
    {"priority", "difficulty", "mastery", "review_count"}
)
_REVIEW_BOOL_FIELDS = frozenset(
    {"is_favorite", "needs_redo", "allow_print", "human_confirmed"}
)
_REVIEW_REQUIRED_TEXT_FIELDS = frozenset(
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
_REVIEW_OPTIONAL_TEXT_FIELDS = frozenset(
    {
        "subject_id",
        "chapter_id",
        "problem_type",
        "title",
        "source_book",
        "source_year",
        "page_number",
        "original_number",
    }
)


def _review_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc)
        )
    except (TypeError, ValueError) as exc:
        raise DomainError(f"review datetime is invalid: {value!r}") from exc


def _review_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "1"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "0"}:
        return False
    raise DomainError(f"review boolean is invalid: {value!r}")


def _coerce_review_value(key: str, value: Any) -> Any:
    if key not in _REVIEW_MUTABLE_FIELDS:
        raise DomainError(f"review proposal cannot change field: {key}")
    if key == "status":
        return validate_status(str(value))
    if key in {"next_review_at", "deleted_at"}:
        return _review_datetime(value)
    if key in _REVIEW_INT_FIELDS:
        if value is None and key in {"difficulty", "mastery"}:
            return None
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise DomainError(f"review integer is invalid: {value!r}") from exc
        if key == "priority":
            return validate_priority(number)
        return number
    if key in _REVIEW_BOOL_FIELDS:
        return _review_bool(value)
    if key in _REVIEW_REQUIRED_TEXT_FIELDS:
        if not isinstance(value, str):
            raise DomainError(f"review text is invalid: {key}={value!r}")
        return value
    if key in _REVIEW_OPTIONAL_TEXT_FIELDS:
        if value is not None and not isinstance(value, str):
            raise DomainError(f"review text is invalid: {key}={value!r}")
        return value
    return value


class AIService:
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

    def get_prompt(self, key: str = "structure_recognize") -> Prompt:
        with self.session() as s:
            prompt = s.scalar(select(Prompt).where(Prompt.key == key))
            if not prompt:
                raise DomainError(f"提示词不存在：{key}")
            s.expunge(prompt)
            return prompt

    def list_jobs(self, limit: int = 50) -> list[AiJob]:
        with self.session() as s:
            rows = s.scalars(
                select(AiJob).order_by(AiJob.created_at.desc()).limit(limit)
            ).all()
            s.expunge_all()
            return list(rows)

    def get_job(self, job_id: str) -> AiJob | None:
        with self.session() as s:
            job = s.scalars(
                select(AiJob)
                .where(AiJob.id == job_id)
                .options(selectinload(AiJob.items))
            ).first()
            if job:
                s.expunge_all()
            return job

    def list_open_review_items(self) -> list[ReviewItem]:
        with self.session() as s:
            rows = s.scalars(
                select(ReviewItem)
                .where(ReviewItem.status.in_(("pending", "conflict")))
                .order_by(ReviewItem.id.desc())
            ).all()
            s.expunge_all()
            return list(rows)

    def list_review_items_for_job(self, job_id: str) -> list[ReviewItem]:
        """Return review candidates belonging to one AI job.

        The legacy review dialog shows every source in one global queue.  The
        intake workflow needs a job-scoped view so users stay inside the same
        recording session from upload through confirmation.
        """

        with self.session() as s:
            rows = s.scalars(
                select(ReviewItem)
                .join(ReviewSession, ReviewSession.id == ReviewItem.session_id)
                .where(ReviewSession.job_id == job_id)
                .order_by(text("review_items.rowid"))
            ).all()
            s.expunge_all()
            return list(rows)

    def get_review_item(self, item_id: str) -> ReviewItem | None:
        with self.session() as s:
            item = s.get(ReviewItem, item_id)
            if item:
                s.expunge(item)
            return item

    def today_cost(self) -> float:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.session() as s:
            total = s.scalar(
                select(func.coalesce(func.sum(AiJobItem.cost_estimate), 0.0)).where(
                    AiJobItem.created_at >= start
                )
            )
            return float(total or 0.0)

    def create_structure_job(
        self,
        problem_ids: list[str],
        *,
        user_instruction: str = "",
        allowed_fields: set[str] | frozenset[str] | None = None,
    ) -> AiJob:
        if not self.runtime.settings.ai.enabled:
            raise DomainError("AI 功能未启用（config [ai].enabled）")
        if not problem_ids:
            raise DomainError("未选择题目")
        max_n = self.runtime.settings.ai.max_images_per_job
        if len(problem_ids) > max_n:
            raise DomainError(f"单次最多 {max_n} 张/题")
        if self.today_cost() >= self.runtime.settings.ai.max_daily_cost_yuan:
            raise DomainError("已达每日 AI 费用上限")
        base_prompt = self.get_prompt("structure_recognize")
        provider_name = self.runtime.settings.ai.default_provider
        allowed = sorted(allowed_fields or DEFAULT_ALLOWED_FIELDS)
        with self.session() as s:
            job_id = new_id("job")
            prompt_key = "structure_recognize"
            instruction = user_instruction.strip()
            if instruction:
                prompt_key = f"intake_{job_id}"
                body = (
                    f"{base_prompt.body.rstrip()}\n\n"
                    "## 本次录题补充要求\n"
                    f"{instruction}\n\n"
                    "补充要求用于定位和理解图片内容；如其中明确要求 problems 数组，"
                    "按该根结构输出。不得改变字段权限或原图保护规则。"
                )
                s.add(
                    Prompt(
                        id=new_id("prompt"),
                        key=prompt_key,
                        name="AI 录题临时提示词",
                        body=body,
                        version=1,
                        is_builtin=False,
                    )
                )
            job = AiJob(
                id=job_id,
                job_type="structure_recognize",
                status="pending",
                provider=provider_name,
                model=self.runtime.settings.ai.default_vision_model or "mock-v1",
                prompt_key=prompt_key,
                total_items=0,
                allowed_fields_json=json.dumps(allowed, ensure_ascii=False),
            )
            s.add(job)
            s.flush()
            count = 0
            for pid in problem_ids:
                problem = s.scalars(
                    select(Problem)
                    .where(Problem.id == pid)
                    .options(selectinload(Problem.assets))
                ).first()
                if not problem:
                    continue
                original = next((a for a in problem.assets if a.role == "original"), None)
                if not original:
                    continue
                s.add(
                    AiJobItem(
                        id=new_id("jitem"),
                        job_id=job.id,
                        problem_id=problem.id,
                        asset_id=original.id,
                        status="pending",
                    )
                )
                count += 1
            if count == 0:
                raise DomainError("所选题目没有可识别的原图")
            job.total_items = count
            self._audit(
                s,
                "ai_job_created",
                "ai_job",
                job.id,
                {
                    "problem_ids": problem_ids,
                    "provider": provider_name,
                    "has_user_instruction": bool(instruction),
                },
            )
            s.commit()
            s.refresh(job)
            s.expunge(job)
            return job

    def get_job_diagnostics(self, job_id: str) -> dict[str, Any]:
        """Return privacy-safe live state and measured timing aggregates."""

        with self.session() as s:
            job = s.scalars(
                select(AiJob)
                .where(AiJob.id == job_id)
                .options(selectinload(AiJob.items))
            ).first()
            if job is None:
                raise DomainError("任务不存在")
            states = [item.status for item in job.items]
            if job.status == "cancelled":
                stage = "cancelled"
                label = "任务已取消"
            elif "running" in states:
                stage = "request"
                label = "AI 请求中（含图片上传、模型推理与响应等待）"
            elif "pending" in states:
                stage = "queued"
                label = "等待处理"
            elif "failed" in states:
                stage = "failed"
                label = "部分图片失败，可重新尝试"
            else:
                stage = "completed"
                label = "识别与候选写入已完成"

            item_ids = [item.id for item in job.items]
            logs = (
                s.scalars(
                    select(AuditLog).where(
                        AuditLog.action.in_(
                            {"ai_item_done", "ai_item_failed"}
                        ),
                        AuditLog.entity_type == "ai_job_item",
                        AuditLog.entity_id.in_(item_ids),
                    )
                ).all()
                if item_ids
                else []
            )
            totals: dict[str, float] = {}
            samples = 0
            retry_count = 0
            for log in logs:
                try:
                    detail = json.loads(log.detail_json)
                except json.JSONDecodeError:
                    continue
                provider_diagnostics = detail.get("provider_diagnostics")
                if isinstance(provider_diagnostics, dict):
                    attempts = provider_diagnostics.get("request_attempts")
                    if isinstance(attempts, int):
                        retry_count += max(0, attempts - 1)
                timings = detail.get("timings_ms")
                if log.action != "ai_item_done" or not isinstance(timings, dict):
                    continue
                samples += 1
                for key, value in timings.items():
                    if isinstance(value, (int, float)):
                        totals[str(key)] = totals.get(str(key), 0.0) + float(value)
            averages = {
                key: round(value / samples, 1)
                for key, value in totals.items()
            } if samples else {}
            return {
                "stage": stage,
                "stage_label": label,
                "timings_ms": averages,
                "timing_samples": samples,
                "retry_count": retry_count,
            }

    def create_intake_structure_job(
        self,
        intake_session_id: str,
        intake_asset_ids: list[str],
        *,
        user_instruction: str = "",
        allowed_fields: set[str] | frozenset[str] | None = None,
    ) -> AiJob:
        """Create a new-problem AI job without creating staging Problems."""

        if not self.runtime.settings.ai.enabled:
            raise DomainError("AI 功能未启用（config [ai].enabled）")
        if not intake_asset_ids:
            raise DomainError("未选择图片")
        max_n = self.runtime.settings.ai.max_images_per_job
        if len(intake_asset_ids) > max_n:
            raise DomainError(f"单次最多 {max_n} 张图片")
        if self.today_cost() >= self.runtime.settings.ai.max_daily_cost_yuan:
            raise DomainError("已达每日 AI 费用上限")
        base_prompt = self.get_prompt("structure_recognize")
        provider_name = self.runtime.settings.ai.default_provider
        allowed = sorted(allowed_fields or DEFAULT_ALLOWED_FIELDS)
        with self.session() as s:
            intake_session = s.get(IntakeSession, intake_session_id)
            if intake_session is None or intake_session.mode != "ai":
                raise DomainError("AI 录题会话不存在")
            job_id = new_id("job")
            prompt_key = f"intake_{job_id}"
            instruction = user_instruction.strip()
            body = base_prompt.body.rstrip()
            if instruction:
                body += (
                    "\n\n## 本次录题补充要求\n"
                    f"{instruction}\n\n"
                    "补充要求用于定位和理解图片内容；如其中明确要求 problems 数组，"
                    "按该根结构输出。不得改变字段权限或原图保护规则。"
                )
            s.add(
                Prompt(
                    id=new_id("prompt"),
                    key=prompt_key,
                    name="AI 录题临时提示词",
                    body=body,
                    version=1,
                    is_builtin=False,
                )
            )
            job = AiJob(
                id=job_id,
                job_type="intake_structure",
                status="pending",
                provider=provider_name,
                model=self.runtime.settings.ai.default_vision_model or "mock-v1",
                prompt_key=prompt_key,
                total_items=0,
                allowed_fields_json=json.dumps(allowed, ensure_ascii=False),
            )
            s.add(job)
            s.flush()
            assets = s.scalars(
                select(IntakeAsset).where(
                    IntakeAsset.session_id == intake_session_id,
                    IntakeAsset.id.in_(intake_asset_ids),
                )
            ).all()
            for asset in assets:
                s.add(
                    AiJobItem(
                        id=new_id("jitem"),
                        job_id=job.id,
                        intake_asset_id=asset.id,
                        status="pending",
                    )
                )
            if not assets:
                raise DomainError("录题会话中没有可识别的图片")
            job.total_items = len(assets)
            intake_session.job_id = job.id
            intake_session.status = "processing"
            intake_session.user_instruction = instruction
            self._audit(
                s,
                "intake_ai_job_created",
                "intake_session",
                intake_session.id,
                {"job_id": job.id, "asset_count": len(assets)},
            )
            s.commit()
            s.refresh(job)
            s.expunge(job)
            return job

    def run_job(self, job_id: str, *, should_cancel: Callable[[], bool] | None = None) -> AiJob:
        """同步执行任务（可由后台线程调用）。不直接写入正式题库字段。"""
        provider = get_provider(self.runtime.settings)

        with self.session() as s:
            job = s.scalars(
                select(AiJob).where(AiJob.id == job_id).options(selectinload(AiJob.items))
            ).first()
            if not job:
                raise DomainError("任务不存在")
            if job.status == "cancelled":
                return job
            prompt_key = job.prompt_key or "structure_recognize"
            job.status = "running"
            job.updated_at = utcnow()
            s.commit()

        prompt = self.get_prompt(prompt_key)

        # 重新加载条目 ID 列表，逐条处理并短事务提交，便于 UI 刷新进度
        with self.session() as s:
            item_ids = list(
                s.scalars(
                    select(AiJobItem.id).where(
                        AiJobItem.job_id == job_id,
                        AiJobItem.status.in_(("pending", "running", "failed")),
                    )
                ).all()
            )

        session_id: str | None = None
        for item_id in item_ids:
            if should_cancel and should_cancel():
                with self.session() as s:
                    job = s.get(AiJob, job_id)
                    if job:
                        job.status = "cancelled"
                        job.updated_at = utcnow()
                        job.finished_at = utcnow()
                        s.commit()
                break
            self._process_item(job_id, item_id, prompt.body, provider, session_holder := [])
            if session_holder and session_id is None:
                session_id = session_holder[0]

        with self.session() as s:
            job = s.scalars(
                select(AiJob).where(AiJob.id == job_id).options(selectinload(AiJob.items))
            ).first()
            assert job
            if job.status != "cancelled":
                job.status = "completed"
                job.finished_at = utcnow()
                job.updated_at = utcnow()
                job.estimated_cost = sum(i.cost_estimate for i in job.items)
                job.done_items = sum(1 for i in job.items if i.status == "done")
                job.failed_items = sum(1 for i in job.items if i.status == "failed")
                self._audit(
                    s,
                    "ai_job_finished",
                    "ai_job",
                    job.id,
                    {
                        "done": job.done_items,
                        "failed": job.failed_items,
                        "cost": job.estimated_cost,
                    },
                )
            else:
                intake_session = s.scalar(
                    select(IntakeSession).where(
                        IntakeSession.job_id == job.id
                    )
                )
                if intake_session:
                    intake_session.status = "cancelled"
                    intake_session.completed_at = utcnow()
            s.commit()
            s.expunge_all()
            return job

    def _process_item(
        self,
        job_id: str,
        item_id: str,
        prompt_body: str,
        provider: AIProvider,
        session_holder: list[str],
    ) -> None:
        item_started = perf_counter()
        active_stage = "preflight"
        timings_ms: dict[str, float] = {}
        with self.session() as s:
            job = s.get(AiJob, job_id)
            item = s.get(AiJobItem, item_id)
            if not job or not item:
                return
            intake_session: IntakeSession | None = None
            problem: Problem | None = None
            if item.intake_asset_id:
                asset = s.get(IntakeAsset, item.intake_asset_id)
                if asset:
                    intake_session = s.get(IntakeSession, asset.session_id)
            else:
                asset = s.get(Asset, item.asset_id) if item.asset_id else None
                problem = (
                    s.scalars(
                        select(Problem)
                        .where(Problem.id == item.problem_id)
                        .options(selectinload(Problem.tags))
                    ).first()
                    if item.problem_id
                    else None
                )
            if not asset or (item.intake_asset_id and not intake_session) or (
                not item.intake_asset_id and not problem
            ):
                item.status = "failed"
                item.error_message = "题目或资源缺失"
                s.commit()
                return

            # 预处理：存在性 / 大小；不修改原图
            image_path = self.store.resolve(asset.relative_path)
            if not image_path.is_file():
                item.status = "failed"
                item.error_message = f"原图丢失：{asset.relative_path}"
                job.failed_items += 1
                s.commit()
                return
            size = image_path.stat().st_size
            if size <= 0:
                item.status = "failed"
                item.error_message = "图片大小为 0"
                job.failed_items += 1
                s.commit()
                return

            item.status = "running"
            s.commit()

            try:
                timings_ms["preflight"] = (perf_counter() - item_started) * 1000
                if not self.runtime.settings.privacy.send_original_images_to_ai:
                    raise DomainError("隐私设置禁止向 AI 发送原图")
                active_stage = "provider"
                provider_started = perf_counter()
                result = provider.structure_from_image(
                    image_path=str(image_path),
                    prompt=prompt_body,
                    model=job.model,
                    timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
                )
                timings_ms["provider_total"] = (
                    perf_counter() - provider_started
                ) * 1000
                for key, value in result.timings_ms.items():
                    if isinstance(value, (int, float)):
                        timings_ms[str(key)] = float(value)

                active_stage = "validation"
                validation_started = perf_counter()
                allowed = set(json.loads(job.allowed_fields_json) or list(DEFAULT_ALLOWED_FIELDS))
                proposals: list[
                    tuple[dict[str, Any], list[dict[str, Any]], dict[str, float]]
                ] = []
                for candidate in result.candidate_results():
                    filtered, validation_uncertain = validate_and_filter_proposal(
                        candidate.fields,
                        allowed_fields=allowed,
                        allow_delete=self.runtime.settings.ai.allow_delete,
                    )
                    uncertain = [
                        *validation_uncertain,
                        *candidate.uncertain_fields,
                    ]
                    proposals.append(
                        (filtered, uncertain, normalize_region(candidate.region))
                    )
                if not proposals:
                    raise DomainError("AI 没有返回可确认的候选题")
                timings_ms["validation"] = (
                    perf_counter() - validation_started
                ) * 1000

                if self.runtime.settings.ai.save_raw_responses:
                    item.raw_response = result.raw_text
                structured: dict[str, Any]
                if len(proposals) == 1:
                    structured = {
                        **proposals[0][0],
                        "region": proposals[0][2],
                    }
                else:
                    structured = {
                        "problems": [
                            {**proposal, "region": region}
                            for proposal, _uncertain, region in proposals
                        ]
                    }
                item.structured_json = json.dumps(structured, ensure_ascii=False)
                item.cost_estimate = float(result.cost_estimate)

                active_stage = "candidate_write"
                write_started = perf_counter()
                with s.begin_nested():
                    if intake_session and isinstance(asset, IntakeAsset):
                        current_order = s.scalar(
                            select(
                                func.coalesce(
                                    func.max(IntakeCandidateRecord.sort_order), -1
                                )
                            ).where(
                                IntakeCandidateRecord.session_id
                                == intake_session.id
                            )
                        )
                        for offset, (filtered, uncertain, region) in enumerate(
                            proposals, start=1
                        ):
                            s.add(
                                IntakeCandidateRecord(
                                    id=new_id("icand"),
                                    session_id=intake_session.id,
                                    intake_asset_id=asset.id,
                                    status="pending",
                                    fields_json=json.dumps(
                                        filtered, ensure_ascii=False
                                    ),
                                    uncertain_json=json.dumps(
                                        uncertain, ensure_ascii=False
                                    ),
                                    region_json=json.dumps(
                                        region, ensure_ascii=False
                                    ),
                                    sort_order=int(
                                        -1
                                        if current_order is None
                                        else current_order
                                    )
                                    + offset,
                                )
                            )
                        intake_session.status = "review"
                        if not session_holder:
                            session_holder.append(intake_session.id)
                    else:
                        assert problem is not None
                        assert isinstance(asset, Asset)
                        review_session = s.scalar(
                            select(ReviewSession).where(
                                ReviewSession.job_id == job_id,
                                ReviewSession.status == "open",
                            )
                        )
                        if not review_session:
                            review_session = ReviewSession(
                                id=new_id("rsess"),
                                source="ai",
                                job_id=job_id,
                                status="open",
                                summary=f"AI 结构化审核 · {job_id}",
                            )
                            s.add(review_session)
                            s.flush()
                        if not session_holder:
                            session_holder.append(review_session.id)

                        from yancuo_win.application.sync_service import sync_snapshot

                        candidate_problems = [problem]
                        for _index in range(1, len(proposals)):
                            clone = Problem(
                                id=new_id("problem"),
                                status="inbox",
                                revision=1,
                                human_confirmed=False,
                            )
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
                            s.add(clone)
                            s.flush()
                            clone_snapshot = sync_snapshot(clone, [])
                            s.add(
                                Version(
                                    id=new_id("ver"),
                                    problem_id=clone.id,
                                    revision=1,
                                    source="ai_staging",
                                    summary="一图多题候选暂存",
                                    snapshot_json=json.dumps(
                                        clone_snapshot, ensure_ascii=False
                                    ),
                                    created_by=self.runtime.identity.user_id,
                                )
                            )
                            candidate_problems.append(clone)

                        for candidate_problem, (
                            filtered,
                            uncertain,
                            region,
                        ) in zip(candidate_problems, proposals, strict=True):
                            before = sync_snapshot(candidate_problem)
                            s.add(
                                ReviewItem(
                                    id=new_id("ritem"),
                                    session_id=review_session.id,
                                    problem_id=candidate_problem.id,
                                    status="pending",
                                    base_revision=candidate_problem.revision,
                                    before_json=json.dumps(
                                        before, ensure_ascii=False
                                    ),
                                    proposed_json=json.dumps(
                                        filtered, ensure_ascii=False
                                    ),
                                    uncertain_json=json.dumps(
                                        uncertain, ensure_ascii=False
                                    ),
                                    region_json=json.dumps(
                                        region, ensure_ascii=False
                                    ),
                                )
                            )

                timings_ms["candidate_write"] = (
                    perf_counter() - write_started
                ) * 1000
                item.status = "done"
                item.error_message = ""
                job.done_items += 1
                job.updated_at = utcnow()
                timings_ms["total"] = (perf_counter() - item_started) * 1000
                self._audit(
                    s,
                    "ai_item_done",
                    "ai_job_item",
                    item.id,
                    {
                        "problem_id": problem.id if problem else None,
                        "intake_session_id": (
                            intake_session.id if intake_session else None
                        ),
                        "candidate_count": len(proposals),
                        "cost": item.cost_estimate,
                        "timings_ms": {
                            key: round(value, 1)
                            for key, value in timings_ms.items()
                        },
                        "provider_diagnostics": result.diagnostics,
                    },
                )
                s.commit()
            except Exception as exc:  # noqa: BLE001
                item.status = "failed"
                item.error_message = str(exc)
                job.failed_items += 1
                job.updated_at = utcnow()
                timings_ms["total"] = (perf_counter() - item_started) * 1000
                self._audit(
                    s,
                    "ai_item_failed",
                    "ai_job_item",
                    item.id,
                    {
                        "error": str(exc)[:500],
                        "failed_stage": active_stage,
                        "timings_ms": {
                            key: round(value, 1)
                            for key, value in timings_ms.items()
                        },
                        "provider_diagnostics": {
                            "request_attempts": int(
                                getattr(provider, "_last_request_attempts", 0)
                                or 0
                            )
                        },
                    },
                )
                s.commit()

    def accept_review_item(self, review_item_id: str, *, force: bool = False) -> None:
        from yancuo_win.application.sync_service import SyncService, sync_snapshot

        with self.session() as s:
            item = s.get(ReviewItem, review_item_id)
            if not item or item.status not in {"pending", "conflict"}:
                raise DomainError("审核项不可接受")
            problem = s.scalars(
                select(Problem)
                .where(Problem.id == item.problem_id)
                .options(selectinload(Problem.tags))
            ).first()
            if not problem:
                raise DomainError("题目不存在")
            if problem.revision != item.base_revision and not force:
                if item.status == "conflict":
                    raise DomainError(
                        "存在冲突：请确认后选择「强制采用外部」或「保留内部」"
                    )
                raise DomainError(
                    f"题目已变更（当前 r{problem.revision}，审核基于 r{item.base_revision}），请拒绝后重跑"
                )
            before_sync = sync_snapshot(problem)
            try:
                proposed = json.loads(item.proposed_json)
            except json.JSONDecodeError as exc:
                raise DomainError("审查提案 JSON 无效") from exc
            if not isinstance(proposed, dict):
                raise DomainError("审查提案必须是对象")
            tags_present = "tags" in proposed
            tags = proposed.pop("tags", None)
            if tags_present and not isinstance(tags, list):
                raise DomainError("审查提案 tags 必须是列表")
            for key, value in proposed.items():
                if key not in _REVIEW_MUTABLE_FIELDS:
                    # Keep identity/revision/audit columns owned by the
                    # service even when a malformed review item is present.
                    continue
                setattr(problem, key, _coerce_review_value(key, value))
            # Keep the soft-delete timestamp consistent when a sync conflict
            # proposes a status change without carrying both fields.
            if "status" in proposed or "deleted_at" in proposed:
                if problem.status == "trashed" and problem.deleted_at is None:
                    problem.deleted_at = utcnow()
                elif problem.status != "trashed":
                    problem.deleted_at = None
            if isinstance(tags, list):
                tag_objs = []
                seen_tags: set[str] = set()
                for name in tags[:20]:
                    name = str(name).strip()
                    if not name or name in seen_tags or len(name) > 128:
                        continue
                    seen_tags.add(name)
                    tag = s.scalar(select(Tag).where(Tag.name == name))
                    if not tag:
                        tag = Tag(id=new_id("tag"), name=name, is_system=False)
                        s.add(tag)
                        s.flush()
                    tag_objs.append(tag)
                # 列表语义是权威结果：空列表也应清空旧标签，不能只在
                # 有新增标签时才写入。
                problem.tags = tag_objs

            problem.updated_at = utcnow()
            problem.revision += 1
            after_sync = sync_snapshot(problem, [t.name for t in problem.tags])
            # 根据 session source 标注版本来源
            session = s.get(ReviewSession, item.session_id)
            source = "ai"
            summary = "接受 AI 结构化结果"
            if session and session.source == "workspace":
                source = "workspace"
                summary = "接受外部工作区修改" + ("（强制）" if force else "")
            elif session and session.source == "sync":
                source = "sync"
                summary = "接受同步冲突的远端值" + ("（强制）" if force else "")
            ver = Version(
                id=new_id("ver"),
                problem_id=problem.id,
                revision=problem.revision,
                source=source,
                summary=summary,
                snapshot_json=json.dumps(after_sync, ensure_ascii=False),
                created_by=self.runtime.identity.user_id,
            )
            s.add(ver)
            s.flush()
            item.status = "accepted"
            item.applied_version_id = ver.id
            item.decided_at = utcnow()
            self._audit(
                s,
                "review_accepted",
                "review_item",
                item.id,
                {"problem_id": problem.id, "version_id": ver.id, "force": force},
            )
            problem_id = problem.id
            s.commit()
        operation = "update"
        if before_sync.get("status") != "trashed" and after_sync.get("status") == "trashed":
            operation = "delete"
        elif before_sync.get("status") == "trashed" and after_sync.get("status") != "trashed":
            operation = "undelete"
        SyncService(self.runtime).record_problem_update(
            problem_id, before=before_sync, after=after_sync, operation=operation
        )

    def reject_review_item(self, review_item_id: str) -> None:
        with self.session() as s:
            item = s.get(ReviewItem, review_item_id)
            if not item or item.status not in {"pending", "conflict"}:
                raise DomainError("审核项不可拒绝")
            item.status = "rejected"
            item.decided_at = utcnow()
            self._audit(
                s,
                "review_rejected",
                "review_item",
                item.id,
                {"problem_id": item.problem_id},
            )
            s.commit()

    def undo_last_ai_accept(self, problem_id: str) -> None:
        """撤销最近一次已接受的 AI 变更，恢复到接受前快照。"""
        from yancuo_win.application.sync_service import SyncService, sync_snapshot

        with self.session() as s:
            item = s.scalars(
                select(ReviewItem)
                .where(
                    ReviewItem.problem_id == problem_id,
                    ReviewItem.status == "accepted",
                )
                .order_by(ReviewItem.decided_at.desc())
            ).first()
            if not item:
                raise DomainError("没有可撤销的 AI 接受记录")
            problem = s.scalars(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.tags))
            ).first()
            if not problem:
                raise DomainError("题目不存在")
            before_sync = sync_snapshot(problem)
            try:
                before = json.loads(item.before_json)
            except json.JSONDecodeError as exc:
                raise DomainError("撤销快照 JSON 无效") from exc
            if not isinstance(before, dict):
                raise DomainError("撤销快照必须是对象")
            restore_fields = {
                "status",
                "title",
                "question_markdown",
                "question_latex",
                "user_answer",
                "correct_answer",
                "solution_markdown",
                "error_analysis",
                "notes",
                "priority",
                "subject_id",
                "chapter_id",
                "problem_type",
                "source_book",
                "source_year",
                "page_number",
                "original_number",
                "difficulty",
                "mastery",
                "is_favorite",
                "needs_redo",
                "allow_print",
                "human_confirmed",
                "next_review_at",
                "review_count",
                "deleted_at",
            }
            for key in restore_fields:
                if key in before:
                    setattr(problem, key, _coerce_review_value(key, before[key]))
            if "status" in before or "deleted_at" in before:
                if problem.status == "trashed" and problem.deleted_at is None:
                    problem.deleted_at = utcnow()
                elif problem.status != "trashed":
                    problem.deleted_at = None
            if isinstance(before.get("tags"), list):
                restored_tags = []
                seen_tags: set[str] = set()
                for name in before["tags"][:20]:
                    name = str(name).strip()
                    if not name or name in seen_tags or len(name) > 128:
                        continue
                    seen_tags.add(name)
                    tag = s.scalar(select(Tag).where(Tag.name == name))
                    if not tag:
                        tag = Tag(id=new_id("tag"), name=name, is_system=False)
                        s.add(tag)
                        s.flush()
                    restored_tags.append(tag)
                problem.tags = restored_tags
            problem.updated_at = utcnow()
            problem.revision += 1
            after_sync = sync_snapshot(problem, [t.name for t in problem.tags])
            s.add(
                Version(
                    id=new_id("ver"),
                    problem_id=problem.id,
                    revision=problem.revision,
                    source="ai_undo",
                    summary="撤销 AI 接受",
                    snapshot_json=json.dumps(after_sync, ensure_ascii=False),
                    created_by=self.runtime.identity.user_id,
                )
            )
            item.status = "undone"
            self._audit(
                s,
                "review_undone",
                "review_item",
                item.id,
                {"problem_id": problem_id},
            )
            s.commit()
        operation = "update"
        if before_sync.get("status") != "trashed" and after_sync.get("status") == "trashed":
            operation = "delete"
        elif before_sync.get("status") == "trashed" and after_sync.get("status") != "trashed":
            operation = "undelete"
        SyncService(self.runtime).record_problem_update(
            problem_id, before=before_sync, after=after_sync, operation=operation
        )

    def review_diffs(self, review_item_id: str) -> list[dict[str, Any]]:
        item = self.get_review_item(review_item_id)
        if not item:
            return []
        before = json.loads(item.before_json)
        proposed = json.loads(item.proposed_json)
        return field_diffs(before, proposed)

    def assert_original_untouched(self, problem_id: str) -> None:
        with self.session() as s:
            problem = s.scalars(
                select(Problem)
                .where(Problem.id == problem_id)
                .options(selectinload(Problem.assets))
            ).first()
            if not problem:
                raise DomainError("题目不存在")
            for asset in problem.assets:
                if asset.role == "original":
                    if not asset.is_immutable:
                        raise DomainError("原图丢失不可变标记")
                    path = self.store.resolve(asset.relative_path)
                    if not path.is_file():
                        raise DomainError("原图文件丢失")
                    return
            raise DomainError("无原图")
