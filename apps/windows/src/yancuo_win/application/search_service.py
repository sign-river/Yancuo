"""Completely local problem search projection and FTS5 queries."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import selectinload

from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.services import AppServices, KnowledgeScope
from yancuo_win.data.models import Chapter, Problem, SearchDocument, Subject


@dataclass(frozen=True)
class SearchHit:
    problem_id: str
    title: str
    snippet: str
    knowledge_path: str
    status: str
    score: float


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

    def rebuild(self) -> int:
        """Atomically rebuild the projection and FTS table from canonical rows."""

        with self.runtime.session_factory() as session:
            subjects = list(session.scalars(select(Subject)).all())
            chapters = list(session.scalars(select(Chapter)).all())
            problems = list(
                session.scalars(
                    select(Problem).options(selectinload(Problem.tags))
                ).all()
            )
            subject_names = {subject.id: subject.name for subject in subjects}
            chapter_paths = self._knowledge_paths(subjects, chapters)

            session.execute(delete(SearchDocument))
            session.execute(text("DELETE FROM search_documents_fts"))
            documents: list[SearchDocument] = []
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
                document = SearchDocument(
                    problem_id=problem.id,
                    status=problem.status,
                    subject_id=problem.subject_id,
                    chapter_id=problem.chapter_id,
                    knowledge_path=knowledge_path,
                    title=(problem.title or "").strip(),
                    body=self._problem_body(problem),
                    tags_text=" ".join(tag.name for tag in problem.tags),
                    updated_at=problem.updated_at,
                )
                documents.append(document)
                session.add(document)
            session.flush()
            for document in documents:
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
                    {
                        "problem_id": document.problem_id,
                        "title": document.title,
                        "body": document.body,
                        "tags_text": document.tags_text,
                        "knowledge_path": document.knowledge_path,
                    },
                )
            session.commit()
            return len(documents)

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
