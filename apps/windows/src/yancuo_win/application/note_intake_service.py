"""Recoverable staging for AI/custom note classification drafts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from yancuo_win.ai.base import normalize_region
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.note_service import NOTE_BLOCK_TYPES
from yancuo_win.assets.object_store import ObjectStore, StoredObject
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    Chapter,
    NoteDraftBlock,
    NoteDraftGroup,
    NoteAsset,
    NoteBlock,
    NoteDocument,
    NoteIntakeAsset,
    NoteIntakeSession,
    Subject,
    Tag,
    utcnow,
)
from yancuo_win.domain.rules import DomainError

CLASSIFICATION_MODES = frozenset({"ai", "custom"})
NOTE_INTAKE_STATUSES = frozenset(
    {"draft", "processing", "review", "failed", "completed", "cancelled"}
)
RESUMABLE_NOTE_INTAKE_STATUSES = frozenset(
    {"draft", "processing", "review", "failed"}
)
CATEGORY_RESOLUTIONS = frozenset({"unresolved", "existing", "create_new"})


@dataclass(frozen=True)
class NoteDraftBlockInput:
    block_type: str
    content_markdown: str = ""
    content_latex: str = ""
    source_asset_id: str | None = None
    source_region: dict[str, float] = field(default_factory=dict)
    uncertain_fields: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class NoteDraftGroupInput:
    title: str = ""
    summary: str = ""
    category_resolution: str = "unresolved"
    subject_id: str | None = None
    chapter_id: str | None = None
    proposed_subject: str = ""
    proposed_chapter: str = ""
    proposal: dict[str, Any] = field(default_factory=dict)
    tag_ids: tuple[str, ...] = ()
    proposed_tags: tuple[str, ...] = ()
    target_status: str = "inbox"
    blocks: tuple[NoteDraftBlockInput, ...] = ()


class NoteIntakeService:
    """Persist note extraction before it is promoted to formal documents."""

    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime
        self.store = ObjectStore(runtime.paths.asset_objects_dir)

    def start_session(
        self,
        source_paths: list[Path],
        *,
        classification_mode: str,
        user_instruction: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> NoteIntakeSession:
        self._validate_classification_mode(classification_mode)
        paths = [Path(path) for path in source_paths]
        if len(paths) != 1:
            raise DomainError("当前笔记分类暂存每次只支持一张来源图片")
        metadata_json = self._dump_json(metadata or {}, "笔记草稿元数据")

        stored_sources: list[tuple[Path, StoredObject]] = []
        seen_hashes: set[str] = set()
        for path in paths:
            stored = self.store.store_copy(path, role="original")
            if stored.sha256 in seen_hashes:
                continue
            seen_hashes.add(stored.sha256)
            stored_sources.append((path, stored))
        if not stored_sources:
            raise DomainError("没有可保存的笔记来源图片")

        with self.runtime.session_factory() as session:
            intake = NoteIntakeSession(
                id=new_id("nintake"),
                classification_mode=classification_mode,
                status="draft",
                user_instruction=user_instruction.strip(),
                draft_meta_json=metadata_json,
            )
            session.add(intake)
            session.flush()
            for asset_order, (path, stored) in enumerate(stored_sources):
                session.add(
                    NoteIntakeAsset(
                        id=new_id("niasset"),
                        session_id=intake.id,
                        sort_order=asset_order,
                        role="original",
                        original_name=path.name,
                        sha256=stored.sha256,
                        relative_path=stored.relative_path,
                        mime_type=stored.mime_type,
                        size_bytes=stored.size_bytes,
                        is_immutable=True,
                    )
                )
            intake_id = intake.id
            session.commit()
        loaded = self.get_session(intake_id)
        assert loaded is not None
        return loaded

    def save_extraction(
        self,
        session_id: str,
        *,
        metadata: dict[str, Any],
        groups: list[NoteDraftGroupInput] | tuple[NoteDraftGroupInput, ...],
    ) -> NoteIntakeSession:
        """Atomically replace all draft groups and move the session to review."""

        group_inputs = tuple(groups)
        if not group_inputs:
            raise DomainError("笔记草稿至少需要一个分类组")
        metadata_json = self._dump_json(metadata, "笔记草稿元数据")

        # Validate JSON and basic values before deleting the previous snapshot.
        prepared: list[
            tuple[
                NoteDraftGroupInput,
                str,
                str,
                str,
                list[tuple[NoteDraftBlockInput, str, str]],
            ]
        ] = []
        for group in group_inputs:
            self._validate_group(group)
            proposal_json = self._dump_json(group.proposal, "分类建议")
            tag_ids_json = self._dump_json(list(group.tag_ids), "分类组标签")
            proposed_tags_json = self._dump_json(
                list(group.proposed_tags), "分类组建议标签"
            )
            blocks: list[tuple[NoteDraftBlockInput, str, str]] = []
            for block in group.blocks:
                self._validate_block(block)
                region_json = self._dump_json(
                    normalize_region(block.source_region), "内容块来源区域"
                )
                uncertain_json = self._dump_json(
                    block.uncertain_fields, "内容块不确定字段"
                )
                blocks.append((block, region_json, uncertain_json))
            prepared.append(
                (
                    group,
                    proposal_json,
                    tag_ids_json,
                    proposed_tags_json,
                    blocks,
                )
            )

        with self.runtime.session_factory() as session:
            intake = self._get_open_session(session, session_id)
            asset_ids = set(
                session.scalars(
                    select(NoteIntakeAsset.id).where(
                        NoteIntakeAsset.session_id == session_id
                    )
                ).all()
            )
            for prepared_group in prepared:
                group, _proposal_json, _tag_ids_json, _proposed_tags_json, blocks = (
                    prepared_group
                )
                self._validate_catalog(
                    session,
                    resolution=group.category_resolution,
                    subject_id=group.subject_id,
                    chapter_id=group.chapter_id,
                    proposed_subject=group.proposed_subject,
                    proposed_chapter=group.proposed_chapter,
                )
                if group.tag_ids:
                    existing_tag_ids = set(
                        session.scalars(
                            select(Tag.id).where(Tag.id.in_(group.tag_ids))
                        ).all()
                    )
                    if existing_tag_ids != set(group.tag_ids):
                        raise DomainError("笔记草稿组包含不存在的标签")
                for block, _region_json, _uncertain_json in blocks:
                    if block.source_asset_id and block.source_asset_id not in asset_ids:
                        raise DomainError("笔记草稿块引用了其他会话的来源图片")

            existing = list(
                session.scalars(
                    select(NoteDraftGroup)
                    .where(NoteDraftGroup.session_id == session_id)
                    .options(selectinload(NoteDraftGroup.blocks))
                ).all()
            )
            for group in existing:
                session.delete(group)
            session.flush()

            for group_order, prepared_group in enumerate(prepared):
                (
                    group_input,
                    proposal_json,
                    tag_ids_json,
                    proposed_tags_json,
                    blocks,
                ) = prepared_group
                group = NoteDraftGroup(
                    id=new_id("ndgroup"),
                    session_id=session_id,
                    sort_order=group_order,
                    title=group_input.title.strip(),
                    summary=group_input.summary,
                    category_resolution=group_input.category_resolution,
                    subject_id=group_input.subject_id,
                    chapter_id=group_input.chapter_id,
                    proposed_subject=group_input.proposed_subject.strip(),
                    proposed_chapter=group_input.proposed_chapter.strip(),
                    proposal_json=proposal_json,
                    tag_ids_json=tag_ids_json,
                    proposed_tags_json=proposed_tags_json,
                    target_status=group_input.target_status,
                )
                session.add(group)
                session.flush()
                for block_order, (block_input, region_json, uncertain_json) in enumerate(blocks):
                    session.add(
                        NoteDraftBlock(
                            id=new_id("ndblock"),
                            group_id=group.id,
                            source_asset_id=block_input.source_asset_id,
                            sort_order=block_order,
                            block_type=block_input.block_type,
                            content_markdown=block_input.content_markdown,
                            content_latex=block_input.content_latex,
                            source_region_json=region_json,
                            uncertain_json=uncertain_json,
                        )
                    )
            intake.draft_meta_json = metadata_json
            intake.status = "review"
            intake.error_message = ""
            intake.completed_at = None
            intake.updated_at = utcnow()
            session.commit()

        loaded = self.get_session(session_id)
        assert loaded is not None
        return loaded

    def save_flat_draft(
        self,
        session_id: str,
        draft: Any,
    ) -> NoteIntakeSession:
        """Adapt the existing single-note extraction result to one unresolved group."""

        intake = self.get_session(session_id)
        if intake is None:
            raise DomainError("笔记录入会话不存在")
        source_asset_id = intake.assets[0].id if intake.assets else None
        blocks = tuple(
            NoteDraftBlockInput(
                block_type=str(block.block_type),
                content_markdown=str(block.content_markdown),
                content_latex=str(block.content_latex),
                source_asset_id=source_asset_id,
                source_region=dict(block.source_region),
                uncertain_fields=list(block.uncertain_fields),
            )
            for block in draft.blocks
        )
        metadata = {
            "title": str(draft.title),
            "summary": str(draft.summary),
            "subject_suggestion": str(draft.subject_suggestion),
            "chapter_suggestion": str(draft.chapter_suggestion),
            "tags": list(draft.tags),
            "uncertain_fields": list(draft.uncertain_fields),
            "model": str(draft.model),
            "cost_estimate": float(draft.cost_estimate),
        }
        return self.save_extraction(
            session_id,
            metadata=metadata,
            groups=[
                NoteDraftGroupInput(
                    title=str(draft.title),
                    summary=str(draft.summary),
                    proposed_tags=tuple(str(tag) for tag in draft.tags),
                    blocks=blocks,
                )
            ],
        )

    def save_grouped_draft(self, session_id: str, draft: Any) -> NoteIntakeSession:
        """Persist AI-proposed groups without granting them catalog authority."""

        intake = self.get_session(session_id)
        if intake is None:
            raise DomainError("笔记录入会话不存在")
        source_asset_id = intake.assets[0].id if intake.assets else None
        metadata = {
            "title": str(draft.title),
            "summary": str(draft.summary),
            "subject_suggestion": str(draft.subject_suggestion),
            "chapter_suggestion": str(draft.chapter_suggestion),
            "tags": list(draft.tags),
            "uncertain_fields": list(draft.uncertain_fields),
            "model": str(draft.model),
            "cost_estimate": float(draft.cost_estimate),
        }

        if intake.classification_mode != "ai" or not getattr(draft, "groups", None):
            return self.save_flat_draft(session_id, draft)

        groups = []
        for group in draft.groups:
            blocks = tuple(
                NoteDraftBlockInput(
                    block_type=str(block.block_type),
                    content_markdown=str(block.content_markdown),
                    content_latex=str(block.content_latex),
                    source_asset_id=source_asset_id,
                    source_region=dict(block.source_region),
                    uncertain_fields=list(block.uncertain_fields),
                )
                for block in group.blocks
            )
            if not blocks:
                continue
            groups.append(
                NoteDraftGroupInput(
                    title=str(group.title),
                    summary=str(group.summary),
                    proposed_subject=str(group.subject_suggestion),
                    proposed_chapter=str(group.chapter_suggestion),
                    proposal={"reason": str(group.reason)},
                    proposed_tags=tuple(str(tag) for tag in group.tags),
                    blocks=blocks,
                )
            )
        if not groups:
            return self.save_flat_draft(session_id, draft)
        return self.save_extraction(session_id, metadata=metadata, groups=groups)

    def get_session(self, session_id: str) -> NoteIntakeSession | None:
        with self.runtime.session_factory() as session:
            intake = session.scalar(self._session_statement(session_id=session_id))
            if intake is None:
                return None
            session.expunge_all()
            return intake

    def list_resumable_sessions(self) -> list[NoteIntakeSession]:
        with self.runtime.session_factory() as session:
            rows = list(
                session.scalars(
                    self._session_statement(
                        statuses=RESUMABLE_NOTE_INTAKE_STATUSES
                    ).order_by(NoteIntakeSession.updated_at.desc())
                ).all()
            )
            session.expunge_all()
            return rows

    def latest_resumable_session(self) -> NoteIntakeSession | None:
        rows = self.list_resumable_sessions()
        return rows[0] if rows else None

    def mark_processing(self, session_id: str) -> NoteIntakeSession:
        return self._set_status(session_id, "processing", error_message="")

    def mark_failed(self, session_id: str, error_message: str) -> NoteIntakeSession:
        return self._set_status(
            session_id,
            "failed",
            error_message=error_message.strip() or "笔记识别失败",
        )

    def mark_completed(self, session_id: str) -> NoteIntakeSession:
        return self._set_status(session_id, "completed", error_message="")

    def abandon_session(self, session_id: str) -> NoteIntakeSession:
        intake = self.get_session(session_id)
        if intake is None:
            raise DomainError("笔记录入会话不存在")
        if intake.status == "cancelled":
            return intake
        if intake.status == "completed":
            raise DomainError("已完成的笔记录入会话不能放弃")
        return self._set_status(session_id, "cancelled", error_message="")

    def purge_cancelled_sessions(self) -> int:
        """Delete cancelled drafts and then remove only truly orphaned objects."""

        relative_paths: set[str] = set()
        with self.runtime.session_factory() as session:
            rows = list(
                session.scalars(
                    select(NoteIntakeSession)
                    .where(NoteIntakeSession.status == "cancelled")
                    .options(selectinload(NoteIntakeSession.assets))
                ).all()
            )
            for intake in rows:
                relative_paths.update(asset.relative_path for asset in intake.assets)
                session.delete(intake)
            session.commit()
        if relative_paths:
            from yancuo_win.application.services import AppServices

            AppServices(self.runtime)._remove_unreferenced_asset_files(relative_paths)
        return len(rows)

    def recover_interrupted_sessions(self) -> int:
        """Convert stale processing rows to explicit, user-retryable failures."""

        with self.runtime.session_factory() as session:
            rows = list(
                session.scalars(
                    select(NoteIntakeSession).where(
                        NoteIntakeSession.status == "processing"
                    )
                ).all()
            )
            for intake in rows:
                intake.status = "failed"
                intake.error_message = "上次识别在完成前中断，可重新尝试"
                intake.updated_at = utcnow()
            session.commit()
            return len(rows)

    def resolve_source_path(self, asset: NoteIntakeAsset) -> Path:
        return self.store.resolve(asset.relative_path)

    def update_group(
        self,
        session_id: str,
        group_id: str,
        *,
        title: str,
        summary: str,
        category_resolution: str,
        subject_id: str | None = None,
        chapter_id: str | None = None,
        proposed_subject: str = "",
        proposed_chapter: str = "",
    ) -> NoteIntakeSession:
        """Update one classification row without replacing its draft blocks."""

        candidate = NoteDraftGroupInput(
            title=title,
            summary=summary,
            category_resolution=category_resolution,
            subject_id=subject_id,
            chapter_id=chapter_id,
            proposed_subject=proposed_subject,
            proposed_chapter=proposed_chapter,
        )
        self._validate_group(candidate)
        with self.runtime.session_factory() as session:
            intake = self._get_open_session(session, session_id)
            group = session.get(NoteDraftGroup, group_id)
            if group is None or group.session_id != intake.id:
                raise DomainError("笔记草稿分类组不属于当前会话")
            self._validate_catalog(
                session,
                resolution=candidate.category_resolution,
                subject_id=candidate.subject_id,
                chapter_id=candidate.chapter_id,
                proposed_subject=candidate.proposed_subject,
                proposed_chapter=candidate.proposed_chapter,
            )
            group.title = candidate.title.strip()
            group.summary = candidate.summary
            group.category_resolution = candidate.category_resolution
            group.subject_id = candidate.subject_id
            group.chapter_id = candidate.chapter_id
            group.proposed_subject = candidate.proposed_subject.strip()
            group.proposed_chapter = candidate.proposed_chapter.strip()
            group.updated_at = utcnow()
            intake.updated_at = utcnow()
            session.commit()
        return self._require_session(session_id)

    def add_group(self, session_id: str, *, title: str = "") -> NoteIntakeSession:
        """Append an unresolved, empty classification group to a review draft."""

        candidate = NoteDraftGroupInput(title=title)
        self._validate_group(candidate)
        with self.runtime.session_factory() as session:
            intake = self._get_open_session(session, session_id)
            next_order = session.scalar(
                select(NoteDraftGroup.sort_order)
                .where(NoteDraftGroup.session_id == session_id)
                .order_by(NoteDraftGroup.sort_order.desc())
                .limit(1)
            )
            session.add(
                NoteDraftGroup(
                    id=new_id("ndgroup"),
                    session_id=session_id,
                    sort_order=(next_order + 1) if next_order is not None else 0,
                    title=candidate.title.strip(),
                    category_resolution="unresolved",
                    target_status="inbox",
                )
            )
            intake.updated_at = utcnow()
            session.commit()
        return self._require_session(session_id)

    def delete_group(self, session_id: str, group_id: str) -> NoteIntakeSession:
        """Delete only an empty group so classification edits cannot discard blocks."""

        with self.runtime.session_factory() as session:
            intake = self._get_open_session(session, session_id)
            group = session.get(NoteDraftGroup, group_id)
            if group is None or group.session_id != intake.id:
                raise DomainError("笔记草稿分类组不属于当前会话")
            has_blocks = session.scalar(
                select(NoteDraftBlock.id).where(NoteDraftBlock.group_id == group.id).limit(1)
            )
            if has_blocks is not None:
                raise DomainError("含有内容块的分类组不能删除，请先合并到其他分类组")
            session.delete(group)
            intake.updated_at = utcnow()
            session.commit()
        return self._require_session(session_id)

    def merge_groups(
        self, session_id: str, *, source_group_id: str, target_group_id: str
    ) -> NoteIntakeSession:
        """Move all blocks into the target group, preserving their source metadata."""

        if source_group_id == target_group_id:
            raise DomainError("请选择不同的源分类组和目标分类组")
        with self.runtime.session_factory() as session:
            intake = self._get_open_session(session, session_id)
            source = session.get(NoteDraftGroup, source_group_id)
            target = session.get(NoteDraftGroup, target_group_id)
            if (
                source is None
                or target is None
                or source.session_id != intake.id
                or target.session_id != intake.id
            ):
                raise DomainError("笔记草稿分类组不属于当前会话")
            next_order = session.scalar(
                select(NoteDraftBlock.sort_order)
                .where(NoteDraftBlock.group_id == target.id)
                .order_by(NoteDraftBlock.sort_order.desc())
                .limit(1)
            )
            for offset, block in enumerate(
                session.scalars(
                    select(NoteDraftBlock)
                    .where(NoteDraftBlock.group_id == source.id)
                    .order_by(NoteDraftBlock.sort_order)
                ).all()
            ):
                block.group = target
                block.sort_order = (next_order + 1 if next_order is not None else 0) + offset
            # Flush the reassignment before deleting the source; otherwise its
            # delete-orphan cascade can still consider the moved blocks children.
            session.flush()
            session.delete(source)
            target.updated_at = utcnow()
            intake.updated_at = utcnow()
            session.commit()
        return self._require_session(session_id)

    def move_block(
        self,
        session_id: str,
        block_id: str,
        *,
        target_group_id: str,
        target_index: int | None = None,
    ) -> NoteIntakeSession:
        """Move a draft block and normalize affected group ordering atomically."""

        with self.runtime.session_factory() as session:
            intake = self._get_open_session(session, session_id)
            block = session.get(NoteDraftBlock, block_id)
            target = session.get(NoteDraftGroup, target_group_id)
            if block is None or target is None:
                raise DomainError("笔记草稿内容块或分类组不存在")
            source = session.get(NoteDraftGroup, block.group_id)
            if (
                source is None
                or source.session_id != intake.id
                or target.session_id != intake.id
            ):
                raise DomainError("笔记草稿内容块或分类组不属于当前会话")

            source_blocks = list(
                session.scalars(
                    select(NoteDraftBlock)
                    .where(NoteDraftBlock.group_id == source.id)
                    .order_by(NoteDraftBlock.sort_order, NoteDraftBlock.id)
                ).all()
            )
            target_blocks = source_blocks if source.id == target.id else list(
                session.scalars(
                    select(NoteDraftBlock)
                    .where(NoteDraftBlock.group_id == target.id)
                    .order_by(NoteDraftBlock.sort_order, NoteDraftBlock.id)
                ).all()
            )
            source_index = source_blocks.index(block)
            source_blocks.remove(block)
            if source.id == target.id:
                target_blocks = source_blocks
            index = len(target_blocks) if target_index is None else target_index
            if source.id == target.id and target_index is not None and index > source_index:
                index -= 1
            if not 0 <= index <= len(target_blocks):
                raise DomainError("内容块目标位置无效")
            target_blocks.insert(index, block)

            if source.id != target.id:
                for order, item in enumerate(source_blocks):
                    item.sort_order = order
                block.group = target
            for order, item in enumerate(target_blocks):
                item.sort_order = order
            source.updated_at = utcnow()
            target.updated_at = utcnow()
            intake.updated_at = utcnow()
            session.commit()
        return self._require_session(session_id)

    def confirm_groups(self, session_id: str) -> tuple[NoteDocument, ...]:
        """Promote every non-empty draft group to an independent formal note."""

        with self.runtime.session_factory() as session:
            intake = session.scalar(self._session_statement(session_id=session_id))
            if intake is None:
                raise DomainError("笔记录入会话不存在")
            if intake.status != "review":
                raise DomainError("只有等待确认的笔记草稿可以入库")
            groups = [group for group in intake.groups if group.blocks]
            if not groups:
                raise DomainError("笔记草稿没有可入库的内容块")
            if not intake.assets:
                raise DomainError("笔记草稿缺少来源图片")
            metadata = self._load_json(intake.draft_meta_json, "笔记草稿元数据")
            source_asset = intake.assets[0]
            note_ids: list[str] = []
            for group in groups:
                if group.target_status not in {"inbox", "active"}:
                    raise DomainError("笔记草稿组目标状态只能是 inbox 或 active")
                if (
                    group.category_resolution == "unresolved"
                    and group.target_status != "inbox"
                ):
                    raise DomainError("未分类笔记草稿只能放入待整理")
                subject_id, chapter_id = self._resolve_group_category(session, group)
                tag_ids = self._load_json(group.tag_ids_json, "分类组标签")
                if not isinstance(tag_ids, list) or any(not isinstance(item, str) for item in tag_ids):
                    raise DomainError("分类组标签格式无效")
                tags = list(session.scalars(select(Tag).where(Tag.id.in_(tag_ids))).all())
                if len(tags) != len(tag_ids) or len(set(tag_ids)) != len(tag_ids):
                    raise DomainError("分类组标签包含不存在或重复的标签")
                fallback_title = str(metadata.get("title", "")).strip()
                note = NoteDocument(
                    id=new_id("note"),
                    title=(group.title.strip() or fallback_title or "未命名笔记")[:256],
                    summary=group.summary,
                    subject_id=subject_id,
                    chapter_id=chapter_id,
                    status=group.target_status,
                )
                note.tags = tags
                session.add(note)
                session.flush()
                session.add(
                    NoteAsset(
                        id=new_id("nasset"),
                        note_document_id=note.id,
                        role="original",
                        relative_path=source_asset.relative_path,
                        sha256=source_asset.sha256,
                        mime_type=source_asset.mime_type or "",
                        size_bytes=source_asset.size_bytes or 0,
                        is_immutable=True,
                    )
                )
                for order, block in enumerate(group.blocks):
                    session.add(
                        NoteBlock(
                            id=new_id("nblock"),
                            note_document_id=note.id,
                            sort_order=order,
                            block_type=block.block_type,
                            content_markdown=block.content_markdown,
                            content_latex=block.content_latex,
                            source_region_json=block.source_region_json,
                            uncertain_json=block.uncertain_json,
                        )
                    )
                group.note_document_id = note.id
                group.decided_at = utcnow()
                note_ids.append(note.id)
            intake.status = "completed"
            intake.error_message = ""
            intake.completed_at = utcnow()
            intake.updated_at = utcnow()
            session.commit()
        from yancuo_win.application.note_service import NoteService

        notes = NoteService(self.runtime)
        committed = tuple(notes.get_note(note_id) for note_id in note_ids)
        if any(note is None for note in committed):
            raise DomainError("笔记入库后无法重新读取")
        return tuple(note for note in committed if note is not None)

    def _require_session(self, session_id: str) -> NoteIntakeSession:
        loaded = self.get_session(session_id)
        assert loaded is not None
        return loaded

    def _set_status(
        self,
        session_id: str,
        status: str,
        *,
        error_message: str,
    ) -> NoteIntakeSession:
        if status not in NOTE_INTAKE_STATUSES:
            raise DomainError(f"不支持的笔记录入状态：{status}")
        with self.runtime.session_factory() as session:
            intake = self._get_open_session(session, session_id)
            if status == "processing" and intake.status not in {"draft", "failed", "review"}:
                raise DomainError("当前笔记录入状态不能重新开始识别")
            if status == "completed" and intake.status != "review":
                raise DomainError("笔记草稿尚未进入确认状态")
            intake.status = status
            intake.error_message = error_message
            intake.updated_at = utcnow()
            intake.completed_at = utcnow() if status in {"completed", "cancelled"} else None
            session.commit()
        loaded = self.get_session(session_id)
        assert loaded is not None
        return loaded

    @staticmethod
    def _session_statement(
        *,
        session_id: str | None = None,
        statuses: frozenset[str] | None = None,
    ):
        statement = select(NoteIntakeSession).options(
            selectinload(NoteIntakeSession.assets),
            selectinload(NoteIntakeSession.groups).selectinload(
                NoteDraftGroup.blocks
            ),
        )
        if session_id is not None:
            statement = statement.where(NoteIntakeSession.id == session_id)
        if statuses is not None:
            statement = statement.where(NoteIntakeSession.status.in_(statuses))
        return statement

    @staticmethod
    def _get_open_session(session, session_id: str) -> NoteIntakeSession:
        intake = session.get(NoteIntakeSession, session_id)
        if intake is None:
            raise DomainError("笔记录入会话不存在")
        if intake.status in {"completed", "cancelled"}:
            raise DomainError("笔记录入会话已经结束")
        return intake

    @staticmethod
    def _validate_classification_mode(mode: str) -> None:
        if mode not in CLASSIFICATION_MODES:
            raise DomainError(f"不支持的笔记分类方式：{mode}")

    @staticmethod
    def _validate_group(group: NoteDraftGroupInput) -> None:
        if group.category_resolution not in CATEGORY_RESOLUTIONS:
            raise DomainError(f"不支持的分类解析状态：{group.category_resolution}")
        if len(group.title.strip()) > 256:
            raise DomainError("笔记草稿组标题不能超过 256 个字符")
        if group.target_status not in {"inbox", "active"}:
            raise DomainError("笔记草稿组目标状态只能是 inbox 或 active")
        if (
            group.category_resolution == "unresolved"
            and group.target_status != "inbox"
        ):
            raise DomainError("未分类笔记草稿只能放入待整理")
        if len(group.tag_ids) != len(set(group.tag_ids)):
            raise DomainError("笔记草稿组标签不能重复")

    @staticmethod
    def _validate_block(block: NoteDraftBlockInput) -> None:
        if block.block_type not in NOTE_BLOCK_TYPES:
            raise DomainError(f"不支持的笔记块类型：{block.block_type}")
        if not isinstance(block.uncertain_fields, list) or any(
            not isinstance(item, dict) for item in block.uncertain_fields
        ):
            raise DomainError("内容块不确定字段必须是对象列表")

    @staticmethod
    def _validate_catalog(
        session,
        *,
        resolution: str,
        subject_id: str | None,
        chapter_id: str | None,
        proposed_subject: str,
        proposed_chapter: str,
    ) -> None:
        subject = session.get(Subject, subject_id) if subject_id else None
        if subject_id and subject is None:
            raise DomainError("笔记草稿选择的科目不存在")
        chapter = session.get(Chapter, chapter_id) if chapter_id else None
        if chapter_id and chapter is None:
            raise DomainError("笔记草稿选择的章节不存在")
        if chapter is not None and subject_id and chapter.subject_id != subject_id:
            raise DomainError("笔记草稿选择的章节不属于当前科目")
        if resolution == "existing" and subject is None:
            raise DomainError("已有分类必须选择科目")
        if resolution == "create_new" and not (
            proposed_subject.strip() or proposed_chapter.strip()
        ):
            raise DomainError("新分类必须填写建议科目或章节")

    @staticmethod
    def _resolve_group_category(session, group: NoteDraftGroup) -> tuple[str | None, str | None]:
        if group.category_resolution == "unresolved":
            return None, None
        if group.category_resolution == "existing":
            NoteIntakeService._validate_catalog(
                session,
                resolution="existing",
                subject_id=group.subject_id,
                chapter_id=group.chapter_id,
                proposed_subject="",
                proposed_chapter="",
            )
            return group.subject_id, group.chapter_id
        NoteIntakeService._validate_catalog(
            session,
            resolution="create_new",
            subject_id=None,
            chapter_id=None,
            proposed_subject=group.proposed_subject,
            proposed_chapter=group.proposed_chapter,
        )
        subject_name = group.proposed_subject.strip()
        chapter_name = group.proposed_chapter.strip()
        if not subject_name:
            return None, None
        subject = session.scalar(select(Subject).where(Subject.name == subject_name))
        if subject is None:
            subject = Subject(id=new_id("subject"), name=subject_name)
            session.add(subject)
            session.flush()
        if not chapter_name:
            return subject.id, None
        chapter = session.scalar(
            select(Chapter).where(
                Chapter.subject_id == subject.id,
                Chapter.parent_id.is_(None),
                Chapter.name == chapter_name,
            )
        )
        if chapter is None:
            chapter = Chapter(
                id=new_id("chapter"),
                subject_id=subject.id,
                name=chapter_name,
                parent_id=None,
                sort_order=0,
            )
            session.add(chapter)
            session.flush()
        return subject.id, chapter.id

    @staticmethod
    def _load_json(value: str, label: str) -> Any:
        try:
            return json.loads(value or "{}")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DomainError(f"{label}不是有效 JSON 数据") from exc

    @staticmethod
    def _dump_json(value: Any, label: str) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise DomainError(f"{label}不是有效 JSON 数据") from exc
