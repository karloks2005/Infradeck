import threading

import pytest

from src.agent import chat as agent_service
from src.agent.chat import AgentService
from tests.fakes import ScriptedProvider


def make_service():
    service = AgentService()
    service.lock = threading.Lock()
    return service


def test_without_a_tool_budget_the_chat_is_one_call_without_tools():
    provider = ScriptedProvider('{"message":"All quiet."}')

    assert make_service().send(1, "conversation", "Anything wrong?", provider, budget=0) == agent_service.Answer("All quiet.", [])
    assert provider.started == [{"tools": [], "effort": "low", "schema_name": "agent_reply"}] and len(provider.calls) == 1


def test_secrets_in_the_question_and_incident_context_never_reach_the_provider():
    provider = ScriptedProvider('{"message":"Looking."}', '{"message":"Still looking."}')
    context = ("[INCIDENT INC-0001 on web1, warning, active]\nContainer app exited\n"
               "- GET https://admin:hunter2@db.internal/ failed, api_key=ABCDEF123456\n[QUESTION]\n")
    service = make_service()

    service.send(1, "conversation", context + "My token is ghp_" + "a" * 30, provider)
    service.send(1, "conversation", "And now?", provider)

    for sent in provider.calls:   # the first turn, and the history sent again with the second
        text = " ".join(message["content"] for message in sent if message["role"] == "user")
        assert "hunter2" not in text and "ABCDEF123456" not in text and "ghp_" + "a" * 30 not in text
    first = provider.calls[0][-1]["content"]
    assert "[masked]" in first and "Container app exited" in first and "INC-0001" in first   # the rest stays readable


def test_a_reply_without_a_message_is_an_error():
    with pytest.raises(ValueError, match="must contain a message"):
        make_service().send(1, "conversation", "Hello", ScriptedProvider('{"message":"  "}'))


def test_test_connection_tries_one_request_shaped_like_a_diagnosis_and_counts_it(monkeypatch):
    # A call the model makes anyway is not run: the result comes from the first step only.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.agent import diagnosis, tools
    from src.backend.models import AgentRun, Base
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    provider = ScriptedProvider('{"summary":"x"}', tokens=(3100, 40))
    provider.check = lambda: {"provider": "Anthropic", "model": "claude-test"}
    monkeypatch.setattr(agent_service, "get_provider", lambda db: provider)

    result = agent_service.check_provider_connection(db, user_id=1)

    assert result == {"provider": "Anthropic", "model": "claude-test", "tokens": 3140}
    assert provider.started[0]["tools"] == [tool.name for tool in tools.TOOLS] and provider.started[0]["schema_name"] == "incident_diagnosis"
    assert provider.allowed == [True]   # exactly as a diagnosis sends it: Anthropic only checks the tools' grammar then (#199)
    run = db.query(AgentRun).one()
    assert (run.kind, run.status, run.input_tokens, run.user_id) == ("test", "ok", 3100, 1)

    refused = ScriptedProvider(ValueError("Schemas contains too many parameters with union types"))
    refused.check = provider.check
    monkeypatch.setattr(agent_service, "get_provider", lambda db: refused)
    with pytest.raises(ValueError, match="union types"):
        agent_service.check_provider_connection(db)
    assert [run.status for run in db.query(AgentRun).order_by(AgentRun.id)] == ["ok", "error"]
    assert diagnosis.SCHEMA   # the same schema a diagnosis uses


def test_the_reply_can_only_be_a_message():
    assert agent_service.REPLY_SCHEMA["properties"].keys() == {"message"} and agent_service.REPLY_SCHEMA["additionalProperties"] is False


# --- the Chat Completions provider (Gemini's OpenAI-compatible endpoint) ----------------------------------------

from types import SimpleNamespace

from src.agent.providers import Completion, OpenAIProvider


class FakeCompletions:
    def __init__(self, content='{"message":"All good."}', refusal=None, fail=None):
        self.calls = []
        self.content, self.refusal, self.fail = content, refusal, fail

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        message = SimpleNamespace(content=self.content, refusal=self.refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], model="gpt-test", usage=SimpleNamespace(prompt_tokens=120, completion_tokens=8))


def fake_client(completions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions), models=SimpleNamespace(retrieve=lambda model: SimpleNamespace(id=model)))


def test_the_chat_completions_provider_asks_for_the_reply_schema_and_reports_tokens():
    completions = FakeCompletions()
    provider = OpenAIProvider("sk-test", "gpt-test", client=fake_client(completions))

    result = provider.complete([{"role": "user", "content": "hi"}], agent_service.REPLY_SCHEMA, "agent_reply")

    assert result == Completion(text='{"message":"All good."}', model="gpt-test", input_tokens=120, output_tokens=8)
    format_ = completions.calls[0]["response_format"]
    assert format_["type"] == "json_schema" and format_["json_schema"]["strict"] is True and format_["json_schema"]["schema"] == agent_service.REPLY_SCHEMA


def test_a_refusal_is_an_error_not_an_empty_reply():
    provider = OpenAIProvider("sk-test", "gpt-test", client=fake_client(FakeCompletions(content=None, refusal="I can't help with that.")))

    with pytest.raises(ValueError, match="refused"):
        provider.complete([], agent_service.REPLY_SCHEMA, "agent_reply")


def test_test_connection_spends_no_tokens():
    completions = FakeCompletions()
    provider = OpenAIProvider("sk-test", "gpt-test", client=fake_client(completions))

    assert provider.check() == {"provider": "OpenAI", "model": "gpt-test"}
    assert completions.calls == []


def test_a_conversation_sends_the_system_prompt_and_its_history():
    completions = FakeCompletions()
    provider, chat = OpenAIProvider("sk-test", "gpt-test", client=fake_client(completions)), agent_service.Chat()

    chat.send_message(provider, "first")
    chat.send_message(provider, "second")

    sent = completions.calls[-1]["messages"]
    assert sent[0]["role"] == "system" and [message["content"] for message in sent[1:] if message["role"] == "user"] == ["first", "second"]
    assert [message["role"] for message in sent[1:]] == ["user", "assistant", "user"]


def test_only_recent_turns_go_back_to_the_model(monkeypatch):
    monkeypatch.setattr(agent_service, "MAX_HISTORY_MESSAGES", 4)
    completions = FakeCompletions()
    provider, chat = OpenAIProvider("sk-test", "gpt-test", client=fake_client(completions)), agent_service.Chat()

    for number in range(6):
        chat.send_message(provider, f"message {number}")

    assert len(completions.calls[-1]["messages"]) == 1 + 4


def test_a_failed_turn_is_not_kept_in_the_conversation():
    completions = FakeCompletions(fail=TimeoutError("timed out"))
    provider, chat = OpenAIProvider("sk-test", "gpt-test", client=fake_client(completions)), agent_service.Chat()

    with pytest.raises(TimeoutError):
        chat.send_message(provider, "hello")

    assert chat.history == []
