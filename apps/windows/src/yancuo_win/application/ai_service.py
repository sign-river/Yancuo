"""AI 任务与审核应用服务。"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from yancuo_win.ai.base import AIProvider
from yancuo_win.ai.factory import get_provider
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    AiJob,
    AiJobItem,
    Asset,
    AuditLog,
    Problem,
    Prompt,
    ReviewItem,
    ReviewSession,
    Tag,
    Version,
    utcnow,
)
from yancuo_win.domain.rules import DomainError
from yancuo_win.review.changeset import (
    DEFAULT_ALLOWED_FIELDS,
    field_diffs,
    snapshot_problem_fields,
    validate_and_filter_proposal,
)


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

    def create_structure_job(self, problem_ids: list[str]) -> AiJob:
        if not self.runtime.settings.ai.enabled:
            raise DomainError("AI 功能未启用（config [ai].enabled）")
        if not problem_ids:
            raise DomainError("未选择题目")
        max_n = self.runtime.settings.ai.max_images_per_job
        if len(problem_ids) > max_n:
            raise DomainError(f"单次最多 {max_n} 张/题")
        if self.today_cost() >= self.runtime.settings.ai.max_daily_cost_yuan:
            raise DomainError("已达每日 AI 费用上限")

        provider_name = self.runtime.settings.ai.default_provider
        allowed = sorted(DEFAULT_ALLOWED_FIELDS)
        with self.session() as s:
            job = AiJob(
                id=new_id("job"),
                job_type="structure_recognize",
                status="pending",
                provider=provider_name,
                model=self.runtime.settings.ai.default_vision_model or "mock-v1",
                prompt_key="structure_recognize",
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
                {"problem_ids": problem_ids, "provider": provider_name},
            )
            s.commit()
            s.refresh(job)
            s.expunge(job)
            return job

    def run_job(self, job_id: str, *, should_cancel: Callable[[], bool] | None = None) -> AiJob:
        """同步执行任务（可由后台线程调用）。不直接写入正式题库字段。"""
        prompt = self.get_prompt("structure_recognize")
        provider = get_provider(self.runtime.settings)

        with self.session() as s:
            job = s.scalars(
                select(AiJob).where(AiJob.id == job_id).options(selectinload(AiJob.items))
            ).first()
            if not job:
                raise DomainError("任务不存在")
            if job.status == "cancelled":
                return job
            job.status = "running"
            job.updated_at = utcnow()
            s.commit()

        # 重新加载条目 ID 列表，逐条处理并短事务提交，便于 UI 刷新进度
        with self.session() as s:
            item_ids = list(
                s.scalars(select(AiJobItem.id).where(AiJobItem.job_id == job_id)).all()
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
        with self.session() as s:
            job = s.get(AiJob, job_id)
            item = s.get(AiJobItem, item_id)
            if not job or not item or not item.problem_id or not item.asset_id:
                return
            asset = s.get(Asset, item.asset_id)
            problem = s.get(Problem, item.problem_id)
            if not asset or not problem:
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
                if not self.runtime.settings.privacy.send_original_images_to_ai:
                    raise DomainError("隐私设置禁止向 AI 发送原图")
                result = provider.structure_from_image(
                    image_path=str(image_path),
                    prompt=prompt_body,
                    model=job.model,
                    timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
                )
                allowed = set(json.loads(job.allowed_fields_json) or list(DEFAULT_ALLOWED_FIELDS))
                filtered, uncertain = validate_and_filter_proposal(
                    result.fields,
                    allowed_fields=allowed,
                    allow_delete=self.runtime.settings.ai.allow_delete,
                )
                if self.runtime.settings.ai.save_raw_responses:
                    item.raw_response = result.raw_text
                item.structured_json = json.dumps(filtered, ensure_ascii=False)
                item.cost_estimate = float(result.cost_estimate)
                item.status = "done"
                item.error_message = ""

                # 确保有审核会话
                review_session = s.scalar(
                    select(ReviewSession).where(
                        ReviewSession.job_id == job_id, ReviewSession.status == "open"
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

                before = snapshot_problem_fields(problem)
                s.add(
                    ReviewItem(
                        id=new_id("ritem"),
                        session_id=review_session.id,
                        problem_id=problem.id,
                        status="pending",
                        base_revision=problem.revision,
                        before_json=json.dumps(before, ensure_ascii=False),
                        proposed_json=json.dumps(filtered, ensure_ascii=False),
                        uncertain_json=json.dumps(uncertain, ensure_ascii=False),
                    )
                )
                job.done_items += 1
                job.updated_at = utcnow()
                self._audit(
                    s,
                    "ai_item_done",
                    "ai_job_item",
                    item.id,
                    {"problem_id": problem.id, "cost": item.cost_estimate},
                )
                s.commit()
            except Exception as exc:  # noqa: BLE001
                item.status = "failed"
                item.error_message = str(exc)
                job.failed_items += 1
                job.updated_at = utcnow()
                self._audit(
                    s,
                    "ai_item_failed",
                    "ai_job_item",
                    item.id,
                    {"error": str(exc)[:500]},
                )
                s.commit()

    def accept_review_item(self, review_item_id: str, *, force: bool = False) -> None:
        with self.session() as s:
            item = s.get(ReviewItem, review_item_id)
            if not item or item.status not in {"pending", "conflict"}:
                raise DomainError("审核项不可接受")
            problem = s.get(Problem, item.problem_id)
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
            proposed = json.loads(item.proposed_json)
            tags = proposed.pop("tags", None)
            for key, value in proposed.items():
                if hasattr(problem, key):
                    setattr(problem, key, value)
            if isinstance(tags, list):
                tag_objs = []
                for name in tags:
                    name = str(name).strip()
                    if not name:
                        continue
                    tag = s.scalar(select(Tag).where(Tag.name == name))
                    if not tag:
                        tag = Tag(id=new_id("tag"), name=name, is_system=False)
                        s.add(tag)
                        s.flush()
                    tag_objs.append(tag)
                if tag_objs:
                    existing = {t.id: t for t in problem.tags}
                    for t in tag_objs:
                        existing[t.id] = t
                    problem.tags = list(existing.values())

            problem.updated_at = utcnow()
            problem.revision += 1
            snap = snapshot_problem_fields(problem)
            # 根据 session source 标注版本来源
            session = s.get(ReviewSession, item.session_id)
            source = "ai"
            summary = "接受 AI 结构化结果"
            if session and session.source == "workspace":
                source = "workspace"
                summary = "接受外部工作区修改" + ("（强制）" if force else "")
            ver = Version(
                id=new_id("ver"),
                problem_id=problem.id,
                revision=problem.revision,
                source=source,
                summary=summary,
                snapshot_json=json.dumps(snap, ensure_ascii=False),
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
            s.commit()

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
            problem = s.get(Problem, problem_id)
            if not problem:
                raise DomainError("题目不存在")
            before = json.loads(item.before_json)
            for key in (
                "title",
                "question_markdown",
                "question_latex",
                "user_answer",
                "correct_answer",
                "solution_markdown",
                "error_analysis",
                "notes",
            ):
                if key in before:
                    setattr(problem, key, before[key])
            problem.updated_at = utcnow()
            problem.revision += 1
            s.add(
                Version(
                    id=new_id("ver"),
                    problem_id=problem.id,
                    revision=problem.revision,
                    source="ai_undo",
                    summary="撤销 AI 接受",
                    snapshot_json=json.dumps(snapshot_problem_fields(problem), ensure_ascii=False),
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
