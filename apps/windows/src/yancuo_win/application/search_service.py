"""Completely local problem search projection and FTS5 queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import bindparam, delete, event, select, text
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.session import Session, sessionmaker

from yancuo_win.application.services import AppServices, KnowledgeScope
from yancuo_win.data.models import (
    Chapter,
    Problem,
    SearchDocument,
    Subject,
    Tag,
)

if TYPE_CHECKING:
    from yancuo_win.application.bootstrap import RuntimeContext


@dataclass(frozen=True)
class SearchHit:
    problem_id: str
    title: str
    snippet: str
    knowledge_path: str
    status: str
    score: float


@dataclass(frozen=True)
class SearchIndexHealth:
    canonical_count: int
    projection_count: int
    fts_count: int
    missing_problem_ids: tuple[str, ...] = ()
    orphaned_problem_ids: tuple[str, ...] = ()
    stale_problem_ids: tuple[str, ...] = ()
    stale_fts_problem_ids: tuple[str, ...] = ()

    @property
    def is_consistent(self) -> bool:
        return not (
            self.missing_problem_ids
            or self.orphaned_problem_ids
            or self.stale_problem_ids
            or self.stale_fts_problem_ids
            or self.canonical_count != self.projection_count
            or self.projection_count != self.fts_count
        )

    @property
    def summary(self) -> str:
        state = "正常" if self.is_consistent else "需要重建"
        return (
            f"{state} · 题目 {self.canonical_count} · "
            f"投影 {self.projection_count} · FTS {self.fts_count}"
        )


_CHANGED_IDS_KEY = "yancuo_search_changed_ids"
_DELETED_IDS_KEY = "yancuo_search_deleted_ids"
_REBUILD_KEY = "yancuo_search_rebuild"
_HOOKS_INSTALLED_ATTR = "_yancuo_search_hooks_installed"


class SearchIndexService:
    """Build and query a disposable read projection over canonical problem data."""

    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime
        self.app = AppServices(runtime)

    @staticmethod
    def _problem_body(problem: Problem) -> str:
        values = (
            problem.question_markdown,
            problem.question_latex,
            problem.user_answer,
            problem.correct_answer,
            problem.solution_markdown,
            problem.error_analysis,
            problem.notes,
            problem.source_book,
            problem.source_year,
            problem.page_number,
            problem.original_number,
            problem.problem_type,
        )
        return "\n".join(str(value).strip() for value in values if value)

    @staticmethod
    def _knowledge_paths(
        subjects: list[Subject],
        chapters: list[Chapter],
    ) -> dict[str, str]:
        subject_names = {subject.id: subject.name for subject in subjects}
        chapter_by_id = {chapter.id: chapter for chapter in chapters}
        cache: dict[str, str] = {}

        def resolve(chapter: Chapter) -> str:
            if chapter.id in cache:
                return cache[chapter.id]
            names = [chapter.name]
            seen = {chapter.id}
            parent_id = chapter.parent_id
            while parent_id:
                if parent_id in seen:
                    raise RuntimeError("章节目录包含循环引用，无法建立搜索索引")
                seen.add(parent_id)
                parent = chapter_by_id.get(parent_id)
                if parent is None or parent.subject_id != chapter.subject_id:
                    raise RuntimeError("章节目录包含无效的上级引用，无法建立搜索索引")
                names.append(parent.name)
                parent_id = parent.parent_id
            names.append(subject_names.get(chapter.subject_id, "未知科目"))
            path = " / ".join(reversed(names))
            cache[chapter.id] = path
            return path

        return {chapter.id: resolve(chapter) for chapter in chapters}

    @classmethod
    def _documents(
        cls,
        session: Session,
        problem_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        subjects = list(session.scalars(select(Subject)).all())
        chapters = list(session.scalars(select(Chapter)).all())
        statement = select(Problem).options(selectinload(Problem.tags))
        if problem_ids is not None:
            if not problem_ids:
                return []
            statement = statement.where(Problem.id.in_(problem_ids))
        problems = list(session.scalars(statement).all())
        subject_names = {subject.id: subject.name for subject in subjects}
        chapter_paths = cls._knowledge_paths(subjects, chapters)
        documents: list[dict[str, Any]] = []
        for problem in problems:
            if problem.chapter_id:
                knowledge_path = chapter_paths.get(
                    problem.chapter_id,
                    subject_names.get(problem.subject_id or "", "未分类"),
                )
            elif problem.subject_id:
                knowledge_path = (
                    f"{subject_names.get(problem.subject_id, '未知科目')} / 未分类"
                )
            else:
                knowledge_path = "未分类"
            documents.append(
                {
                    "problem_id": problem.id,
                    "status": problem.status,
                    "subject_id": problem.subject_id,
                    "chapter_id": problem.chapter_id,
                    "knowledge_path": knowledge_path,
                    "title": (problem.title or "").strip(),
                    "body": cls._problem_body(problem),
                    "tags_text": " ".join(sorted(tag.name for tag in problem.tags)),
                    "updated_at": problem.updated_at,
                }
            )
        return documents

    @staticmethod
    def _delete_ids(session: Session, problem_ids: set[str]) -> None:
        if not problem_ids:
            return
        session.execute(
            delete(SearchDocument).where(SearchDocument.problem_id.in_(problem_ids))
        )
        statement = text(
            "DELETE FROM search_documents_fts WHERE problem_id IN :problem_ids"
        ).bindparams(bindparam("problem_ids", expanding=True))
        session.execute(statement, {"problem_ids": tuple(problem_ids)})

    @classmethod
    def _replace(
        cls,
        session: Session,
        *,
        problem_ids: set[str] | None = None,
    ) -> int:
        documents = cls._documents(session, problem_ids)
        if problem_ids is None:
            session.execute(delete(SearchDocument))
            session.execute(text("DELETE FROM search_documents_fts"))
        else:
            cls._delete_ids(session, problem_ids)
        if documents:
            session.execute(SearchDocument.__table__.insert(), documents)
            session.execute(
                text(
                    """
                    INSERT INTO search_documents_fts(
                        problem_id, title, body, tags_text, knowledge_path
                    ) VALUES (
                        :problem_id, :title, :body, :tags_text, :knowledge_path
                    )
                    """
                ),
                documents,
            )
        return len(documents)

    def rebuild(self) -> int:
        """Atomically rebuild the projection and FTS table from canonical rows."""

        with self.runtime.session_factory() as session:
            count = self._replace(session)
            session.commit()
            return count

    def upsert(self, problem_id: str) -> bool:
        """Refresh one problem; return False when the canonical row no longer exists."""

        with self.runtime.session_factory() as session:
            count = self._replace(session, problem_ids={problem_id})
            session.commit()
            return bool(count)

    def delete(self, problem_id: str) -> None:
        with self.runtime.session_factory() as session:
            self._delete_ids(session, {problem_id})
            session.commit()

    @staticmethod
    def _signature(document: dict[str, Any]) -> tuple[Any, ...]:
        return (
            document["status"],
            document["subject_id"],
            document["chapter_id"],
            document["knowledge_path"],
            document["title"],
            document["body"],
            document["tags_text"],
        )

    def check_consistency(self) -> SearchIndexHealth:
        """Compare canonical, projection, and FTS content without changing data."""

        with self.runtime.session_factory() as session:
            canonical = {
                document["problem_id"]: document
                for document in self._documents(session)
            }
            projection = {
                row["problem_id"]: dict(row)
                for row in session.execute(
                    select(SearchDocument.__table__)
                ).mappings()
            }
            fts = {
                row["problem_id"]: dict(row)
                for row in session.execute(
                    text(
                        """
                        SELECT problem_id, title, body, tags_text, knowledge_path
                        FROM search_documents_fts
                        """
                    )
                ).mappings()
            }
        canonical_ids = set(canonical)
        projection_ids = set(projection)
        fts_ids = set(fts)
        stale = tuple(
            sorted(
                problem_id
                for problem_id in canonical_ids & projection_ids
                if self._signature(canonical[problem_id])
                != self._signature(projection[problem_id])
            )
        )
        stale_fts = tuple(
            sorted(
                problem_id
                for problem_id in projection_ids & fts_ids
                if (
                    projection[problem_id]["title"],
                    projection[problem_id]["body"],
                    projection[problem_id]["tags_text"],
                    projection[problem_id]["knowledge_path"],
                )
                != (
                    fts[problem_id]["title"],
                    fts[problem_id]["body"],
                    fts[problem_id]["tags_text"],
                    fts[problem_id]["knowledge_path"],
                )
            )
        )
        return SearchIndexHealth(
            canonical_count=len(canonical),
            projection_count=len(projection),
            fts_count=len(fts),
            missing_problem_ids=tuple(sorted(canonical_ids - projection_ids)),
            orphaned_problem_ids=tuple(
                sorted((projection_ids | fts_ids) - canonical_ids)
            ),
            stale_problem_ids=stale,
            stale_fts_problem_ids=stale_fts,
        )

    def repair_if_needed(self) -> SearchIndexHealth:
        health = self.check_consistency()
        if health.is_consistent:
            return health
        self.rebuild()
        return self.check_consistency()

    def search(
        self,
        query: str,
        *,
        scope: KnowledgeScope | None = None,
        statuses: tuple[str, ...] = ("active",),
        limit: int = 50,
    ) -> tuple[SearchHit, ...]:
        """Search locally, using trigram FTS and a short-query fallback."""

        query = query.strip()
        if not query or not statuses or limit < 1:
            return ()

        where = ["d.status IN :statuses"]
        parameters: dict[str, object] = {
            "statuses": statuses,
            "limit": min(int(limit), 200),
        }
        if scope is not None:
            if scope.subject_id:
                where.append("d.subject_id = :subject_id")
                parameters["subject_id"] = scope.subject_id
            if scope.only_uncategorized:
                where.append("d.chapter_id IS NULL")
            elif scope.chapter_id:
                chapter_ids = (
                    self.app.chapter_subtree_ids(scope.chapter_id)
                    if scope.include_descendants
                    else (scope.chapter_id,)
                )
                where.append("d.chapter_id IN :chapter_ids")
                parameters["chapter_ids"] = chapter_ids

        if len(query) >= 3:
            where.insert(0, "search_documents_fts MATCH :match_query")
            parameters["match_query"] = f'"{query.replace(chr(34), chr(34) * 2)}"'
            statement = text(
                f"""
                SELECT
                    d.problem_id,
                    d.title,
                    snippet(
                        search_documents_fts, 2, '<mark>', '</mark>', '…', 24
                    ) AS snippet,
                    d.knowledge_path,
                    d.status,
                    bm25(search_documents_fts) AS score
                FROM search_documents_fts
                JOIN search_documents AS d
                  ON d.problem_id = search_documents_fts.problem_id
                WHERE {' AND '.join(where)}
                ORDER BY score, d.updated_at DESC
                LIMIT :limit
                """
            )
        else:
            where.insert(
                0,
                "("
                "d.title LIKE :like_query OR d.body LIKE :like_query "
                "OR d.tags_text LIKE :like_query "
                "OR d.knowledge_path LIKE :like_query"
                ")",
            )
            parameters["like_query"] = f"%{query}%"
            statement = text(
                f"""
                SELECT
                    d.problem_id,
                    d.title,
                    CASE
                        WHEN d.title LIKE :like_query THEN d.title
                        ELSE substr(d.body, 1, 160)
                    END AS snippet,
                    d.knowledge_path,
                    d.status,
                    0.0 AS score
                FROM search_documents AS d
                WHERE {' AND '.join(where)}
                ORDER BY d.updated_at DESC
                LIMIT :limit
                """
            )

        statement = statement.bindparams(bindparam("statuses", expanding=True))
        if "chapter_ids" in parameters:
            statement = statement.bindparams(bindparam("chapter_ids", expanding=True))
        with self.runtime.engine.connect() as connection:
            rows = connection.execute(statement, parameters).mappings().all()
        return tuple(
            SearchHit(
                problem_id=row["problem_id"],
                title=row["title"],
                snippet=row["snippet"] or "",
                knowledge_path=row["knowledge_path"],
                status=row["status"],
                score=float(row["score"]),
            )
            for row in rows
        )

    def browse(
        self,
        *,
        scope: KnowledgeScope | None = None,
        statuses: tuple[str, ...] = ("active",),
        limit: int | None = 200,
    ) -> tuple[SearchHit, ...]:
        """Return recent local rows inside a trusted boundary without text matching."""

        if not statuses or (limit is not None and limit < 1):
            return ()
        where = ["d.status IN :statuses"]
        parameters: dict[str, object] = {"statuses": statuses}
        limit_clause = ""
        if limit is not None:
            parameters["limit"] = min(int(limit), 10_000)
            limit_clause = "LIMIT :limit"
        if scope is not None:
            if scope.subject_id:
                where.append("d.subject_id = :subject_id")
                parameters["subject_id"] = scope.subject_id
            if scope.only_uncategorized:
                where.append("d.chapter_id IS NULL")
            elif scope.chapter_id:
                chapter_ids = (
                    self.app.chapter_subtree_ids(scope.chapter_id)
                    if scope.include_descendants
                    else (scope.chapter_id,)
                )
                where.append("d.chapter_id IN :chapter_ids")
                parameters["chapter_ids"] = chapter_ids
        statement = text(
            f"""
            SELECT
                d.problem_id,
                d.title,
                substr(d.body, 1, 160) AS snippet,
                d.knowledge_path,
                d.status,
                0.0 AS score
            FROM search_documents AS d
            WHERE {' AND '.join(where)}
            ORDER BY d.updated_at DESC
            {limit_clause}
            """
        ).bindparams(bindparam("statuses", expanding=True))
        if "chapter_ids" in parameters:
            statement = statement.bindparams(bindparam("chapter_ids", expanding=True))
        with self.runtime.engine.connect() as connection:
            rows = connection.execute(statement, parameters).mappings().all()
        return tuple(
            SearchHit(
                problem_id=row["problem_id"],
                title=row["title"],
                snippet=row["snippet"] or "",
                knowledge_path=row["knowledge_path"],
                status=row["status"],
                score=0.0,
            )
            for row in rows
        )


def _capture_search_changes(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    changed = session.info.setdefault(_CHANGED_IDS_KEY, set())
    deleted = session.info.setdefault(_DELETED_IDS_KEY, set())
    for item in session.new.union(session.dirty):
        if isinstance(item, Problem):
            changed.add(item.id)
        elif isinstance(item, (Subject, Chapter, Tag)):
            session.info[_REBUILD_KEY] = True
    for item in session.deleted:
        if isinstance(item, Problem):
            deleted.add(item.id)
            changed.discard(item.id)
        elif isinstance(item, (Subject, Chapter, Tag)):
            session.info[_REBUILD_KEY] = True
    if deleted:
        # search_documents has a protective FK to problems. Remove derived
        # rows before the ORM emits the canonical DELETE; the same transaction
        # still rolls both operations back together on failure.
        SearchIndexService._delete_ids(session, set(deleted))
        deleted.clear()


def _apply_search_changes(session: Session, _flush_context: object) -> None:
    rebuild = bool(session.info.pop(_REBUILD_KEY, False))
    changed = set(session.info.pop(_CHANGED_IDS_KEY, set()))
    deleted = set(session.info.pop(_DELETED_IDS_KEY, set()))
    if rebuild:
        SearchIndexService._replace(session)
        return
    if deleted:
        SearchIndexService._delete_ids(session, deleted)
    changed.difference_update(deleted)
    if changed:
        SearchIndexService._replace(session, problem_ids=changed)


def install_search_index_hooks(factory: sessionmaker[Session]) -> None:
    """Install one transaction-local search projection coordinator."""

    if getattr(factory, _HOOKS_INSTALLED_ATTR, False):
        return
    event.listen(factory, "before_flush", _capture_search_changes)
    event.listen(factory, "after_flush_postexec", _apply_search_changes)
    setattr(factory, _HOOKS_INSTALLED_ATTR, True)
