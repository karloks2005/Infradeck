"""The agent's providers (Claude, OpenAI, Gemini) with fake clients, and how the chosen provider and its encrypted
API key are stored. No test reaches a real API."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agent import chat as agent_service, providers
from src.agent.providers import Completion, NoApiKey
from src.agent.providers.claude import ClaudeProvider
from src.agent.providers.openai_compatible import GEMINI_BASE_URL, OpenAIProvider
from src.backend import secrets_box
from src.backend.models import AppSetting, Base


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


# --- Claude ------------------------------------------------------------------------------------------------------

class FakeMessages:
    def __init__(self, text='{"message":"All good.","action":null}', stop_reason="end_turn"):
        self.calls, self.text, self.stop_reason = [], text, stop_reason

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=100, output_tokens=8, cache_creation_input_tokens=20, cache_read_input_tokens=5)
        content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self.text)]
        return SimpleNamespace(content=content, stop_reason=self.stop_reason, model="claude-test", usage=usage)


def claude(messages):
    client = SimpleNamespace(messages=messages, models=SimpleNamespace(retrieve=lambda model: SimpleNamespace(id=model)))
    return ClaudeProvider("sk-ant-test", "claude-test", client=client)


def test_claude_gets_the_system_prompt_apart_and_must_answer_in_the_schema():
    messages = FakeMessages()
    turns = [{"role": "system", "content": "You are the agent."}, {"role": "user", "content": "hi"}, {"role": "assistant", "content": "{}"}, {"role": "user", "content": "again"}]

    result = claude(messages).complete(turns, agent_service.REPLY_SCHEMA, "agent_reply", effort="low")

    assert result == Completion(text='{"message":"All good.","action":null}', model="claude-test", input_tokens=125, output_tokens=8)
    sent = messages.calls[0]
    assert sent["system"] == "You are the agent." and [turn["role"] for turn in sent["messages"]] == ["user", "assistant", "user"]
    assert sent["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": agent_service.REPLY_SCHEMA}}
    assert sent["thinking"] == {"type": "adaptive"} and sent["cache_control"] == {"type": "ephemeral"}


@pytest.mark.parametrize("stop_reason, error", [("refusal", "refused"), ("max_tokens", "cut off")])
def test_a_refused_or_cut_off_claude_answer_is_an_error(stop_reason, error):
    with pytest.raises(ValueError, match=error):
        claude(FakeMessages(text="{", stop_reason=stop_reason)).complete([{"role": "user", "content": "hi"}], agent_service.REPLY_SCHEMA, "agent_reply")


def test_claude_test_connection_spends_no_tokens():
    messages = FakeMessages()

    assert claude(messages).check() == {"provider": "Anthropic", "model": "claude-test"}
    assert messages.calls == []


# --- which provider, and its key ---------------------------------------------------------------------------------

def test_without_a_choice_the_agent_has_no_provider(db):
    assert providers.current(db).provider is None and not providers.key_configured(db)
    with pytest.raises(NoApiKey):
        providers.get_provider(db)


@pytest.mark.parametrize("provider, cls, model", [("anthropic", ClaudeProvider, "claude-haiku-5-5"), ("openai", OpenAIProvider, "gpt-6-luna"), ("gemini", OpenAIProvider, "gemini-3.1-flash-lite")])
def test_each_provider_starts_with_its_own_default_model(db, provider, cls, model):
    providers.save(db, provider, None, "test-key-123")

    built = providers.get_provider(db)
    assert isinstance(built, cls) and built.model == model


def test_gemini_goes_to_googles_address(db):
    providers.save(db, "gemini", None, "AIza-test-key")

    assert str(providers.get_provider(db).client.base_url) == GEMINI_BASE_URL


def test_the_key_is_stored_encrypted_and_each_provider_keeps_its_own(db):
    providers.save(db, "anthropic", "claude-test", "sk-ant-first-key")
    providers.save(db, "openai", None, "sk-openai-key")
    assert all("sk-" not in setting.value for setting in db.query(AppSetting))

    providers.save(db, "anthropic", None, None)   # no new key: the stored one stays
    assert providers.current(db).key == "sk-ant-first-key"


def test_a_key_that_cant_be_decrypted_counts_as_no_key(db, monkeypatch):
    providers.save(db, "anthropic", None, "sk-ant-key")
    monkeypatch.setenv("SECRETS_KEY", secrets_box.generate_key())

    assert not providers.key_configured(db)


def test_a_removed_key_turns_the_agent_off(db):
    providers.save(db, "openai", None, "sk-openai-key")
    providers.remove_key(db, "openai")

    assert not providers.key_configured(db) and providers.current(db).provider == "openai"
