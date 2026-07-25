"""Generic local projection used by the note-search slice."""

from __future__ import annotations

import hashlib

from sqlalchemy import delete, event, select, text
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.session import Session, sessionmaker

from yancuo_win.data.models import (
    NoteBlock,
    NoteCollection,
    NoteDocument,
    Tag,
    UnifiedSearchDocument,
)

_REBUILD_KEY = "yancuo_unified_note_search_rebuild"
_HOOKS_INSTALLED_ATTR = "_yancuo_unified_note_search_hooks_installed"


class UnifiedSearchIndexService:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    @staticmethod
    def _document(note: NoteDocument) -> dict[str, object]:
        body = "\n".join(
            value
            for block in note.blocks
            for value in (
                block.content_latex if block.block_type == "formula" else block.content_markdown,
            )
            if value.strip()
        )
        tags = " ".join(sorted(tag.name for tag in note.tags))
        collections = " ".join(sorted(item.title for item in note.collections))
        payload = "\n".join((note.title, note.summary, body, tags, collections))
        return {
            "entity_type": "note", "entity_id": note.id, "entity_revision": note.revision,
            "status": note.status, "subject_id": note.subject_id, "chapter_id": note.chapter_id,
            "knowledge_path": "", "title": note.title, "body": payload,
            "tags_text": tags, "collections_text": collections,
            "content_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "updated_at": note.updated_at,
        }

    def rebuild_notes(self) -> int:
        with self.runtime.session_factory() as session:
            count = self._replace_notes(session)
            session.commit()
            return count

    @classmethod
    def _replace_notes(cls, session: Session) -> int:
        notes = list(session.scalars(select(NoteDocument).options(
            selectinload(NoteDocument.blocks), selectinload(NoteDocument.tags),
            selectinload(NoteDocument.collections),
        )).all())
        session.execute(delete(UnifiedSearchDocument).where(UnifiedSearchDocument.entity_type == "note"))
        session.execute(text("DELETE FROM unified_search_documents_fts WHERE entity_type='note'"))
        documents = [cls._document(note) for note in notes]
        if documents:
            session.execute(UnifiedSearchDocument.__table__.insert(), documents)
            session.execute(text("""INSERT INTO unified_search_documents_fts
                (entity_type, entity_id, title, body, tags_text, collections_text, knowledge_path)
                VALUES (:entity_type, :entity_id, :title, :body, :tags_text, :collections_text, :knowledge_path)"""), documents)
        return len(documents)

    def repair_notes_if_needed(self) -> int:
        """Repair the disposable note projection when its two local copies diverge."""

        with self.runtime.engine.connect() as connection:
            projection_count = int(
                connection.execute(
                    text("SELECT count(*) FROM unified_search_documents WHERE entity_type='note'")
                ).scalar_one()
            )
            fts_count = int(
                connection.execute(
                    text("SELECT count(*) FROM unified_search_documents_fts WHERE entity_type='note'")
                ).scalar_one()
            )
            canonical_count = int(
                connection.execute(text("SELECT count(*) FROM note_documents")).scalar_one()
            )
        if projection_count != canonical_count or fts_count != canonical_count:
            return self.rebuild_notes()
        return canonical_count

    def upsert_note(self, note_id: str) -> bool:
        with self.runtime.session_factory() as session:
            note = session.scalar(select(NoteDocument).where(NoteDocument.id == note_id).options(
                selectinload(NoteDocument.blocks), selectinload(NoteDocument.tags),
                selectinload(NoteDocument.collections),
            ))
            session.execute(delete(UnifiedSearchDocument).where(
                UnifiedSearchDocument.entity_type == "note", UnifiedSearchDocument.entity_id == note_id
            ))
            session.execute(text("DELETE FROM unified_search_documents_fts WHERE entity_type='note' AND entity_id=:id"), {"id": note_id})
            if note is None:
                session.commit()
                return False
            document = self._document(note)
            session.execute(UnifiedSearchDocument.__table__.insert(), [document])
            session.execute(text("""INSERT INTO unified_search_documents_fts
                (entity_type, entity_id, title, body, tags_text, collections_text, knowledge_path)
                VALUES (:entity_type, :entity_id, :title, :body, :tags_text, :collections_text, :knowledge_path)"""), [document])
            session.commit()
            return True

    def search_notes(self, query: str, *, statuses: tuple[str, ...] = ("active",), limit: int = 50):
        query = query.strip()
        if not query or not statuses:
            return ()
        with self.runtime.engine.connect() as connection:
            rows = connection.execute(text("""SELECT entity_id, title, substr(body, 1, 160) AS snippet, status
                FROM unified_search_documents WHERE entity_type='note' AND status IN :statuses
                AND (title LIKE :query OR body LIKE :query OR tags_text LIKE :query OR collections_text LIKE :query)
                ORDER BY updated_at DESC LIMIT :limit""").bindparams(__import__("sqlalchemy").bindparam("statuses", expanding=True)),
                {"statuses": statuses, "query": f"%{query}%", "limit": min(limit, 200)}).mappings().all()
        return tuple(rows)


def _capture_note_search_changes(session: Session, _flush_context: object, _instances: object) -> None:
    watched = (NoteDocument, NoteBlock, NoteCollection, Tag)
    if any(isinstance(item, watched) for item in session.new.union(session.dirty).union(session.deleted)):
        session.info[_REBUILD_KEY] = True


def _apply_note_search_changes(session: Session, _flush_context: object) -> None:
    if session.info.pop(_REBUILD_KEY, False):
        UnifiedSearchIndexService._replace_notes(session)


def install_unified_search_index_hooks(factory: sessionmaker[Session]) -> None:
    """Keep the disposable note projection in the originating write transaction."""

    if getattr(factory, _HOOKS_INSTALLED_ATTR, False):
        return
    event.listen(factory, "before_flush", _capture_note_search_changes)
    event.listen(factory, "after_flush_postexec", _apply_note_search_changes)
    setattr(factory, _HOOKS_INSTALLED_ATTR, True)
