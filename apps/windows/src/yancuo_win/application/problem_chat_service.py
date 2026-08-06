"""Durable, problem-scoped AI discussions."""

from __future__ import annotations

import json
import base64
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImageReader

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from yancuo_win.ai.factory import get_provider
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.data.ids import new_id
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.models import Asset, Problem, ProblemConversation, ProblemMessage, utcnow
from yancuo_win.domain.rules import DomainError
from yancuo_win.infrastructure.atomic_file import atomic_text_writer


_MAX_CHAT_REFERENCE_COUNT = 20
_MAX_CHAT_REFERENCE_SOURCE_PIXELS = 25_000_000
_MAX_CHAT_REFERENCE_TOTAL_BYTES = 32 * 1024 * 1024
RENDER_PAGE_ROLE = "render_page"


@dataclass(frozen=True)
class ProblemReference:
    """A stable, normalized visual excerpt from an immutable derived figure."""

    asset_id: str
    page_index: int
    x: float
    y: float
    width: float
    height: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "page_index": self.page_index,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_value(cls, value: ProblemReference | dict[str, Any]) -> ProblemReference:
        if isinstance(value, cls):
            return value
        try:
            reference = cls(
                str(value["asset_id"]),
                int(value["page_index"]),
                float(value["x"]),
                float(value["y"]),
                float(value["width"]),
                float(value["height"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError("引用区域格式无效") from exc
        if (
            not reference.asset_id
            or not 0 <= reference.x < 1
            or not 0 <= reference.y < 1
            or not 0 < reference.width <= 1 - reference.x
            or not 0 < reference.height <= 1 - reference.y
        ):
            raise DomainError("引用区域坐标无效")
        return reference


class ProblemChatService:
    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime

    def _session(self):
        return self.runtime.session_factory()

    @staticmethod
    def _snapshot(problem: Problem) -> dict[str, Any]:
        return {
            "title": problem.title or "",
            "question_markdown": problem.question_markdown,
            "question_latex": problem.question_latex,
            "question_content_json": problem.question_content_json,
            "correct_answer": problem.correct_answer,
            "solution_markdown": problem.solution_markdown,
            "error_analysis": problem.error_analysis,
            "notes": problem.notes,
            "subject_id": problem.subject_id,
            "chapter_id": problem.chapter_id,
            "tags": [tag.name for tag in problem.tags],
        }

    def create_conversation(
        self, problem_id: str, *, title: str = "新对话", include_original_image: bool = False
    ) -> ProblemConversation:
        provider_name = self.runtime.settings.ai.default_provider
        model = self.runtime.settings.ai.default_text_model
        with self._session() as session:
            problem = session.scalar(
                select(Problem).where(Problem.id == problem_id).options(selectinload(Problem.tags))
            )
            if problem is None:
                raise DomainError("题目不存在")
            conversation = ProblemConversation(
                id=new_id("conversation"),
                problem_id=problem.id,
                title=title.strip() or "新对话",
                provider=provider_name,
                model=model,
                problem_revision=problem.revision,
                context_snapshot_json=json.dumps(self._snapshot(problem), ensure_ascii=False),
                # Legacy column retained for schema compatibility. Formal
                # conversations never receive discarded source originals.
                include_original_image=False,
            )
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            session.expunge(conversation)
            return conversation

    def list_conversations(self, problem_id: str) -> list[ProblemConversation]:
        with self._session() as session:
            rows = list(
                session.scalars(
                    select(ProblemConversation)
                    .where(ProblemConversation.problem_id == problem_id)
                    .order_by(ProblemConversation.updated_at.desc())
                ).all()
            )
            session.expunge_all()
            return rows

    def list_reference_sources(self, problem_id: str) -> list[dict[str, Any]]:
        """Return rendered PDF page sources, falling back to legacy figure blocks."""

        store = ObjectStore(self.runtime.paths.asset_objects_dir)
        with self._session() as session:
            problem = session.get(Problem, problem_id)
            if problem is None:
                return []
            render_pages = list(
                session.scalars(
                    select(Asset)
                    .where(
                        Asset.problem_id == problem_id,
                        Asset.role == RENDER_PAGE_ROLE,
                    )
                    .order_by(Asset.created_at, Asset.id)
                )
            )
            if render_pages:
                return [
                    {
                        "asset_id": asset.id,
                        "page_index": index,
                        "path": store.resolve(asset.relative_path),
                    }
                    for index, asset in enumerate(render_pages)
                    if store.resolve(asset.relative_path).is_file()
                ]
            try:
                blocks = json.loads(problem.question_content_json or "[]")
            except json.JSONDecodeError:
                blocks = []
            ordered_ids = list(
                dict.fromkeys(
                    str(block.get("derived_asset_id"))
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type") == "figure"
                    and block.get("derived_asset_id")
                )
            )
            assets = {
                asset.id: asset.relative_path
                for asset in session.scalars(
                    select(Asset).where(
                        Asset.problem_id == problem_id,
                        Asset.role == "derived_figure",
                        Asset.id.in_(ordered_ids),
                    )
                )
            }
            sources = [
                (asset_id, assets[asset_id])
                for asset_id in ordered_ids
                if asset_id in assets
            ]
            return [
                {
                    "asset_id": asset_id,
                    "page_index": index,
                    "path": store.resolve(relative_path),
                }
                for index, (asset_id, relative_path) in enumerate(sources)
                if store.resolve(relative_path).is_file()
            ]


    def ensure_render_sources(
        self, problem_id: str, pages: Sequence[bytes]
    ) -> list[dict[str, Any]]:
        """Persist rendered PDF pages as immutable render_page sources.

        Pages are stored content-addressed and reused when unchanged; stale
        pages from an older render are removed so page order follows the PDF.
        """
        if not pages:
            return []
        store = ObjectStore(self.runtime.paths.asset_objects_dir)
        cache_dir = self.runtime.paths.cache_dir / "render_pages"
        cache_dir.mkdir(parents=True, exist_ok=True)
        with self._session() as session:
            problem = session.get(Problem, problem_id)
            if problem is None:
                return []
            existing = {
                asset.sha256: asset
                for asset in session.scalars(
                    select(Asset).where(
                        Asset.problem_id == problem_id,
                        Asset.role == RENDER_PAGE_ROLE,
                    )
                )
            }
            ordered: list[Asset] = []
            changed = False
            for data in pages:
                if not data:
                    continue
                tmp_path = cache_dir / f"render-{uuid.uuid4().hex}.png"
                try:
                    tmp_path.write_bytes(data)
                    stored = store.store_copy(tmp_path, role=RENDER_PAGE_ROLE)
                finally:
                    tmp_path.unlink(missing_ok=True)
                asset = existing.get(stored.sha256)
                if asset is None:
                    asset = Asset(
                        id=new_id("asset"),
                        problem_id=problem_id,
                        role=RENDER_PAGE_ROLE,
                        sha256=stored.sha256,
                        relative_path=stored.relative_path,
                        mime_type="image/png",
                        size_bytes=stored.size_bytes,
                    )
                    session.add(asset)
                    changed = True
                ordered.append(asset)
            if changed:
                keep_ids = {asset.id for asset in ordered}
                for stale in session.scalars(
                    select(Asset).where(
                        Asset.problem_id == problem_id,
                        Asset.role == RENDER_PAGE_ROLE,
                    )
                ):
                    if stale.id not in keep_ids:
                        session.delete(stale)
                session.commit()
            sources = [
                {
                    "asset_id": asset.id,
                    "page_index": index,
                    "path": store.resolve(asset.relative_path),
                }
                for index, asset in enumerate(ordered)
                if store.resolve(asset.relative_path).is_file()
            ]
            session.expunge_all()
        return sources


    def get_conversation(self, conversation_id: str) -> ProblemConversation | None:
        with self._session() as session:
            row = session.scalar(
                select(ProblemConversation)
                .where(ProblemConversation.id == conversation_id)
                .options(selectinload(ProblemConversation.messages))
            )
            if row is not None:
                session.expunge(row)
            return row

    def rename_conversation(self, conversation_id: str, title: str) -> None:
        with self._session() as session:
            conversation = session.get(ProblemConversation, conversation_id)
            if conversation is None:
                raise DomainError("对话不存在")
            conversation.title = title.strip() or "未命名对话"
            conversation.updated_at = utcnow()
            session.commit()

    def save_conversation(self, conversation_id: str) -> None:
        with self._session() as session:
            conversation = session.get(ProblemConversation, conversation_id)
            if conversation is None:
                raise DomainError("对话不存在")
            conversation.status = "saved"
            conversation.updated_at = utcnow()
            session.commit()

    def delete_conversation(self, conversation_id: str) -> None:
        with self._session() as session:
            conversation = session.get(ProblemConversation, conversation_id)
            if conversation is None:
                raise DomainError("对话不存在")
            session.delete(conversation)
            session.commit()

    def send_message(
        self,
        conversation_id: str,
        content: str,
        references: Sequence[ProblemReference | dict[str, Any]] = (),
        on_text_delta: Callable[[str], None] | None = None,
    ) -> ProblemMessage:
        content = content.strip()
        if not content:
            raise DomainError("请输入要讨论的问题")
        if len(references) > _MAX_CHAT_REFERENCE_COUNT:
            raise DomainError(f"单条消息最多包含 {_MAX_CHAT_REFERENCE_COUNT} 个视觉引用")
        parsed_references = [ProblemReference.from_value(value) for value in references]
        with self._session() as lookup_session:
            lookup_conversation = lookup_session.get(
                ProblemConversation, conversation_id
            )
            if lookup_conversation is None:
                raise DomainError("对话不存在")
            reference_problem_id = lookup_conversation.problem_id
        source_pages = {
            str(source["asset_id"]): int(source["page_index"])
            for source in self.list_reference_sources(reference_problem_id)
        }
        with self._session() as session:
            conversation = session.get(ProblemConversation, conversation_id)
            if conversation is None:
                raise DomainError("对话不存在")
            for reference in parsed_references:
                if source_pages.get(reference.asset_id) != reference.page_index:
                    raise DomainError("引用区域不属于当前题目或页码已失效")
            reference_json = json.dumps(
                [reference.as_dict() for reference in parsed_references],
                ensure_ascii=False,
            )
            latest = session.scalar(
                select(ProblemMessage)
                .where(ProblemMessage.conversation_id == conversation_id)
                .order_by(ProblemMessage.sequence.desc())
                .limit(1)
            )
            if (
                latest is not None
                and latest.role == "user"
                and latest.status in {"pending", "failed"}
                and latest.content_markdown == content
                and latest.reference_snapshot_json == reference_json
            ):
                # pending 表示上一次请求因程序中断/取消未完成，
                # 重新请求时复用该消息，避免重复发送同一内容。
                user_message = latest
                user_message.status = "pending"
                user_message.error_message = ""
            else:
                sequence = (
                    int(
                        session.scalar(
                            select(func.max(ProblemMessage.sequence)).where(
                                ProblemMessage.conversation_id == conversation_id
                            )
                        )
                        or 0
                    )
                    + 1
                )
                user_message = ProblemMessage(
                    id=new_id("message"),
                    conversation_id=conversation_id,
                    sequence=sequence,
                    role="user",
                    content_markdown=content,
                    status="pending",
                    reference_snapshot_json=reference_json,
                )
                session.add(user_message)
            user_message_id = user_message.id
            conversation.updated_at = utcnow()
            session.commit()

        try:
            response = self._request_reply(
                conversation_id, on_text_delta=on_text_delta
            )
        except Exception as exc:
            with self._session() as session:
                failed = session.get(ProblemMessage, user_message_id)
                if failed is not None:
                    failed.status = "failed"
                    failed.error_message = str(exc)[:500]
                    session.commit()
                    session.refresh(failed)
                    session.expunge(failed)
                    return failed
            raise

        with self._session() as session:
            pending = session.get(ProblemMessage, user_message_id)
            conversation = session.get(ProblemConversation, conversation_id)
            if pending is None or conversation is None:
                raise DomainError("对话在生成过程中被删除")
            pending.status = "complete"
            next_sequence = pending.sequence + 1
            assistant = ProblemMessage(
                id=new_id("message"),
                conversation_id=conversation_id,
                sequence=next_sequence,
                role="assistant",
                content_markdown=response.content_markdown,
                status="complete",
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cost_estimate=response.cost_estimate,
            )
            session.add(assistant)
            conversation.model = response.model or conversation.model
            conversation.updated_at = utcnow()
            session.commit()
            session.refresh(assistant)
            session.expunge(assistant)
            return assistant

    def _request_reply(
        self, conversation_id: str, on_text_delta: Callable[[str], None] | None = None
    ):
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise DomainError("对话不存在")
        provider = get_provider(self.runtime.settings, conversation.provider)
        if not provider.capabilities.supports_chat:
            raise DomainError("当前 AI 提供商不支持题目对话")
        try:
            snapshot = json.loads(conversation.context_snapshot_json)
        except json.JSONDecodeError:
            snapshot = {}
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": "你是错题学习助手。只围绕下列固定题目上下文解释，使用 Markdown 和 LaTex。\n"
                + json.dumps(snapshot, ensure_ascii=False),
            }
        ]
        for message in conversation.messages:
            include = (message.role == "user" and message.status in {"complete", "pending"}) or (
                message.role == "assistant" and message.status == "complete"
            )
            if include:
                messages.append(
                    {
                        "role": message.role,
                        "content": self._message_content(
                            conversation.problem_id,
                            message,
                            provider.capabilities.supports_chat_images,
                        ),
                    }
                )
        return provider.complete_chat(
            messages=messages,
            model=conversation.model or self.runtime.settings.ai.default_text_model,
            timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
            on_text_delta=on_text_delta,
        )

    def _message_content(
        self, problem_id: str, message: ProblemMessage, include_images: bool
    ) -> str | list[dict[str, Any]]:
        if message.role != "user":
            return message.content_markdown
        try:
            references = json.loads(message.reference_snapshot_json or "[]")
        except json.JSONDecodeError:
            references = []
        if not references:
            return message.content_markdown
        if not include_images:
            return f"{message.content_markdown}\n\n用户选择了 {len(references)} 个视觉引用，但当前提供商不支持图片对话。"
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": f"{message.content_markdown}\n\n用户明确框选了以下 {len(references)} 个视觉引用；请优先回答选区，同时结合完整题目上下文。",
            }
        ]
        content.extend(self._reference_image_context(problem_id, references))
        return content

    def _reference_image_context(
        self, problem_id: str, references: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Rebuild ordered crops from immutable formal figure assets."""

        if len(references) > _MAX_CHAT_REFERENCE_COUNT:
            raise DomainError(f"单条消息最多包含 {_MAX_CHAT_REFERENCE_COUNT} 个视觉引用")

        with self._session() as session:
            sources = {
                asset.id: asset.relative_path
                for asset in session.scalars(
                    select(Asset).where(
                        Asset.problem_id == problem_id,
                        Asset.role.in_(["derived_figure", RENDER_PAGE_ROLE]),
                    )
                )
            }
        store = ObjectStore(self.runtime.paths.asset_objects_dir)
        content: list[dict[str, Any]] = []
        total_bytes = 0
        for index, value in enumerate(references, start=1):
            reference = ProblemReference.from_value(value)
            source = sources.get(reference.asset_id)
            if source is None:
                continue
            reader = QImageReader(str(store.resolve(source)))
            image_size = reader.size()
            if not image_size.isValid():
                continue
            if image_size.width() * image_size.height() > _MAX_CHAT_REFERENCE_SOURCE_PIXELS:
                raise DomainError("视觉引用题图解码像素超过安全上限")
            image = reader.read()
            if image.isNull():
                continue
            x, y = round(image.width() * reference.x), round(image.height() * reference.y)
            width, height = (
                max(1, round(image.width() * reference.width)),
                max(1, round(image.height() * reference.height)),
            )
            crop = image.copy(x, y, min(width, image.width() - x), min(height, image.height() - y))
            encoded = QByteArray()
            buffer = QBuffer(encoded)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            saved = crop.save(buffer, "PNG")
            buffer.close()
            if not saved:
                continue
            total_bytes += encoded.size()
            if total_bytes > _MAX_CHAT_REFERENCE_TOTAL_BYTES:
                raise DomainError("单条消息的视觉引用总大小不能超过 32 MiB")
            content.extend(
                [
                    {
                        "type": "text",
                        "text": f"引用区域 {index}（第 {reference.page_index + 1} 页）",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64.b64encode(bytes(encoded)).decode('ascii')}"
                        },
                    },
                ]
            )
        return content

    def export_conversation_markdown(self, conversation_id: str, dest: Path) -> Path:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise DomainError("对话不存在")
        parts = [f"# {conversation.title}", ""]
        for message in conversation.messages:
            role = "我" if message.role == "user" else "AI"
            parts.extend([f"## {role}", message.content_markdown, ""])
        with atomic_text_writer(dest) as stream:
            stream.write("\n".join(parts))
        return dest
