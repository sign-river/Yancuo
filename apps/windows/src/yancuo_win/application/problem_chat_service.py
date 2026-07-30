"""Durable, problem-scoped AI discussions."""

from __future__ import annotations

import json
import base64
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from yancuo_win.ai.factory import get_provider
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.data.ids import new_id
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.models import Asset, Problem, ProblemConversation, ProblemMessage, utcnow
from yancuo_win.domain.rules import DomainError


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
            rows = list(session.scalars(
                select(ProblemConversation)
                .where(ProblemConversation.problem_id == problem_id)
                .order_by(ProblemConversation.updated_at.desc())
            ).all())
            session.expunge_all()
            return rows

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

    def send_message(self, conversation_id: str, content: str) -> ProblemMessage:
        content = content.strip()
        if not content:
            raise DomainError("请输入要讨论的问题")
        with self._session() as session:
            conversation = session.get(ProblemConversation, conversation_id)
            if conversation is None:
                raise DomainError("对话不存在")
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
            ):
                user_message = latest
                user_message.status = "pending"
                user_message.error_message = ""
            else:
                sequence = int(
                    session.scalar(
                        select(func.max(ProblemMessage.sequence)).where(
                            ProblemMessage.conversation_id == conversation_id
                        )
                    )
                    or 0
                ) + 1
                user_message = ProblemMessage(
                    id=new_id("message"),
                    conversation_id=conversation_id,
                    sequence=sequence,
                    role="user",
                    content_markdown=content,
                    status="pending",
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
                id=new_id("message"), conversation_id=conversation_id, sequence=next_sequence,
                role="assistant", content_markdown=response.content_markdown, status="complete",
                prompt_tokens=response.prompt_tokens, completion_tokens=response.completion_tokens,
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
            include = (
                message.role == "user" and message.status in {"complete", "pending"}
            ) or (message.role == "assistant" and message.status == "complete")
            if include:
                messages.append({"role": message.role, "content": message.content_markdown})
        if conversation.include_original_image and provider.capabilities.supports_chat_images:
            image_content = self._original_image_context(conversation.problem_id)
            if image_content:
                messages.append({"role": "user", "content": image_content})
        return provider.complete_chat(
            messages=messages,
            model=conversation.model or self.runtime.settings.ai.default_text_model,
            timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
        )

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
            sources = [
                (asset.relative_path, asset.mime_type or "image/jpeg")
                for asset in assets
            ]
        store = ObjectStore(self.runtime.paths.asset_objects_dir)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "用户已明确授权附带当前题目的原图。"},
        ]
        for relative_path, mime_type in sources:
            path = store.resolve(relative_path)
            if not path.is_file() or path.stat().st_size == 0:
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
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
