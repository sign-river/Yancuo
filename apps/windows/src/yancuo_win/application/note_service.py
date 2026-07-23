"""Local note document and ordered block services."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import (
    NoteBlock,
    NoteDocument,
    Tag,
    note_tags,
    utcnow,
)
from yancuo_win.domain.rules import (
    DomainError,
    assert_transition,
    validate_status,
)

NOTE_BLOCK_TYPES = frozenset({"heading", "text", "formula", "image", "callout"})
_EDITABLE_STATUSES = frozenset({"inbox", "active", "archived"})


class NoteService:
    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime

    def session(self):
        return self.runtime.session_factory()

    def create_note(
        self,
        *,
        title: str = "",
        summary: str = "",
        subject_id: str | None = None,
        chapter_id: str | None = None,
        status: str = "inbox",
    ) -> NoteDocument:
        validate_status(status)
        title = title.strip()
        if len(title) > 256:
            raise DomainError("笔记标题不能超过 256 个字符")
        with self.session() as session:
            note = NoteDocument(
                id=new_id("note"),
                title=title,
                summary=summary,
                subject_id=subject_id,
                chapter_id=chapter_id,
                status=status,
            )
            session.add(note)
            session.commit()
            return self._load(session, note.id)

    def get_note(self, note_id: str) -> NoteDocument | None:
        with self.session() as session:
            return self._load(session, note_id, allow_missing=True)

    def list_notes(
        self,
        *,
        status: str | None = None,
        subject_id: str | None = None,
        chapter_id: str | None = None,
    ) -> list[NoteDocument]:
        if status is not None:
            validate_status(status)
        with self.session() as session:
            statement = (
                select(NoteDocument)
                .options(
                    selectinload(NoteDocument.blocks),
                    selectinload(NoteDocument.tags),
                    selectinload(NoteDocument.assets),
                )
                .order_by(NoteDocument.updated_at.desc())
            )
            if status is not None:
                statement = statement.where(NoteDocument.status == status)
            if subject_id is not None:
                statement = statement.where(NoteDocument.subject_id == subject_id)
            if chapter_id is not None:
                statement = statement.where(NoteDocument.chapter_id == chapter_id)
            rows = list(session.scalars(statement).all())
            session.expunge_all()
            return rows

    def update_note(self, note_id: str, values: dict[str, Any]) -> NoteDocument:
        allowed = {"title", "summary", "subject_id", "chapter_id", "status"}
        unknown = set(values) - allowed
        if unknown:
            raise DomainError(f"不允许修改笔记字段：{', '.join(sorted(unknown))}")
        with self.session() as session:
            note = session.get(NoteDocument, note_id)
            if note is None:
                raise DomainError("笔记不存在")
            self._assert_editable(note)
            if "status" in values:
                target = validate_status(str(values["status"]))
                assert_transition(note.status, target)
                note.status = target
                note.deleted_at = utcnow() if target == "trashed" else None
            if "title" in values:
                title = str(values["title"]).strip()
                if len(title) > 256:
                    raise DomainError("笔记标题不能超过 256 个字符")
                note.title = title
            for key in ("summary", "subject_id", "chapter_id"):
                if key in values:
                    setattr(note, key, values[key])
            note.revision += 1
            note.updated_at = utcnow()
            session.commit()
            return self._load(session, note.id)

    def add_block(
        self,
        note_id: str,
        *,
        block_type: str,
        content_markdown: str = "",
        content_latex: str = "",
        sort_order: int | None = None,
        source_region: dict[str, float] | None = None,
        uncertain_fields: list[dict[str, str]] | None = None,
    ) -> NoteBlock:
        self._validate_block_type(block_type)
        with self.session() as session:
            note = session.get(NoteDocument, note_id)
            if note is None:
                raise DomainError("笔记不存在")
            self._assert_editable(note)
            if sort_order is None:
                max_order = session.scalar(
                    select(NoteBlock.sort_order)
                    .where(NoteBlock.note_document_id == note_id)
                    .order_by(NoteBlock.sort_order.desc())
                    .limit(1)
                )
                sort_order = int(max_order or 0) + 1
            block = NoteBlock(
                id=new_id("nblock"),
                note_document_id=note_id,
                block_type=block_type,
                content_markdown=content_markdown,
                content_latex=content_latex,
                sort_order=self._validate_sort_order(sort_order),
                source_region_json=json.dumps(
                    source_region or {}, ensure_ascii=False, separators=(",", ":")
                ),
                uncertain_json=json.dumps(
                    uncertain_fields or [], ensure_ascii=False, separators=(",", ":")
                ),
            )
            session.add(block)
            note.revision += 1
            note.updated_at = utcnow()
            session.commit()
            session.refresh(block)
            session.expunge(block)
            return block

    def update_block(self, block_id: str, values: dict[str, Any]) -> NoteBlock:
        allowed = {
            "block_type",
            "content_markdown",
            "content_latex",
            "source_region",
            "uncertain_fields",
        }
        unknown = set(values) - allowed
        if unknown:
            raise DomainError(f"不允许修改笔记块字段：{', '.join(sorted(unknown))}")
        with self.session() as session:
            block = session.scalar(
                select(NoteBlock)
                .where(NoteBlock.id == block_id)
                .options(selectinload(NoteBlock.document))
            )
            if block is None:
                raise DomainError("笔记块不存在")
            self._assert_editable(block.document)
            if "block_type" in values:
                self._validate_block_type(str(values["block_type"]))
                block.block_type = str(values["block_type"])
            for key in ("content_markdown", "content_latex"):
                if key in values:
                    setattr(block, key, str(values[key]))
            if "source_region" in values:
                block.source_region_json = json.dumps(
                    values["source_region"] or {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if "uncertain_fields" in values:
                block.uncertain_json = json.dumps(
                    values["uncertain_fields"] or [],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            block.document.revision += 1
            block.document.updated_at = utcnow()
            session.commit()
            session.refresh(block)
            session.expunge(block)
            return block

    def delete_block(self, block_id: str) -> None:
        with self.session() as session:
            block = session.scalar(
                select(NoteBlock)
                .where(NoteBlock.id == block_id)
                .options(selectinload(NoteBlock.document))
            )
            if block is None:
                return
            note = block.document
            self._assert_editable(note)
            session.delete(block)
            session.flush()
            remaining = list(
                session.scalars(
                    select(NoteBlock)
                    .where(NoteBlock.note_document_id == note.id)
                    .order_by(NoteBlock.sort_order, NoteBlock.created_at)
                ).all()
            )
            for index, item in enumerate(remaining):
                item.sort_order = index
            note.revision += 1
            note.updated_at = utcnow()
            session.commit()

    def reorder_blocks(self, note_id: str, block_ids: list[str]) -> NoteDocument:
        with self.session() as session:
            note = self._load(session, note_id, allow_missing=False, detach=False)
            self._assert_editable(note)
            blocks = list(
                session.scalars(
                    select(NoteBlock).where(NoteBlock.note_document_id == note_id)
                ).all()
            )
            existing = {block.id for block in blocks}
            if set(block_ids) != existing or len(block_ids) != len(existing):
                raise DomainError("笔记块排序必须完整包含当前文档的每个块，且不能重复")
            by_id = {block.id: block for block in blocks}
            for index, block_id in enumerate(block_ids):
                by_id[block_id].sort_order = index
            note.revision += 1
            note.updated_at = utcnow()
            session.commit()
            return self._load(session, note_id)

    def set_tags(self, note_id: str, tag_ids: list[str]) -> NoteDocument:
        if len(tag_ids) != len(set(tag_ids)):
            raise DomainError("笔记标签不能重复")
        with self.session() as session:
            note = self._load(session, note_id, allow_missing=False, detach=False)
            self._assert_editable(note)
            tags = list(session.scalars(select(Tag).where(Tag.id.in_(tag_ids))).all())
            if len(tags) != len(tag_ids):
                raise DomainError("笔记标签包含不存在的标签")
            note.tags = tags
            note.revision += 1
            note.updated_at = utcnow()
            session.commit()
            return self._load(session, note_id)

    def trash_note(self, note_id: str) -> NoteDocument:
        return self.update_note(note_id, {"status": "trashed"})

    def restore_note(self, note_id: str) -> NoteDocument:
        with self.session() as session:
            note = self._load(session, note_id, allow_missing=False, detach=False)
            assert note is not None
            if note.status != "trashed":
                raise DomainError("只能恢复回收站中的笔记")
            assert_transition(note.status, "active")
            note.status = "active"
            note.deleted_at = None
            note.revision += 1
            note.updated_at = utcnow()
            session.commit()
            return self._load(session, note_id)

    def delete_note_permanently(self, note_id: str) -> None:
        with self.session() as session:
            note = session.get(NoteDocument, note_id)
            if note is None:
                return
            if note.status != "trashed":
                raise DomainError("只能永久删除回收站中的笔记")
            session.execute(delete(note_tags).where(note_tags.c.note_document_id == note_id))
            session.delete(note)
            session.commit()

    @staticmethod
    def _load(
        session,
        note_id: str,
        *,
        allow_missing: bool = False,
        detach: bool = True,
    ) -> NoteDocument | None:
        statement = (
            select(NoteDocument)
            .where(NoteDocument.id == note_id)
            .options(
                selectinload(NoteDocument.blocks),
                selectinload(NoteDocument.tags),
                selectinload(NoteDocument.assets),
            )
        )
        note = session.scalar(statement)
        if note is None:
            if allow_missing:
                return None
            raise DomainError("笔记不存在")
        if detach:
            session.expunge(note)
        return note

    @staticmethod
    def _assert_editable(note: NoteDocument) -> None:
        if note.status not in _EDITABLE_STATUSES:
            raise DomainError("回收站中的笔记不可编辑，请先恢复")

    @staticmethod
    def _validate_block_type(block_type: str) -> None:
        if block_type not in NOTE_BLOCK_TYPES:
            raise DomainError(f"不支持的笔记块类型：{block_type}")

    @staticmethod
    def _validate_sort_order(sort_order: int) -> int:
        if sort_order < 0:
            raise DomainError("笔记块顺序不能为负数")
        return sort_order
