"""SQLAlchemy 模型（阶段 A：MVP 必需字段 + 第二版扩展位）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class MetaKV(Base):
    """库级元数据，如 schema_version。"""

    __tablename__ = "meta_kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    chapters: Mapped[list[Chapter]] = relationship(back_populates="subject")


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_id: Mapped[str] = mapped_column(ForeignKey("subjects.id"), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    subject: Mapped[Subject] = relationship(back_populates="chapters")


class Problem(Base):
    """错题主体。状态：inbox / active / archived / trashed。"""

    __tablename__ = "problems"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="inbox", nullable=False)
    subject_id: Mapped[str | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    chapter_id: Mapped[str | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    problem_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    question_markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    question_latex: Mapped[str] = mapped_column(Text, default="", nullable=False)
    user_answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    correct_answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    solution_markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error_analysis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    source_book: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_year: Mapped[str | None] = mapped_column(String(32), nullable=True)
    page_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    original_number: Mapped[str | None] = mapped_column(String(64), nullable=True)

    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mastery: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_redo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_print: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    human_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 第二版扩展位（复习）；阶段 A 仅占位，业务逻辑不启用
    next_review_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    tags: Mapped[list[Tag]] = relationship(
        secondary="problem_tags", back_populates="problems"
    )
    assets: Mapped[list[Asset]] = relationship(back_populates="problem")
    versions: Mapped[list[Version]] = relationship(back_populates="problem")


class Asset(Base):
    """图片与附件。数据库只存相对对象路径与哈希，不存绝对路径。"""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    problem_id: Mapped[str | None] = mapped_column(ForeignKey("problems.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # original/processed/...
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_immutable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    problem: Mapped[Problem | None] = relationship(back_populates="assets")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("tags.id"), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    problems: Mapped[list[Problem]] = relationship(
        secondary="problem_tags", back_populates="tags"
    )


class ProblemTag(Base):
    __tablename__ = "problem_tags"
    __table_args__ = (UniqueConstraint("problem_id", "tag_id", name="uq_problem_tag"),)

    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id"), primary_key=True)
    tag_id: Mapped[str] = mapped_column(ForeignKey("tags.id"), primary_key=True)


class Version(Base):
    """题目修改历史（AI/人工/外部工作区共用）。"""

    __tablename__ = "versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # manual/ai/workspace/...
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    problem: Mapped[Problem] = relationship(back_populates="versions")
