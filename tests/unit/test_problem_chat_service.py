"""Problem-scoped conversations persist locally and remain tied to a revision."""

from __future__ import annotations

from pathlib import Path

import pytest

from yancuo_win.application.bootstrap import bootstrap_runtime
from yancuo_win.application.problem_chat_service import ProblemChatService
from yancuo_win.application.services import AppServices
from yancuo_win.config.settings import default_toml_path


@pytest.fixture()
def chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AppServices, ProblemChatService]:
    monkeypatch.setenv("YANCUO_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("YANCUO_CONFIG_FILE", str(default_toml_path()))
    runtime = bootstrap_runtime()
    runtime.settings.ai.default_provider = "mock"
    runtime.settings.ai.default_text_model = "mock-chat"
    return AppServices(runtime), ProblemChatService(runtime)


def test_problem_chat_persists_snapshot_messages_and_export(
    chat: tuple[AppServices, ProblemChatService], tmp_path: Path
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(
        title="积分题", status="active", question_markdown="计算 $\\int_0^1 x dx$"
    )
    conversation = problem_chat.create_conversation(problem.id, title="积分讨论")
    assert conversation.problem_revision == problem.revision
    reply = problem_chat.send_message(conversation.id, "为什么答案是 1/2？")
    assert reply.role == "assistant"
    loaded = problem_chat.get_conversation(conversation.id)
    assert loaded is not None
    assert [message.role for message in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[0].status == "complete"
    problem_chat.save_conversation(conversation.id)
    assert problem_chat.get_conversation(conversation.id).status == "saved"  # type: ignore[union-attr]
    export = problem_chat.export_conversation_markdown(conversation.id, tmp_path / "chat.md")
    assert "积分讨论" in export.read_text(encoding="utf-8")


def test_failed_chat_keeps_pending_user_message_as_failed(
    chat: tuple[AppServices, ProblemChatService]
) -> None:
    services, problem_chat = chat
    problem = services.create_problem(title="失败题", status="active")
    conversation = problem_chat.create_conversation(problem.id)
    with problem_chat._session() as session:
        row = session.get(type(conversation), conversation.id)
        row.provider = "unknown"
        session.commit()
    reply = problem_chat.send_message(conversation.id, "测试失败持久化")
    assert reply.role == "user"
    assert reply.status == "failed"
    assert reply.error_message
