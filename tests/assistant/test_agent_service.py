import threading

import pytest

from src.agent.chat import AgentService


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeChat:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def send_message(self, provider, message):
        self.messages.append(message)
        return FakeResponse(next(self.responses))


class FakeHealthChecks:
    def __init__(self):
        self.requested = []

    def run(self, check_name):
        self.requested.append(check_name)
        return {"check": check_name, "status": "ok"}


def make_service(fake_chat, checks=None):
    service = AgentService.__new__(AgentService)
    service.chats = {(1, "conversation"): fake_chat}
    service.health_checks = checks or FakeHealthChecks()
    service.lock = threading.Lock()
    return service


def test_predefined_health_check_runs_and_is_audited():
    chat = FakeChat([
        '{"message":"Checking the server health.","action":{"check":"server_health"}}',
        '{"message":"Server health is OK.","action":null}',
    ])
    checks = FakeHealthChecks()
    service = make_service(chat, checks)
    events = []

    result = service.send(1, "conversation", "Check the host.", object(), events.append)

    assert result == "Server health is OK."
    assert checks.requested == ["server_health"]
    assert [event["event_type"] for event in events] == ["health_check_requested", "health_check_completed"]
    assert "HEALTH-CHECK-RESULT" in chat.messages[-1]


def test_arbitrary_command_is_rejected_before_execution():
    chat = FakeChat(['{"message":"I will run it.","action":{"command":"rm -rf /"}}'])
    service = make_service(chat)

    with pytest.raises(ValueError, match="outside the allow-list"):
        service.send(1, "conversation", "Delete everything", object())


# --- the OpenAI provider ----------------------------------------------------------------------------------------

from types import SimpleNamespace

from src.agent import chat as agent_service
from src.agent.providers import Completion, OpenAIProvider


class FakeCompletions:
    def __init__(self, content='{"message":"All good.","action":null}', refusal=None, fail=None):
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


def test_openai_provider_asks_for_the_reply_schema_and_reports_tokens():
    completions = FakeCompletions()
    provider = OpenAIProvider("sk-test", "gpt-test", client=fake_client(completions))

    result = provider.complete([{"role": "user", "content": "hi"}], agent_service.REPLY_SCHEMA, "agent_reply")

    assert result == Completion(text='{"message":"All good.","action":null}', model="gpt-test", input_tokens=120, output_tokens=8)
    format_ = completions.calls[0]["response_format"]
    assert format_["type"] == "json_schema" and format_["json_schema"]["strict"] is True
    # The model can only pick a check from the allow-list; anything else is impossible, not just rejected later.
    check = format_["json_schema"]["schema"]["properties"]["action"]["anyOf"][0]["properties"]["check"]
    assert set(check["enum"]) == set(agent_service.HealthCheckRunner.ALLOWED_CHECKS)


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
