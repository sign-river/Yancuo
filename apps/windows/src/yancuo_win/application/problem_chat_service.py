"""Durable, problem-scoped AI discussions."""

from __future__ import annotations

import json
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from yancuo_win.ai.factory import get_provider
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.data.ids import new_id
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.models import Asset, Problem, ProblemConversation, ProblemMessage, utcnow
from yancuo_win.domain.rules import DomainError


_MAX_CHAT_IMAGE_COUNT = 20
_MAX_CHAT_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_CHAT_IMAGE_TOTAL_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ProblemReference:
    """A stable, normalized visual excerpt from an immutable original asset."""

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
                include_original_image=include_original_image,
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
        """Return immutable originals in the same stable order used for consented sends."""

        with self._session() as session:
            assets = list(
                session.scalars(
                    select(Asset)
                    .where(Asset.problem_id == problem_id, Asset.role == "original")
                    .order_by(Asset.created_at.asc(), Asset.id.asc())
                )
            )
            sources = [(asset.id, asset.relative_path) for asset in assets]
        store = ObjectStore(self.runtime.paths.asset_objects_dir)
        return [
            {"asset_id": asset_id, "page_index": index, "path": store.resolve(relative_path)}
            for index, (asset_id, relative_path) in enumerate(sources)
            if store.resolve(relative_path).is_file()
        ]

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
    ) -> ProblemMessage:
        content = content.strip()
        if not content:
            raise DomainError("请输入要讨论的问题")
        parsed_references = [ProblemReference.from_value(value) for value in references]
        with self._session() as session:
            conversation = session.get(ProblemConversation, conversation_id)
            if conversation is None:
                raise DomainError("对话不存在")
            originals = list(
                session.scalars(
                    select(Asset)
                    .where(
                        Asset.problem_id == conversation.problem_id,
                        Asset.role == "original",
                    )
                    .order_by(Asset.created_at.asc(), Asset.id.asc())
                )
            )
            source_pages = {asset.id: index for index, asset in enumerate(originals)}
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
                and latest.status == "failed"
                and latest.content_markdown == content
                and latest.reference_snapshot_json == reference_json
            ):
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
            response = self._request_reply(conversation_id)
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

    def _request_reply(self, conversation_id: str):
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
        if conversation.include_original_image and provider.capabilities.supports_chat_images:
            image_content = self._original_image_context(conversation.problem_id)
            if image_content:
                messages.append({"role": "user", "content": image_content})
        return provider.complete_chat(
            messages=messages,
            model=conversation.model or self.runtime.settings.ai.default_text_model,
            timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
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
        """Rebuild ordered crops from immutable originals without sending full images."""

        with self._session() as session:
            sources = {
                asset.id: asset.relative_path
                for asset in session.scalars(
                    select(Asset).where(Asset.problem_id == problem_id, Asset.role == "original")
                )
            }
        store = ObjectStore(self.runtime.paths.asset_objects_dir)
        content: list[dict[str, Any]] = []
        for index, value in enumerate(references, start=1):
            reference = ProblemReference.from_value(value)
            source = sources.get(reference.asset_id)
            if source is None:
                continue
            image = QImage(str(store.resolve(source)))
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
            crop.save(buffer, "PNG")
            buffer.close()
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

    def _original_image_context(self, problem_id: str) -> list[dict[str, Any]]:
        """Read originals in a deterministic order after explicit consent."""

        with self._session() as session:
            assets = list(
                session.scalars(
                    select(Asset)
                    .where(
                        Asset.problem_id == problem_id,
                        Asset.role == "original",
                    )
                    .order_by(Asset.created_at.asc(), Asset.id.asc())
                ).all()
            )
            if not assets:
                return []
            sources = [(asset.relative_path, asset.mime_type or "image/jpeg") for asset in assets]
        if len(sources) > _MAX_CHAT_IMAGE_COUNT:
            raise DomainError(f"题目对话最多附带 {_MAX_CHAT_IMAGE_COUNT} 张原图")
        store = ObjectStore(self.runtime.paths.asset_objects_dir)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "用户已明确授权附带当前题目的原图。"},
        ]
        total_bytes = 0
        for relative_path, mime_type in sources:
            path = store.resolve(relative_path)
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size <= 0 or size > _MAX_CHAT_IMAGE_BYTES:
                raise DomainError("题目对话单张原图必须在 1 字节到 32 MiB 之间")
            total_bytes += size
            if total_bytes > _MAX_CHAT_IMAGE_TOTAL_BYTES:
                raise DomainError("题目对话附带原图总大小不能超过 64 MiB")
            try:
                with path.open("rb") as stream:
                    payload = stream.read(_MAX_CHAT_IMAGE_BYTES + 1)
            except OSError as exc:
                raise DomainError("题目原图读取失败") from exc
            if len(payload) != size or len(payload) > _MAX_CHAT_IMAGE_BYTES:
                raise DomainError("题目原图在读取期间发生变化或超过大小上限")
            encoded = base64.b64encode(payload).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )
        return content if len(content) > 1 else []

    def export_conversation_markdown(self, conversation_id: str, dest: Path) -> Path:
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise DomainError("对话不存在")
        parts = [f"# {conversation.title}", ""]
        for message in conversation.messages:
            role = "我" if message.role == "user" else "AI"
            parts.extend([f"## {role}", message.content_markdown, ""])
        dest.write_text("\n".join(parts), encoding="utf-8")
        return dest
