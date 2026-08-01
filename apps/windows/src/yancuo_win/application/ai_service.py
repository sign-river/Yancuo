"""AI 任务与审核应用服务。"""

from __future__ import annotations

import json
import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload

from yancuo_win.ai.base import (
    AIProvider,
    StructuredCandidate,
    StructuredResult,
    normalize_region,
)
from yancuo_win.ai.factory import get_provider
from yancuo_win.application.ai_result_cache import (
    find_recognition_cache,
    load_cached_structure,
    recognition_cache_key,
)
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    AiJob,
    AiJobItem,
    AiRecognitionCache,
    Asset,
    AuditLog,
    IntakeAsset,
    IntakeCandidateRecord,
    IntakeCandidateUnit,
    IntakeRecognitionUnit,
    IntakeRecognitionUnitAsset,
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

_QUICK_INTAKE_PROMPT = """这是题目图片的首轮快速识别。只返回严格 JSON，且只包含一个 problems 数组。
每个对象仅包含 title、question_markdown、question_latex、correct_answer、region。
优先准确转写题干、公式和最终答案；不要生成解析、标签、分类、说明文字或 Markdown 代码块。
无法确认的字段使用空字符串，region 无法判断时使用整图。"""


def _structured_result_from_cache(
    payload: dict[str, object] | None, raw_response: str
) -> StructuredResult | None:
    """Rebuild single- and multi-candidate results from durable cache data."""

    if not payload:
        return None
    if (
        payload.get("format") == "yancuo-recognition-cache"
        and payload.get("format_version") == 2
    ):
        cached_candidates = payload.get("candidates")
        if not isinstance(cached_candidates, list) or not cached_candidates:
            return None
        candidates: list[StructuredCandidate] = []
        for value in cached_candidates:
            if not isinstance(value, dict):
                return None
            fields = value.get("fields")
            uncertain = value.get("uncertain_fields", [])
            region = value.get("region", {})
            if (
                not isinstance(fields, dict)
                or not fields
                or not isinstance(uncertain, list)
                or not isinstance(region, dict)
            ):
                return None
            candidates.append(
                StructuredCandidate(
                    fields=dict(fields),
                    uncertain_fields=[
                        dict(item) for item in uncertain if isinstance(item, dict)
                    ],
                    region=region,
                )
            )
        diagnostics = payload.get("diagnostics", {})
        return StructuredResult(
            fields=dict(candidates[0].fields),
            uncertain_fields=list(candidates[0].uncertain_fields),
            candidates=candidates,
            raw_text=raw_response,
            diagnostics=(
                dict(diagnostics) if isinstance(diagnostics, dict) else {}
            ),
        )

    cached_problems = payload.get("problems")
    if cached_problems is not None:
        if not isinstance(cached_problems, list) or not cached_problems:
            return None
        candidates: list[StructuredCandidate] = []
        for value in cached_problems:
            if not isinstance(value, dict):
                return None
            fields = dict(value)
            region = fields.pop("region", {})
            if not fields or not isinstance(region, dict):
                return None
            candidates.append(StructuredCandidate(fields=fields, region=region))
        return StructuredResult(
            fields=dict(candidates[0].fields),
            candidates=candidates,
            raw_text=raw_response,
        )

    fields = dict(payload)
    region = fields.pop("region", {})
    if not fields or not isinstance(region, dict):
        return None
    return StructuredResult(
        fields=fields,
        candidates=[StructuredCandidate(fields=fields, region=region)],
        raw_text=raw_response,
    )


def _recognition_cache_payload(
    proposals: list[
        tuple[dict[str, Any], list[dict[str, Any]], dict[str, float]]
    ],
    result: StructuredResult,
) -> dict[str, object]:
    """Persist every normalized value needed to replay candidate creation."""

    diagnostics: dict[str, object] = {}
    suggestion = _structure_suggestion(
        result.diagnostics.get("structure_suggestion")
    )
    if suggestion is not None:
        diagnostics["structure_suggestion"] = suggestion
    return {
        "format": "yancuo-recognition-cache",
        "format_version": 2,
        "candidates": [
            {
                "fields": fields,
                "uncertain_fields": uncertain,
                "region": region,
            }
            for fields, uncertain, region in proposals
        ],
        "diagnostics": diagnostics,
    }


def _structure_suggestion(value: object) -> dict[str, object] | None:
    """Accept only bounded advisory layout metadata from an AI result."""

    if not isinstance(value, dict):
        return None
    kind = value.get("layout_kind")
    count = value.get("subquestion_count")
    confidence = value.get("confidence")
    rationale = value.get("rationale")
    signals = value.get("signals")
    if (
        kind not in {"single", "independent", "composite", "continuation"}
        or not isinstance(count, int)
        or not isinstance(confidence, (int, float))
        or not isinstance(rationale, str)
        or not isinstance(signals, list)
    ):
        return None
    return {
        "layout_kind": kind,
        "subquestion_count": max(1, min(count, 99)),
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "rationale": rationale[:240],
        "signals": [str(signal)[:80] for signal in signals[:8]],
    }


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

    def recognition_cache_summary(self) -> dict[str, int]:
        """Return lightweight cache statistics for the user-facing controls."""

        with self.session() as s:
            count, bytes_used = s.execute(
                select(
                    func.count(AiRecognitionCache.cache_key),
                    func.coalesce(
                        func.sum(
                            func.length(AiRecognitionCache.structured_json)
                            + func.length(AiRecognitionCache.raw_response)
                        ),
                        0,
                    ),
                )
            ).one()
            return {"count": int(count or 0), "bytes": int(bytes_used or 0)}

    def clear_recognition_cache(self) -> int:
        """Delete cached recognition results without touching source jobs or images."""

        with self.session() as s:
            deleted = s.execute(delete(AiRecognitionCache)).rowcount or 0
            s.commit()
            return int(deleted)

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

    def list_open_review_items_for_job(self, job_id: str) -> list[ReviewItem]:
        """Return only unfinished AI proposals for a selected completion batch."""

        with self.session() as s:
            rows = s.scalars(
                select(ReviewItem)
                .join(ReviewSession, ReviewSession.id == ReviewItem.session_id)
                .where(
                    ReviewSession.job_id == job_id,
                    ReviewSession.source == "ai",
                    ReviewItem.status.in_(("pending", "conflict")),
                )
                .order_by(text("review_items.rowid"))
            ).all()
            s.expunge_all()
            return list(rows)

    def completion_review_overview(self, limit: int = 30) -> list[dict[str, Any]]:
        """List resumable completion work using labels safe for the regular UI."""

        labels = {
            "pending": "等待开始",
            "running": "正在生成建议",
            "completed": "建议已生成",
            "failed": "部分内容未完成",
            "cancelled": "已取消",
        }
        with self.session() as s:
            jobs = s.scalars(
                select(AiJob)
                .where(AiJob.job_type == "structure_recognize")
                .order_by(AiJob.created_at.desc())
                .limit(limit)
            ).all()
            result: list[dict[str, Any]] = []
            for job in jobs:
                open_count = s.scalar(
                    select(func.count(ReviewItem.id))
                    .join(ReviewSession, ReviewSession.id == ReviewItem.session_id)
                    .where(
                        ReviewSession.job_id == job.id,
                        ReviewSession.source == "ai",
                        ReviewItem.status.in_(("pending", "conflict")),
                    )
                )
                if job.status == "cancelled" and not open_count:
                    continue
                result.append(
                    {
                        "job_id": job.id,
                        "label": labels.get(job.status, "等待处理"),
                        "status": job.status,
                        "completed": job.done_items,
                        "total": job.total_items,
                        "failed": job.failed_items,
                        "review_count": int(open_count or 0),
                    }
                )
            return result

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
                            {
                                "ai_item_done",
                                "ai_item_failed",
                                "ai_item_cache_hit",
                                "ai_job_ui_delivered",
                            }
                        ),
                        (
                            (
                                (AuditLog.entity_type == "ai_job_item")
                                & AuditLog.entity_id.in_(item_ids)
                            )
                            | (
                                (AuditLog.entity_type == "ai_job")
                                & (AuditLog.entity_id == job_id)
                            )
                        ),
                    )
                ).all()
                if item_ids
                else []
            )
            totals: dict[str, float] = {}
            client_totals: dict[str, float] = {}
            client_samples = 0
            samples = 0
            retry_count = 0
            cache_hits = 0
            token_usage: dict[str, int] = {}
            token_samples = 0
            server_timing: list[dict[str, str]] = []
            for log in logs:
                try:
                    detail = json.loads(log.detail_json)
                except json.JSONDecodeError:
                    continue
                if log.action == "ai_item_cache_hit":
                    cache_hits += 1
                    continue
                if log.action == "ai_job_ui_delivered":
                    timings = detail.get("timings_ms")
                    if isinstance(timings, dict):
                        client_samples += 1
                        for key, value in timings.items():
                            if isinstance(value, (int, float)):
                                client_totals[str(key)] = (
                                    client_totals.get(str(key), 0.0) + float(value)
                                )
                    continue
                provider_diagnostics = detail.get("provider_diagnostics")
                if isinstance(provider_diagnostics, dict):
                    attempts = provider_diagnostics.get("request_attempts")
                    if isinstance(attempts, int):
                        retry_count += max(0, attempts - 1)
                    usage = provider_diagnostics.get("token_usage")
                    if isinstance(usage, dict):
                        token_samples += 1
                        for key, value in usage.items():
                            if isinstance(value, int) and not isinstance(value, bool):
                                token_usage[str(key)] = token_usage.get(str(key), 0) + value
                    timing = provider_diagnostics.get("server_timing")
                    if isinstance(timing, dict):
                        server_timing.append(
                            {
                                str(key): str(value)
                                for key, value in timing.items()
                            }
                        )
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
            if client_samples:
                averages.update(
                    {
                        key: round(value / client_samples, 1)
                        for key, value in client_totals.items()
                    }
                )
            return {
                "stage": stage,
                "stage_label": label,
                "timings_ms": averages,
                "timing_samples": samples,
                "retry_count": retry_count,
                "cache_hits": cache_hits,
                "provider_token_usage": token_usage,
                "provider_token_samples": token_samples,
                "provider_server_timing": server_timing,
            }

    def record_ui_delivery_timings(
        self,
        job_id: str,
        *,
        ui_wait_ms: float,
        classification_match_ms: float,
    ) -> None:
        """Persist client-side delivery stages after the worker signal is received."""

        values = {
            "ui_wait": max(0.0, float(ui_wait_ms)),
            "classification_match": max(0.0, float(classification_match_ms)),
        }
        with self.session() as s:
            if s.get(AiJob, job_id) is None:
                raise DomainError("任务不存在")
            self._audit(
                s,
                "ai_job_ui_delivered",
                "ai_job",
                job_id,
                {
                    "timings_ms": {
                        key: round(value, 1)
                        for key, value in values.items()
                    }
                },
            )
            s.commit()

    def create_intake_structure_job(
        self,
        intake_session_id: str,
        intake_asset_ids: list[str],
        *,
        user_instruction: str = "",
        recognition_mode: str = "auto",
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
            assets_by_id = {asset.id: asset for asset in assets}
            ordered_assets = [
                assets_by_id[asset_id]
                for asset_id in intake_asset_ids
                if asset_id in assets_by_id
            ]
            if recognition_mode == "many_to_one":
                groups = [ordered_assets]
            else:
                groups = [[asset] for asset in ordered_assets]
            for unit_order, group in enumerate(groups):
                unit = IntakeRecognitionUnit(
                    id=new_id("iunit"),
                    session_id=intake_session.id,
                    mode=recognition_mode,
                    sort_order=unit_order,
                )
                s.add(unit)
                for asset_order, asset in enumerate(group):
                    s.add(
                        IntakeRecognitionUnitAsset(
                            recognition_unit_id=unit.id,
                            intake_asset_id=asset.id,
                            sort_order=asset_order,
                        )
                    )
                s.add(
                    AiJobItem(
                        id=new_id("jitem"),
                        job_id=job.id,
                        intake_asset_id=group[0].id,
                        recognition_unit_id=unit.id,
                        status="pending",
                    )
                )
            if not assets:
                raise DomainError("录题会话中没有可识别的图片")
            job.total_items = len(groups)
            intake_session.job_id = job.id
            intake_session.status = "processing"
            intake_session.user_instruction = instruction
            self._audit(
                s,
                "intake_ai_job_created",
                "intake_session",
                intake_session.id,
                {"job_id": job.id, "asset_count": len(assets), "unit_count": len(groups), "mode": recognition_mode},
            )
            s.commit()
            s.refresh(job)
            s.expunge(job)
            return job

    def run_job(
        self,
        job_id: str,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> AiJob:
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
            self._process_item(
                job_id,
                item_id,
                prompt.body,
                prompt.version,
                provider,
                session_holder := [],
                should_cancel=should_cancel,
                on_progress=on_progress,
            )
            if should_cancel and should_cancel():
                with self.session() as s:
                    job = s.get(AiJob, job_id)
                    if job:
                        job.status = "cancelled"
                        job.updated_at = utcnow()
                        job.finished_at = utcnow()
                        s.commit()
                break
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
        prompt_version: int,
        provider: AIProvider,
        session_holder: list[str],
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        item_started = perf_counter()
        active_stage = "preflight"
        timings_ms: dict[str, float] = {}
        with self.session() as s:
            job = s.get(AiJob, job_id)
            item = s.get(AiJobItem, item_id)
            if not job or not item:
                return
            queued_at = item.updated_at or item.created_at
            if queued_at.tzinfo is None:
                queued_at = queued_at.replace(tzinfo=timezone.utc)
            timings_ms["queue_wait"] = max(
                0.0,
                (datetime.now(timezone.utc) - queued_at).total_seconds() * 1000,
            )
            intake_session: IntakeSession | None = None
            problem: Problem | None = None
            intake_assets: list[IntakeAsset] = []
            recognition_mode = ""
            use_recognition_cache = True
            if item.intake_asset_id:
                asset = s.get(IntakeAsset, item.intake_asset_id)
                if asset:
                    intake_session = s.get(IntakeSession, asset.session_id)
                    intake_assets = [asset]
                    if intake_session:
                        try:
                            draft = json.loads(intake_session.draft_json)
                        except json.JSONDecodeError:
                            draft = {}
                        if isinstance(draft, dict):
                            use_recognition_cache = bool(
                                draft.get("use_recognition_cache", True)
                            )
                if item.recognition_unit_id:
                    recognition_unit = s.get(
                        IntakeRecognitionUnit, item.recognition_unit_id
                    )
                    recognition_mode = recognition_unit.mode if recognition_unit else ""
                    members = s.execute(
                        select(IntakeAsset)
                        .join(
                            IntakeRecognitionUnitAsset,
                            IntakeRecognitionUnitAsset.intake_asset_id
                            == IntakeAsset.id,
                        )
                        .where(
                            IntakeRecognitionUnitAsset.recognition_unit_id
                            == item.recognition_unit_id
                        )
                        .order_by(IntakeRecognitionUnitAsset.sort_order)
                    ).scalars().all()
                    if members:
                        intake_assets = members
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
            if not intake_assets:
                intake_assets = [asset]

            if on_progress:
                on_progress({"stage": "preflight", "label": "正在检查原图并准备识别"})

            # 预处理：存在性 / 大小；不修改原图
            image_paths = [self.store.resolve(value.relative_path) for value in intake_assets]
            for source, image_path in zip(intake_assets, image_paths, strict=True):
                if not image_path.is_file():
                    item.status = "failed"
                    item.error_message = f"原图丢失：{source.relative_path}"
                    job.failed_items += 1
                    s.commit()
                    return
                if image_path.stat().st_size <= 0:
                    item.status = "failed"
                    item.error_message = "图片大小为 0"
                    job.failed_items += 1
                    s.commit()
                    return

            item.status = "running"
            s.commit()

            try:
                if should_cancel and should_cancel():
                    return
                timings_ms["preflight"] = (perf_counter() - item_started) * 1000
                allowed = set(json.loads(job.allowed_fields_json) or list(DEFAULT_ALLOWED_FIELDS))
                source_fingerprint = hashlib.sha256(
                    "\n".join(value.sha256 for value in intake_assets).encode("ascii")
                ).hexdigest()
                cache_started = perf_counter()
                cache_key = recognition_cache_key(
                    asset_sha256=source_fingerprint,
                    prompt_body=prompt_body,
                    prompt_version=prompt_version,
                    provider=job.provider,
                    model=job.model,
                    allowed_fields=sorted(allowed),
                )
                cached = find_recognition_cache(s, cache_key)
                cached_fields = (
                    load_cached_structure(cached)
                    if cached and use_recognition_cache
                    else None
                )
                timings_ms["cache_lookup"] = (
                    perf_counter() - cache_started
                ) * 1000
                if not self.runtime.settings.privacy.send_original_images_to_ai:
                    raise DomainError("隐私设置禁止向 AI 发送原图")
                cached_result = _structured_result_from_cache(
                    cached_fields,
                    cached.raw_response if cached and use_recognition_cache else "",
                )
                if cached_result is not None:
                    active_stage = "cache"
                    result = cached_result
                    if on_progress:
                        on_progress({"stage": "cache", "label": "已命中历史识别缓存，正在整理结果"})
                    self._audit(
                        s,
                        "ai_item_cache_hit",
                        "ai_job_item",
                        item.id,
                        {"source_job_item_id": cached.source_job_item_id},
                    )
                else:
                    active_stage = "provider"
                    provider_started = perf_counter()
                    if job.job_type == "intake_structure":
                        if on_progress:
                            on_progress({"stage": "quick_request", "label": "正在识别题干、公式和最终答案"})
                        quick_started = perf_counter()
                        quick_result = (
                            provider.structure_from_image(
                                image_path=str(image_paths[0]),
                                prompt=_QUICK_INTAKE_PROMPT,
                                model=job.model,
                                timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
                            )
                            if len(image_paths) == 1
                            else provider.structure_from_images(
                                image_paths=[str(path) for path in image_paths],
                                prompt=_QUICK_INTAKE_PROMPT,
                                model=job.model,
                                timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
                            )
                        )
                        timings_ms["quick_request"] = (
                            perf_counter() - quick_started
                        ) * 1000
                        quick_candidates = quick_result.candidate_results()
                        if on_progress and quick_candidates:
                            quick_fields = quick_candidates[0].fields
                            preview = {
                                key: str(quick_fields.get(key) or "").strip()
                                for key in ("title", "question_markdown", "question_latex", "correct_answer")
                            }
                            if preview["question_markdown"] or preview["question_latex"] or preview["correct_answer"]:
                                on_progress({
                                    "stage": "quick_ready",
                                    "label": "首轮结果已到，正在补全解析、标签和分类",
                                    "preview": preview,
                                })
                        if on_progress:
                            on_progress({"stage": "enrichment", "label": "正在生成完整解析、标签和分类"})
                    enrichment_started = perf_counter()
                    if len(image_paths) == 1:
                        result = provider.structure_from_image(
                            image_path=str(image_paths[0]),
                            prompt=prompt_body,
                            model=job.model,
                            timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
                        )
                    else:
                        result = provider.structure_from_images(
                            image_paths=[str(path) for path in image_paths],
                            prompt=prompt_body,
                            model=job.model,
                            timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
                        )
                    timings_ms["enrichment_request"] = (
                        perf_counter() - enrichment_started
                    ) * 1000
                    timings_ms["provider_total"] = (
                        perf_counter() - provider_started
                    ) * 1000
                if should_cancel and should_cancel():
                    return
                for key, value in result.timings_ms.items():
                    if isinstance(value, (int, float)):
                        timings_ms[str(key)] = float(value)

                active_stage = "validation"
                validation_started = perf_counter()
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
                if recognition_mode in {"one_to_one", "many_to_one"} and len(proposals) != 1:
                    raise DomainError("当前识别方式要求 AI 只返回一个候选题")
                if intake_session and recognition_mode == "auto" and item.recognition_unit_id:
                    suggestion = _structure_suggestion(
                        result.diagnostics.get("structure_suggestion")
                    )
                    if suggestion is not None:
                        try:
                            draft = json.loads(intake_session.draft_json)
                        except json.JSONDecodeError:
                            draft = {}
                        if not isinstance(draft, dict):
                            draft = {}
                        suggestions = draft.setdefault("structure_suggestions", {})
                        if isinstance(suggestions, dict):
                            suggestions[item.recognition_unit_id] = suggestion
                            intake_session.draft_json = json.dumps(
                                draft, ensure_ascii=False
                            )
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
                if on_progress:
                    on_progress({"stage": "candidate_write", "label": "正在校验字段并写入待确认结果"})
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
                            candidate_id = new_id("icand")
                            s.add(
                                IntakeCandidateRecord(
                                    id=candidate_id,
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
                            if item.recognition_unit_id:
                                s.add(
                                    IntakeCandidateUnit(
                                        candidate_id=candidate_id,
                                        recognition_unit_id=item.recognition_unit_id,
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
                s.merge(
                    AiRecognitionCache(
                        cache_key=recognition_cache_key(
                            asset_sha256=source_fingerprint,
                            prompt_body=prompt_body,
                            prompt_version=prompt_version,
                            provider=job.provider,
                            model=job.model,
                            allowed_fields=sorted(allowed),
                        ),
                        structured_json=json.dumps(
                            _recognition_cache_payload(proposals, result),
                            ensure_ascii=False,
                        ),
                        raw_response=item.raw_response,
                        source_job_item_id=item.id,
                    )
                )
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
                if should_cancel and should_cancel():
                    s.rollback()
                    return
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

    def review_presentation(self, review_item_id: str) -> dict[str, Any]:
        """Return a user-facing review card without storage implementation details."""

        item = self.get_review_item(review_item_id)
        if item is None:
            raise DomainError("审核项不存在")
        problem = None
        with self.session() as s:
            problem = s.get(Problem, item.problem_id)
            session = s.get(ReviewSession, item.session_id)
            source = session.source if session else "external"
            if problem:
                s.expunge(problem)
        try:
            proposed = json.loads(item.proposed_json)
            uncertain = json.loads(item.uncertain_json)
        except json.JSONDecodeError as exc:
            raise DomainError("审核建议无法读取") from exc
        if not isinstance(proposed, dict) or not isinstance(uncertain, list):
            raise DomainError("审核建议格式不正确")

        source_labels = {
            "ai": "AI 补全建议",
            "workspace": "导入建议",
            "sync": "同步建议",
        }
        warnings = []
        for value in uncertain:
            if not isinstance(value, dict):
                continue
            field = str(value.get("field") or "部分内容")
            reason = str(value.get("reason") or "需要人工确认")
            warnings.append(f"{field}：{reason}")
        return {
            "title": str(proposed.get("title") or (problem.title if problem else "未命名题目")),
            "source": source_labels.get(source, "待确认建议"),
            "status": "存在并发变更" if item.status == "conflict" else "等待确认",
            "diffs": self.review_diffs(review_item_id),
            "warnings": warnings,
        }

    def apply_review_decisions(self, decisions: dict[str, str]) -> dict[str, list[str]]:
        """Materialize explicit human decisions only when the final apply step runs."""

        accepted: list[str] = []
        rejected: list[str] = []
        for item_id, decision in decisions.items():
            if decision == "accept":
                self.accept_review_item(item_id)
                item = self.get_review_item(item_id)
                if item:
                    accepted.append(item.problem_id)
            elif decision == "reject":
                self.reject_review_item(item_id)
                rejected.append(item_id)
            else:
                raise DomainError("审核决定无效")
        return {"accepted_problem_ids": accepted, "rejected_item_ids": rejected}

    def undo_review_accepts(self, problem_ids: list[str]) -> int:
        """Undo accepted AI changes from one completed review run."""

        undone = 0
        for problem_id in dict.fromkeys(problem_ids):
            self.undo_last_ai_accept(problem_id)
            undone += 1
        return undone

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
