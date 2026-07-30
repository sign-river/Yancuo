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
from sqlalchemy import delete, func, select
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
    IntakeCandidateUnit,
    IntakeRecognitionUnit,
    IntakeRecognitionUnitAsset,
    IntakeSession,
    Problem,
    ProblemSet,
    ProblemSetAsset,
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
        "correct_answer",
        "solution_markdown",
        "notes",
        "tags",
        "subject_id",
        "chapter_id",
        "taxonomy_proposal",
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

_RECOGNITION_MODES = frozenset({"auto", "one_to_one", "one_to_many", "many_to_one"})

# These are deliberately only high-signal terms from the standard graduate
# entrance-exam curricula.  They supplement, rather than replace, the local
# catalog: an inferred value is always one of the user's existing chapters.
_CHAPTER_KEYWORD_GROUPS = (
    (("行列式",), ("行列式",)),
    (("矩阵", "特征值", "特征向量", "相似矩阵", "二次型"), ("矩阵", "二次型")),
    (("极限", "等价无穷小", "洛必达"), ("极限",)),
    (("连续", "间断点"), ("连续",)),
    (("导数", "微分", "单调性", "极值", "凹凸性"), ("微分", "导数")),
    (("不定积分", "定积分", "换元积分", "分部积分"), ("积分",)),
    (("微分方程",), ("微分方程",)),
    (("多元函数", "偏导数", "全微分"), ("多元函数",)),
    (("二重积分", "三重积分", "曲线积分", "曲面积分"), ("重积分", "曲线积分", "曲面积分")),
    (("概率", "随机变量", "分布函数", "期望", "方差"), ("概率", "随机变量", "数理统计")),
    (("数据结构", "链表", "栈", "队列", "二叉树", "邻接表"), ("数据结构",)),
    (("操作系统", "进程", "线程", "虚拟内存", "页面置换"), ("操作系统",)),
    (("计算机组成", "指令系统", "流水线", "Cache"), ("组成原理", "计算机组成")),
    (("计算机网络", "TCP", "IP", "HTTP", "路由"), ("计算机网络", "网络")),
    (("马克思主义基本原理", "唯物", "辩证法", "剩余价值"), ("马原", "马克思主义基本原理")),
    (("毛泽东思想", "中国特色社会主义理论", "邓小平理论"), ("毛概", "中国特色")),
    (("中国近现代史", "新民主主义革命", "抗日战争"), ("史纲", "近现代史")),
    (("思想道德", "法治", "爱国主义"), ("思修", "思想道德", "法治")),
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
    cache_hits: int = 0
    provider_token_usage: dict[str, int] = field(default_factory=dict)
    provider_token_samples: int = 0
    provider_server_timing: list[dict[str, str]] = field(default_factory=list)


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
    source_images: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class IntakeStructureSuggestion:
    unit_id: str
    layout_kind: str
    subquestion_count: int
    confidence: float
    rationale: str
    signals: list[str]


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

    def _apply_taxonomy_proposal(self, session, fields: dict[str, Any]) -> None:
        proposal = fields.pop("taxonomy_proposal", None)
        if not isinstance(proposal, dict) or fields.get("chapter_id"):
            return
        subject_name = str(proposal.get("subject_name") or "").strip()
        chapter_name = str(proposal.get("chapter_name") or "").strip()
        parent_id = proposal.get("parent_chapter_id")
        reason = str(proposal.get("reason") or "").strip()
        subject = (
            session.get(Subject, fields.get("subject_id"))
            if fields.get("subject_id")
            else None
        )
        if subject is None and not subject_name:
            raise DomainError("新分类提案缺少科目名称")
        if not reason:
            raise DomainError("新分类提案缺少创建理由")
        if parent_id is not None and not isinstance(parent_id, str):
            raise DomainError("新分类提案的上级章节无效")
        if subject is None:
            subject = session.scalar(select(Subject).where(Subject.name == subject_name))
            if subject is None:
                subject = Subject(id=new_id("sub"), name=subject_name)
                session.add(subject)
                session.flush()
        fields["subject_id"] = subject.id
        if chapter_name:
            parent = session.get(Chapter, parent_id) if parent_id else None
            if parent_id and parent is None:
                raise DomainError("新分类提案的上级章节不存在")
            self.app._validate_chapter_parent(
                session,
                subject_id=subject.id,
                parent_id=parent.id if parent else None,
            )
            existing = session.scalar(
                select(Chapter).where(
                    Chapter.subject_id == subject.id,
                    Chapter.parent_id == (parent.id if parent else None),
                    Chapter.name == chapter_name,
                )
            )
            if existing is None:
                self.app._ensure_unique_chapter_name(
                    session,
                    subject_id=subject.id,
                    parent_id=parent.id if parent else None,
                    name=chapter_name,
                )
                self.app._ensure_no_chapter_alias_conflict(
                    session,
                    chapter_id=None,
                    name=chapter_name,
                    parent_id=parent.id if parent else None,
                )
                existing = Chapter(
                    id=new_id("ch"),
                    subject_id=subject.id,
                    parent_id=parent.id if parent else None,
                    name=chapter_name,
                )
                session.add(existing)
                session.flush()
            fields["chapter_id"] = existing.id

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

    def create_problem_set(
        self,
        *,
        title: str,
        material_markdown: str,
        children: list[tuple[dict[str, Any], list[str] | None]],
        image_paths: list[Path],
        source_book: str | None = None,
        source_year: str | None = None,
    ) -> list[Problem]:
        """Atomically save shared material once and independently reviewable children."""

        if not children:
            raise DomainError("题组至少需要一个子题")
        sources = [self.store.store_copy(Path(path), role="original") for path in image_paths]
        unique_sources = {
            stored.sha256: stored for stored in sources
        }
        if not material_markdown.strip() and not unique_sources:
            raise DomainError("题组需要共享材料或至少一张来源图片")

        created_ids: list[str] = []
        snapshots: list[tuple[str, dict[str, Any]]] = []
        with self.runtime.session_factory() as session:
            problem_set = ProblemSet(
                id=new_id("pset"),
                title=title.strip(),
                material_markdown=material_markdown,
                source_book=source_book,
                source_year=source_year,
            )
            session.add(problem_set)
            for order, stored in enumerate(unique_sources.values()):
                problem_set.assets.append(
                    ProblemSetAsset(
                        id=new_id("psasset"),
                        sort_order=order,
                        sha256=stored.sha256,
                        relative_path=stored.relative_path,
                        mime_type=stored.mime_type,
                        size_bytes=stored.size_bytes,
                    )
                )

            for order, (fields, tag_names) in enumerate(children):
                payload = self._normalize_fields(fields)
                self._validate_catalog(
                    session, payload.get("subject_id"), payload.get("chapter_id")
                )
                problem = Problem(
                    id=new_id("problem"),
                    status="active",
                    human_confirmed=True,
                    revision=1,
                    problem_set=problem_set,
                    item_order=order,
                    **payload,
                )
                session.add(problem)
                tags = _normalized_tags(tag_names)
                for name in tags:
                    tag = session.scalar(select(Tag).where(Tag.name == name))
                    if tag is None:
                        tag = Tag(id=new_id("tag"), name=name, is_system=False)
                        session.add(tag)
                        session.flush()
                    problem.tags.append(tag)
                session.flush()
                snapshot = sync_snapshot(problem, tags)
                session.add(
                    Version(
                        id=new_id("ver"),
                        problem_id=problem.id,
                        revision=1,
                        source="problem_set",
                        summary="题组子题确认入库",
                        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                        created_by=self.runtime.identity.user_id,
                    )
                )
                created_ids.append(problem.id)
                snapshots.append((problem.id, snapshot))
            session.add(
                AuditLog(
                    id=new_id("audit"),
                    action="problem_set_created",
                    entity_type="problem_set",
                    entity_id=problem_set.id,
                    detail_json=json.dumps(
                        {"problem_count": len(created_ids), "asset_count": len(unique_sources)},
                        ensure_ascii=False,
                    ),
                    actor=self.runtime.identity.user_id,
                )
            )
            session.commit()
            created = session.scalars(
                select(Problem)
                .where(Problem.id.in_(created_ids))
                .options(selectinload(Problem.tags), selectinload(Problem.assets))
                .order_by(Problem.item_order)
            ).all()
            session.expunge_all()

        for problem_id, snapshot in snapshots:
            SyncService(self.runtime).record_problem_update(
                problem_id, before={}, after=snapshot, operation="create"
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
            '"subject_id": "sub_x", "chapter_id": "ch_x", "problem_type": "计算题", '
            '"priority": 3, "taxonomy_proposal": null, '
            '"region": {"x": 0.05, "y": 0.10, "width": 0.90, "height": 0.35}, '
            '"uncertain_fields": []}]}',
            "即使只有一道题也使用 problems 数组；不要把多道题拼进同一个题干。",
            "region 是该题在原图中的归一化矩形坐标，左上角为原点，四个值均为 0 到 1；无法判断时使用整图 {\"x\":0,\"y\":0,\"width\":1,\"height\":1}。",
            "每道题都必须判断 subject_id、chapter_id、problem_type 和 priority（1-5）。若题干可明确匹配下方已有章节，必须填写对应 chapter_id，不能只填标签或仅填科目。",
            "章节判断必须参考该考研科目的通用考试大纲与教材章节体系。不能因为本地章节为空或缺少目标章节就省略判断；此时必须在 taxonomy_proposal 中给出规范章节名、判断理由和置信度。章节名应是“函数、极限与连续”这类稳定知识章节，不能拿“计算题”“难题”或零散标签代替。",
            "question_markdown、correct_answer、solution_markdown、notes 等 Markdown 字段中的公式必须使用 $...$ 或 $$...$$ 定界；不要输出无定界符的裸公式。",
            "question_latex 只写裸 LaTeX，不要再包 $、$$、\\(\\) 或 \\[\\] 定界符。",
            "subject_id/chapter_id 必须从以下本地目录中成对选择，chapter_id 必须属于该 subject_id；没有合适目录时留空，并输出 taxonomy_proposal（subject_name、parent_chapter_id、chapter_name、reason、confidence），confidence 为 0 到 1；仅提出建议，不能编造 ID。",
            "本地分类目录（按科目分组）：",
        ]
        choices_by_subject: dict[str, list[Any]] = {}
        for choice in self.app.list_category_choices():
            choices_by_subject.setdefault(choice.subject_id, []).append(choice)
        for choices in choices_by_subject.values():
            subject = choices[0]
            lines.append(f"- 科目：{subject.subject_name} (subject_id={subject.subject_id})")
            for choice in choices:
                if choice.chapter_id:
                    lines.append(
                        f"  - 章节：{choice.label} (chapter_id={choice.chapter_id})"
                    )
        return "\n".join(lines)

    def _infer_chapter_id(self, subject_id: str | None, fields: dict[str, Any]) -> str | None:
        """Return one unambiguous local chapter inferred from recognized content."""

        if not subject_id:
            return None
        choices = [
            choice
            for choice in self.app.list_category_choices()
            if choice.subject_id == subject_id and choice.chapter_id
        ]
        if not choices:
            return None
        tags = fields.get("tags")
        tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
        content = " ".join(
            str(fields.get(name) or "")
            for name in ("title", "question_markdown", "question_latex", "correct_answer", "solution_markdown")
        ) + " " + tag_text
        normalized = content.casefold()
        scores: dict[str, int] = {}
        for choice in choices:
            chapter_name = choice.chapter_path[-1].casefold()
            if len(chapter_name) >= 2 and chapter_name in normalized:
                scores[choice.chapter_id] = scores.get(choice.chapter_id, 0) + 100
            for keywords, chapter_markers in _CHAPTER_KEYWORD_GROUPS:
                if any(keyword.casefold() in normalized for keyword in keywords) and any(
                    marker.casefold() in chapter_name for marker in chapter_markers
                ):
                    scores[choice.chapter_id] = scores.get(choice.chapter_id, 0) + 10
        if not scores:
            return None
        highest = max(scores.values())
        matched = [chapter_id for chapter_id, score in scores.items() if score == highest]
        return matched[0] if len(matched) == 1 else None

    def recognize_user_answer_image(
        self,
        image_path: Path,
        *,
        keywords: str = "",
    ) -> str:
        """Extract only a user's written answer from one selected image."""

        if not image_path.is_file():
            raise DomainError("作答图片不存在")
        provider = get_provider(self.runtime.settings)
        provider.validate_configuration()
        prompt = (
            "这是用户补录作答的图片。只提取用户写下的作答内容，保留原有"
            "文本、数学公式和 LaTeX；不要解题、不要补全缺失步骤、不要"
            "生成正确答案或解析。严格只输出 JSON 对象："
            '{"user_answer":"提取到的内容"}。'
        )
        if keywords.strip():
            prompt += "\n用户提供的定位关键词：" + keywords.strip()
        result = provider.structure_from_image(
            image_path=str(image_path),
            prompt=prompt,
            model=self.runtime.settings.ai.default_vision_model,
            timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
        )
        answer = str(result.fields.get("user_answer") or "").strip()
        if not answer:
            raise DomainError("AI 没有识别出可填入的作答内容")
        return answer

    def start_ai(
        self,
        image_paths: list[Path],
        *,
        user_instruction: str = "",
        recognition_mode: str = "auto",
        use_recognition_cache: bool = True,
    ) -> AiIntakeSession:
        """Import images as staging records and start a job-scoped intake."""

        if not image_paths:
            raise DomainError("请先添加需要识别的图片")
        if recognition_mode not in _RECOGNITION_MODES:
            raise DomainError("不支持的识别方式")
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
                draft_json=json.dumps(
                    {
                        "recognition_mode": recognition_mode,
                        "use_recognition_cache": use_recognition_cache,
                    }
                ),
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
            mode_instruction = {
                "auto": (
                    "先按每张图片独立识别；如检测到跨页材料，仅提出结构建议，不自动合并。"
                    "在 JSON 顶层额外返回 layout_kind（single / independent / composite / continuation）、"
                    "subquestion_count、confidence（0-1）、rationale 和 signals 数组；"
                    "它们仅供用户决策，不得改变 problems 的输出。"
                ),
                "one_to_one": "每张图片只提取一道题，不得把同图其他内容拆成额外题目。",
                "one_to_many": "每张图片可拆出多道独立候选题，并为每题返回来源区域。",
                "many_to_one": "本批图片按上传顺序共同描述同一道题或材料；只返回一个候选题。",
            }[recognition_mode]
            job = self.ai.create_intake_structure_job(
                intake_session_id,
                asset_ids,
                user_instruction="\n\n".join([*instruction_parts, mode_instruction]),
                recognition_mode=recognition_mode,
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
            cache_hits=int(diagnostics["cache_hits"]),
            provider_token_usage=dict(diagnostics["provider_token_usage"]),
            provider_token_samples=int(diagnostics["provider_token_samples"]),
            provider_server_timing=list(diagnostics["provider_server_timing"]),
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
                subjects = {subject.id for subject in session.scalars(select(Subject)).all()}
                chapters = {chapter.id: chapter.subject_id for chapter in session.scalars(select(Chapter)).all()}
                for item in rows:
                    try:
                        fields = json.loads(item.fields_json)
                        uncertain = json.loads(item.uncertain_json)
                        region = json.loads(item.region_json)
                    except json.JSONDecodeError as exc:
                        raise DomainError("AI 录题候选 JSON 无效") from exc
                    asset = assets.get(item.intake_asset_id)
                    if isinstance(fields, dict):
                        subject_id = fields.get("subject_id")
                        chapter_id = fields.get("chapter_id")
                        if subject_id and subject_id not in subjects:
                            fields.pop("subject_id", None)
                            fields.pop("chapter_id", None)
                            uncertain = [*uncertain, {"field": "subject_id", "reason": "AI 返回的分类 ID 不在允许集合中"}]
                        elif chapter_id and chapters.get(chapter_id) != subject_id:
                            fields.pop("chapter_id", None)
                            uncertain = [*uncertain, {"field": "chapter_id", "reason": "AI 返回的章节 ID 不属于当前科目"}]
                        if fields.get("subject_id") and not fields.get("chapter_id"):
                            inferred_chapter_id = self._infer_chapter_id(
                                fields["subject_id"], fields
                            )
                            if inferred_chapter_id:
                                fields["chapter_id"] = inferred_chapter_id
                        proposal = fields.get("taxonomy_proposal")
                        if isinstance(proposal, dict):
                            confidence = proposal.get("confidence")
                            confidence_text = (
                                f"（置信度 {float(confidence):.0%}）"
                                if isinstance(confidence, (int, float))
                                else ""
                            )
                            uncertain = [
                                *uncertain,
                                {
                                    "field": "taxonomy_proposal",
                                    "reason": (
                                        f"章节建议{confidence_text}："
                                        f"{proposal.get('reason') or '待确认后创建'}"
                                    ),
                                },
                            ]
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
                            source_images=self.candidate_source_images(item.id),
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

    def candidate_source_images(self, candidate_id: str) -> list[Path]:
        """Return an AI candidate's ordered immutable source images."""

        with self.runtime.session_factory() as session:
            unit_ids = session.scalars(
                select(IntakeCandidateUnit.recognition_unit_id).where(
                    IntakeCandidateUnit.candidate_id == candidate_id
                )
            ).all()
            if unit_ids:
                assets = session.execute(
                    select(IntakeAsset)
                    .join(
                        IntakeRecognitionUnitAsset,
                        IntakeRecognitionUnitAsset.intake_asset_id == IntakeAsset.id,
                    )
                    .join(
                        IntakeRecognitionUnit,
                        IntakeRecognitionUnit.id
                        == IntakeRecognitionUnitAsset.recognition_unit_id,
                    )
                    .where(IntakeRecognitionUnit.id.in_(unit_ids))
                    .order_by(
                        IntakeRecognitionUnit.sort_order,
                        IntakeRecognitionUnitAsset.sort_order,
                    )
                ).scalars().all()
                return [self.store.resolve(asset.relative_path) for asset in assets]
            candidate = session.get(IntakeCandidateRecord, candidate_id)
            asset = (
                session.get(IntakeAsset, candidate.intake_asset_id)
                if candidate is not None
                else None
            )
            return [self.store.resolve(asset.relative_path)] if asset else []

    def reorder_candidate_source_images(
        self, candidate_id: str, image_paths: list[Path]
    ) -> None:
        """Persist a user-selected order for a single recognition unit's images."""

        with self.runtime.session_factory() as session:
            unit_ids = session.scalars(
                select(IntakeCandidateUnit.recognition_unit_id).where(
                    IntakeCandidateUnit.candidate_id == candidate_id
                )
            ).all()
            if len(unit_ids) != 1:
                raise DomainError("当前候选没有可排序的单一识别单元")
            members = session.scalars(
                select(IntakeRecognitionUnitAsset).where(
                    IntakeRecognitionUnitAsset.recognition_unit_id == unit_ids[0]
                )
            ).all()
            by_path = {
                self.store.resolve(session.get(IntakeAsset, member.intake_asset_id).relative_path): member
                for member in members
                if session.get(IntakeAsset, member.intake_asset_id) is not None
            }
            requested = [Path(path) for path in image_paths]
            if len(requested) != len(members) or set(requested) != set(by_path):
                raise DomainError("来源图片顺序无效")
            for order, path in enumerate(requested):
                by_path[path].sort_order = order
            session.commit()

    def split_candidate_recognition_unit(self, candidate_id: str) -> None:
        """Replace one multi-image candidate source unit with ordered single-image units."""

        with self.runtime.session_factory() as session:
            candidate = session.get(IntakeCandidateRecord, candidate_id)
            unit_ids = session.scalars(
                select(IntakeCandidateUnit.recognition_unit_id).where(
                    IntakeCandidateUnit.candidate_id == candidate_id
                )
            ).all()
            if candidate is None or len(unit_ids) != 1:
                raise DomainError("当前候选没有可拆分的识别单元")
            old_unit = session.get(IntakeRecognitionUnit, unit_ids[0])
            members = session.scalars(
                select(IntakeRecognitionUnitAsset)
                .where(IntakeRecognitionUnitAsset.recognition_unit_id == unit_ids[0])
                .order_by(IntakeRecognitionUnitAsset.sort_order)
            ).all()
            if old_unit is None or len(members) < 2:
                raise DomainError("识别单元至少需要两张图片才能拆分")
            session.execute(
                delete(IntakeCandidateUnit).where(
                    IntakeCandidateUnit.candidate_id == candidate_id
                )
            )
            for order, member in enumerate(members):
                unit = IntakeRecognitionUnit(
                    id=new_id("iunit"), session_id=old_unit.session_id,
                    mode="one_to_one", sort_order=old_unit.sort_order + order,
                )
                session.add(unit)
                session.add(IntakeRecognitionUnitAsset(
                    recognition_unit_id=unit.id, intake_asset_id=member.intake_asset_id, sort_order=0
                ))
                session.flush()
                session.add(IntakeCandidateUnit(candidate_id=candidate_id, recognition_unit_id=unit.id))
            session.commit()

    def commit_ai_candidates_as_problem_set(
        self, candidate_ids: list[str], *, title: str, material_markdown: str
    ) -> list[Problem]:
        """Promote selected pending intake candidates to independently reviewable set children."""

        unique_ids = list(dict.fromkeys(candidate_ids))
        if not unique_ids:
            raise DomainError("请先选择至少一个待确认候选")
        with self.runtime.session_factory() as session:
            candidates = session.scalars(
                select(IntakeCandidateRecord).where(
                    IntakeCandidateRecord.id.in_(unique_ids),
                    IntakeCandidateRecord.status == "pending",
                )
            ).all()
            if len(candidates) != len(unique_ids):
                raise DomainError("候选题已经处理或不存在")
            by_id = {candidate.id: candidate for candidate in candidates}
            children = []
            source_paths: list[Path] = []
            for candidate_id in unique_ids:
                candidate = by_id[candidate_id]
                try:
                    fields = json.loads(candidate.fields_json)
                except json.JSONDecodeError as exc:
                    raise DomainError("候选题字段无效") from exc
                children.append((fields if isinstance(fields, dict) else {}, fields.get("tags", []) if isinstance(fields, dict) else []))
                source_paths.extend(self.candidate_source_images(candidate_id))
        created = self.create_problem_set(
            title=title, material_markdown=material_markdown,
            children=children, image_paths=source_paths,
        )
        with self.runtime.session_factory() as session:
            session_ids: set[str] = set()
            for candidate_id, problem in zip(unique_ids, created, strict=True):
                candidate = session.get(IntakeCandidateRecord, candidate_id)
                if candidate is not None:
                    candidate.status = "committed"
                    candidate.problem_id = problem.id
                    candidate.decided_at = utcnow()
                    session_ids.add(candidate.session_id)
            for session_id in session_ids:
                remaining = session.scalar(
                    select(func.count()).select_from(IntakeCandidateRecord).where(
                        IntakeCandidateRecord.session_id == session_id,
                        IntakeCandidateRecord.status == "pending",
                    )
                )
                if not remaining:
                    intake_session = session.get(IntakeSession, session_id)
                    if intake_session is not None:
                        intake_session.status = "completed"
                        intake_session.completed_at = utcnow()
            session.commit()
        return created

    def structure_suggestions(self, job_id: str) -> list[IntakeStructureSuggestion]:
        """Return advisory-only automatic layout suggestions for an intake job."""

        with self.runtime.session_factory() as session:
            intake_session = session.scalar(
                select(IntakeSession).where(IntakeSession.job_id == job_id)
            )
            if intake_session is None:
                return []
            try:
                draft = json.loads(intake_session.draft_json)
            except json.JSONDecodeError:
                return []
        raw_suggestions = draft.get("structure_suggestions", {}) if isinstance(draft, dict) else {}
        if not isinstance(raw_suggestions, dict):
            return []
        result: list[IntakeStructureSuggestion] = []
        for unit_id, value in raw_suggestions.items():
            if not isinstance(unit_id, str) or not isinstance(value, dict):
                continue
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
                continue
            result.append(
                IntakeStructureSuggestion(
                    unit_id=unit_id,
                    layout_kind=kind,
                    subquestion_count=max(1, min(count, 99)),
                    confidence=max(0.0, min(float(confidence), 1.0)),
                    rationale=rationale[:240],
                    signals=[str(signal)[:80] for signal in signals[:8]],
                )
            )
        return result

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

    def commit_ai_candidate(
        self,
        review_item_id: str,
        fields: dict[str, Any],
        *,
        tag_names: list[str] | None = None,
    ) -> Problem:
        """Apply the edited candidate, then promote its staging problem."""

        resolved_fields = dict(fields)
        with self.runtime.session_factory() as session:
            self._apply_taxonomy_proposal(session, resolved_fields)
            session.commit()
        payload = self._normalize_fields(resolved_fields)
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
