"""SQLAlchemy 模型（阶段 A：MVP 必需字段 + 第二版扩展位）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
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


class SearchDocument(Base):
    __tablename__ = "search_documents"

    problem_id: Mapped[str] = mapped_column(
        ForeignKey("problems.id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chapter_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    knowledge_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    title: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    tags_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    assets: Mapped[list[Asset]] = relationship(
        back_populates="problem", cascade="all, delete-orphan"
    )
    versions: Mapped[list[Version]] = relationship(
        back_populates="problem", cascade="all, delete-orphan"
    )


note_tags = Table(
    "note_tags",
    Base.metadata,
    Column(
        "note_document_id",
        String(64),
        ForeignKey("note_documents.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        String(64),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class NoteDocument(Base):
    """独立笔记文档；不复用题目的答案、作答或复习字段。"""

    __tablename__ = "note_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="inbox", nullable=False)
    subject_id: Mapped[str | None] = mapped_column(ForeignKey("subjects.id"), nullable=True)
    chapter_id: Mapped[str | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    blocks: Mapped[list[NoteBlock]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="NoteBlock.sort_order",
    )
    assets: Mapped[list[NoteAsset]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary=note_tags,
        back_populates="note_documents",
    )


class NoteBlock(Base):
    __tablename__ = "note_blocks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    note_document_id: Mapped[str] = mapped_column(
        ForeignKey("note_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_latex: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_region_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    uncertain_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    document: Mapped[NoteDocument] = relationship(back_populates="blocks")


class NoteAsset(Base):
    __tablename__ = "note_assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    note_document_id: Mapped[str] = mapped_column(
        ForeignKey("note_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    relative_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), default="", nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_immutable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[NoteDocument] = relationship(back_populates="assets")


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
    note_documents: Mapped[list[NoteDocument]] = relationship(
        secondary=note_tags,
        back_populates="tags",
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


class Prompt(Base):
    """提示词模板（不写死在业务代码中）。"""

    __tablename__ = "prompts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AiJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    prompt_key: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    total_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    done_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_items: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    allowed_fields_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list[AiJobItem]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class AiJobItem(Base):
    __tablename__ = "ai_job_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("ai_jobs.id"), nullable=False, index=True)
    problem_id: Mapped[str | None] = mapped_column(ForeignKey("problems.id"), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    intake_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("intake_assets.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    raw_response: Mapped[str] = mapped_column(Text, default="", nullable=False)
    structured_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cost_estimate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    job: Mapped[AiJob] = relationship(back_populates="items")


class IntakeSession(Base):
    """Task-scoped manual/AI intake state, separate from the formal library."""

    __tablename__ = "intake_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # manual | ai
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)
    user_instruction: Mapped[str] = mapped_column(Text, default="", nullable=False)
    draft_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IntakeAsset(Base):
    """Immutable source image owned by an intake session."""

    __tablename__ = "intake_assets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("intake_sessions.id"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), default="original", nullable=False)
    original_name: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IntakeCandidateRecord(Base):
    """AI/new-problem candidate that does not exist in the formal library yet."""

    __tablename__ = "intake_candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("intake_sessions.id"), nullable=False, index=True
    )
    intake_asset_id: Mapped[str] = mapped_column(
        ForeignKey("intake_assets.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    fields_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    uncertain_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    region_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    problem_id: Mapped[str | None] = mapped_column(
        ForeignKey("problems.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReviewSession(Base):
    __tablename__ = "review_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # ai / workspace / sync
    job_id: Mapped[str | None] = mapped_column(ForeignKey("ai_jobs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list[ReviewItem]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("review_sessions.id"), nullable=False, index=True
    )
    problem_id: Mapped[str] = mapped_column(ForeignKey("problems.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    before_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    proposed_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    uncertain_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # Normalized image coordinates: {"x": 0..1, "y": 0..1, "width": 0..1, "height": 0..1}.
    # An empty object means the candidate uses the whole original image.
    region_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    applied_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[ReviewSession] = relationship(back_populates="items")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    detail_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SyncOperation(Base):
    """本地增量 Operation 日志（阶段 J）。"""

    __tablename__ = "sync_operations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # = operation_id
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    new_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    origin: Mapped[str] = mapped_column(String(32), default="local", nullable=False)  # local|remote


class ProblemOrigin(Base):
    """分享包导入溯源（阶段 K）：用于 origin 去重。"""

    __tablename__ = "problem_origins"
    __table_args__ = (
        UniqueConstraint(
            "origin_package_id",
            "origin_problem_id",
            name="uq_origin_package_problem",
        ),
    )

    problem_id: Mapped[str] = mapped_column(
        ForeignKey("problems.id"), primary_key=True
    )
    origin_package_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    origin_problem_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    imported_from: Mapped[str] = mapped_column(
        String(64), default="shared-package", nullable=False
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

