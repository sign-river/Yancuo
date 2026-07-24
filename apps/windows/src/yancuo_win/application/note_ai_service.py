"""AI extraction and confirmation boundary for notes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from yancuo_win.ai.base import normalize_region
from yancuo_win.ai.factory import get_provider
from yancuo_win.application.bootstrap import RuntimeContext
from yancuo_win.application.note_service import NoteService
from yancuo_win.assets.object_store import ObjectStore
from yancuo_win.data.ids import new_id
from yancuo_win.data.models import NoteAsset, NoteBlock, NoteDocument
from yancuo_win.domain.rules import DomainError


@dataclass(frozen=True)
class NoteBlockDraft:
    block_type: str
    content_markdown: str = ""
    content_latex: str = ""
    source_region: dict[str, float] = field(default_factory=dict)
    uncertain_fields: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class NoteExtractionDraft:
    source_path: Path
    title: str
    summary: str
    blocks: list[NoteBlockDraft]
    subject_suggestion: str = ""
    chapter_suggestion: str = ""
    tags: list[str] = field(default_factory=list)
    uncertain_fields: list[dict[str, str]] = field(default_factory=list)
    model: str = ""
    cost_estimate: float = 0.0


class NoteAiService:
    def __init__(self, runtime: RuntimeContext) -> None:
        self.runtime = runtime
        self.store = ObjectStore(runtime.paths.asset_objects_dir)
        self.notes = NoteService(runtime)

    def extract_from_image(
        self, image_path: Path, *, instruction: str = ""
    ) -> NoteExtractionDraft:
        image_path = Path(image_path)
        if not image_path.is_file():
            raise DomainError(f"图片不存在：{image_path}")
        if not self.runtime.settings.ai.enabled:
            raise DomainError("AI 功能未启用（config [ai].enabled）")
        if not self.runtime.settings.privacy.send_original_images_to_ai:
            raise DomainError("隐私设置禁止向 AI 发送原图")
        provider = get_provider(self.runtime.settings)
        provider.validate_configuration()
        prompt = self._prompt(instruction)
        result = provider.structure_from_image(
            image_path=str(image_path),
            prompt=prompt,
            model=self.runtime.settings.ai.default_vision_model or "mock-v1",
            timeout_seconds=self.runtime.settings.ai.request_timeout_seconds,
        )
        candidate = result.candidate_results()[0]
        return self._normalize_draft(image_path, candidate.fields, candidate.uncertain_fields, result)

    def commit_draft(
        self,
        draft: NoteExtractionDraft,
        *,
        title: str | None = None,
        summary: str | None = None,
        subject_id: str | None = None,
        chapter_id: str | None = None,
    ) -> NoteDocument:
        stored = self.store.store_copy(draft.source_path, role="original")
        with self.runtime.session_factory() as session:
            note = NoteDocument(
                id=new_id("note"),
                title=(title if title is not None else draft.title).strip()[:256],
                summary=summary if summary is not None else draft.summary,
                subject_id=subject_id,
                chapter_id=chapter_id,
                status="active",
            )
            session.add(note)
            session.flush()
            session.add(
                NoteAsset(
                    id=new_id("nasset"),
                    note_document_id=note.id,
                    role="original",
                    relative_path=stored.relative_path,
                    sha256=stored.sha256,
                    mime_type=stored.mime_type,
                    size_bytes=stored.size_bytes,
                    is_immutable=True,
                )
            )
            for order, block in enumerate(draft.blocks):
                session.add(
                    NoteBlock(
                        id=new_id("nblock"),
                        note_document_id=note.id,
                        sort_order=order,
                        block_type=block.block_type,
                        content_markdown=block.content_markdown,
                        content_latex=block.content_latex,
                        source_region_json=json.dumps(
                            block.source_region,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        uncertain_json=json.dumps(
                            block.uncertain_fields,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                )
            session.commit()
            note_id = note.id
        committed = self.notes.get_note(note_id)
        if committed is None:
            raise DomainError("笔记入库后无法重新读取")
        return committed

    @staticmethod
    def _prompt(instruction: str) -> str:
        extra = f"\n用户补充要求：{instruction.strip()}" if instruction.strip() else ""
        return (
            "你是学习笔记整理助手。请从图片中提取印刷或手写的知识笔记，"
            "只返回 JSON，不要 Markdown 代码围栏。格式为："
            '{"title":"", "summary":"", "subject_suggestion":"", '
            '"chapter_suggestion":"", "tags":[], "blocks":['
            '{"type":"heading|text|concept|formula|callout", "markdown":"", '
            '"latex":"", "region":{"x":0.0,"y":0.0,"width":1.0,"height":1.0},'
            '"uncertain_fields":[]}], "uncertain_fields":[]}. '
            "一个独立公式使用 formula 块，独立知识点使用 concept 块，普通说明使用 text 块，"
            "重点提醒使用 callout 块；每个内容块尽量返回其在原图中的归一化 region；"
            "无法确认的字词放入对应 uncertain_fields。不要输出题目答案结构。"
            + extra
        )

    @staticmethod
    def _normalize_draft(
        source_path: Path,
        fields: dict[str, Any],
        uncertain: list[dict[str, str]],
        result: Any,
    ) -> NoteExtractionDraft:
        raw_blocks = fields.get("blocks")
        blocks: list[NoteBlockDraft] = []
        if isinstance(raw_blocks, list):
            for raw in raw_blocks:
                if not isinstance(raw, dict):
                    continue
                block_type = str(raw.get("type") or raw.get("block_type") or "text")
                if block_type not in {
                    "heading",
                    "text",
                    "concept",
                    "formula",
                    "callout",
                }:
                    block_type = "text"
                blocks.append(
                    NoteBlockDraft(
                        block_type=block_type,
                        content_markdown=str(raw.get("markdown") or raw.get("content_markdown") or ""),
                        content_latex=str(raw.get("latex") or raw.get("content_latex") or ""),
                        source_region=normalize_region(
                            raw.get("region") or raw.get("source_region")
                        ),
                        uncertain_fields=[item for item in raw.get("uncertain_fields", []) if isinstance(item, dict)],
                    )
                )
        # Mock and older providers return the problem-shaped fields. Keep a safe
        # fallback so development mode still demonstrates the note workflow.
        if not blocks:
            title = str(fields.get("title") or source_path.stem)
            text = str(fields.get("notes") or fields.get("question_markdown") or "")
            latex = str(fields.get("question_latex") or "")
            blocks = [NoteBlockDraft("heading", content_markdown=title)]
            if text:
                blocks.append(NoteBlockDraft("text", content_markdown=text))
            if latex:
                blocks.append(NoteBlockDraft("formula", content_latex=latex))
        return NoteExtractionDraft(
            source_path=source_path,
            title=str(fields.get("title") or source_path.stem),
            summary=str(fields.get("summary") or fields.get("notes") or ""),
            blocks=blocks,
            subject_suggestion=str(fields.get("subject_suggestion") or fields.get("subject_name") or ""),
            chapter_suggestion=str(fields.get("chapter_suggestion") or fields.get("chapter_name") or ""),
            tags=[str(item) for item in fields.get("tags", []) if str(item).strip()][:20],
            uncertain_fields=uncertain,
            model=str(result.model or ""),
            cost_estimate=float(result.cost_estimate or 0),
        )
