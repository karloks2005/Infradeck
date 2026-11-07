"""Tool calling through the provider-neutral interface, the same tests for Claude, OpenAI and Gemini with fake clients.
No test reaches a real API."""
import json
from types import SimpleNamespace as NS

import pytest

from src.agent.providers import Tool, ToolResult
from src.agent.providers.claude import ClaudeProvider
from src.agent.providers.openai_compatible import GEMINI_BASE_URL, OpenAIProvider

ANSWER = '{"message":"Done."}'
SCHEMA = {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"], "additionalProperties": False}
TOOLS = [Tool("get_host_summary", "The server's CPU, memory and disks right now.", {"type": "object", "properties": {"server_id": {"type": "integer"}}, "required": ["server_id"], "additionalProperties": False})]
MESSAGES = [{"role": "system", "content": "You are the agent."}, {"role": "user", "content": "Why is it slow?"}]


# --- fake clients: each returns the scripted turns in order; a turn is a list of (call id, arguments) or the answer ---

class FakeClaude:
    def __init__(self, *turns, stop_reason=None):
        self.turns, self.calls, self.stop_reason = list(turns), [], stop_reason

    def create(self, **kwargs):
        self.calls.append(kwargs)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            content, stop = [NS(type="thinking", thinking=""), NS(type="text", text=turn)], "end_turn"
        else:
            content, stop = [NS(type="thinking", thinking="")] + [NS(type="tool_use", id=call_id, name="get_host_summary", input=args) for call_id, args in turn], "tool_use"
        usage = NS(input_tokens=100, output_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return NS(content=content, stop_reason=self.stop_reason or stop, model="claude-test", usage=usage)

    def sent(self, index):
        return self.calls[index]["messages"]

    def tool_choice(self, index):
        return self.calls[index]["tool_choice"]["type"]


class FakeOpenAI:
    def __init__(self, *turns, refusal=None, raw_arguments=None):
        self.turns, self.calls, self.refusal, self.raw_arguments = list(turns), [], refusal, raw_arguments

    def create(self, **kwargs):
        self.calls.append(kwargs)
        turn = self.turns.pop(0)
        if isinstance(turn, str):
            message = NS(content=turn, refusal=self.refusal, tool_calls=None)
        else:
            calls = [NS(id=call_id, function=NS(name="get_host_summary", arguments=self.raw_arguments or json.dumps(args))) for call_id, args in turn]
            message = NS(content=None, refusal=self.refusal, tool_calls=calls)
        return NS(choices=[NS(message=message)], model="gpt-test", usage=NS(prompt_tokens=100, completion_tokens=10))

    def sent(self, index):
        return self.calls[index]["messages"]

    def tool_choice(self, index):
        return self.calls[index]["tool_choice"]


def claude(fake):
    return ClaudeProvider("key", "claude-test", client=NS(messages=fake))


def openai(fake):
    return OpenAIProvider("key", "gpt-test", client=NS(chat=NS(completions=fake)))


def gemini(fake):
    return OpenAIProvider("key", "gpt-test", name="Google Gemini", base_url=GEMINI_BASE_URL, client=NS(chat=NS(completions=fake)))


PROVIDERS = [pytest.param(claude, FakeClaude, id="claude"), pytest.param(openai, FakeOpenAI, id="openai"), pytest.param(gemini, FakeOpenAI, id="gemini")]


@pytest.mark.parametrize("build, Fake", PROVIDERS)
def test_with_tools_the_model_can_still_answer_at_once(build, Fake):
    fake = Fake(ANSWER)

    turn = build(fake).start(MESSAGES, TOOLS, SCHEMA, "reply").step()

    assert turn.tool_calls == [] and turn.completion.text == ANSWER
    assert (turn.completion.input_tokens, turn.completion.output_tokens) == (100, 10)


@pytest.mark.parametrize("build, Fake", PROVIDERS)
def test_a_tool_call_is_answered_and_the_history_goes_back_unchanged(build, Fake):
    fake = Fake([("call_1", {"server_id": 1})], ANSWER)
    conversation = build(fake).start(MESSAGES, TOOLS, SCHEMA, "reply")

    first = conversation.step()
    assert [(call.id, call.name, call.arguments) for call in first.tool_calls] == [("call_1", "get_host_summary", {"server_id": 1})]
    assert first.completion.text == ""
    conversation.add_tool_results([ToolResult("call_1", "CPU 95 %")])
    second = conversation.step()

    assert second.completion.text == ANSWER
    sent_first, sent_second = fake.sent(0), fake.sent(1)
    assert sent_second[:len(sent_first)] == sent_first            # append-only: earlier turns are sent again as they were
    assert "CPU 95 %" in json.dumps(sent_second[-1], default=str)


def test_claude_gets_its_own_answer_back_untouched_thinking_included():
    fake = FakeClaude([("call_1", {"server_id": 1})], ANSWER)
    conversation = claude(fake).start(MESSAGES, TOOLS, SCHEMA, "reply")
    conversation.step()
    conversation.add_tool_results([ToolResult("call_1", "ok")])
    conversation.step()

    assistant = fake.sent(1)[1]
    assert assistant["role"] == "assistant" and assistant["content"][0].type == "thinking"
    tool = fake.calls[0]["tools"][0]
    assert tool["strict"] is True and tool["input_schema"] == TOOLS[0].schema and fake.calls[0]["system"] == "You are the agent."


@pytest.mark.parametrize("build, Fake", PROVIDERS)
def test_several_tool_calls_in_one_turn_get_all_their_results(build, Fake):
    fake = Fake([("call_1", {"server_id": 1}), ("call_2", {"server_id": 2})], ANSWER)
    conversation = build(fake).start(MESSAGES, TOOLS, SCHEMA, "reply")

    assert [call.id for call in conversation.step().tool_calls] == ["call_1", "call_2"]
    conversation.add_tool_results([ToolResult("call_1", "one"), ToolResult("call_2", "two")])
    conversation.step()

    sent = fake.sent(1)
    if Fake is FakeClaude:   # one user message with both results
        assert [block["tool_use_id"] for block in sent[-1]["content"]] == ["call_1", "call_2"]
    else:                    # one tool message per result
        assert [message["tool_call_id"] for message in sent[-2:]] == ["call_1", "call_2"]


@pytest.mark.parametrize("build, Fake", PROVIDERS)
def test_a_failed_tool_is_reported_as_an_error(build, Fake):
    fake = Fake([("call_1", {"server_id": 9})], ANSWER)
    conversation = build(fake).start(MESSAGES, TOOLS, SCHEMA, "reply")
    conversation.step()
    conversation.add_tool_results([ToolResult("call_1", "No such server.", is_error=True)])
    conversation.step()

    result = fake.sent(1)[-1]
    if Fake is FakeClaude:
        assert result["content"][0]["is_error"] is True
    else:
        assert result["content"] == "Error: No such server."


@pytest.mark.parametrize("build, Fake", PROVIDERS)
def test_tool_results_are_masked_before_they_leave(build, Fake):
    fake = Fake([("call_1", {"server_id": 1})], ANSWER)
    conversation = build(fake).start(MESSAGES, TOOLS, SCHEMA, "reply")
    conversation.step()
    conversation.add_tool_results([ToolResult("call_1", "DB_PASSWORD=hunter2 in the container's environment")])
    conversation.step()

    sent = json.dumps(fake.sent(1)[-1], default=str)
    assert "hunter2" not in sent and "[masked]" in sent


@pytest.mark.parametrize("build, Fake", PROVIDERS)
def test_without_tools_allowed_the_model_must_answer_but_the_tools_stay_listed(build, Fake):
    fake = Fake([("call_1", {"server_id": 1})], ANSWER)
    conversation = build(fake).start(MESSAGES, TOOLS, SCHEMA, "reply")
    conversation.step()
    conversation.add_tool_results([ToolResult("call_1", "Tool budget used up; give your final answer now.", is_error=True)])
    conversation.step(allow_tools=False)

    assert (fake.tool_choice(0), fake.tool_choice(1)) == ("auto", "none")
    assert fake.calls[1]["tools"] == fake.calls[0]["tools"]


def test_claude_refusing_midway_is_an_error():
    with pytest.raises(ValueError, match="refused"):
        claude(FakeClaude([("call_1", {})], stop_reason="refusal")).start(MESSAGES, TOOLS, SCHEMA, "reply").step()


def test_openai_refusing_is_an_error():
    with pytest.raises(ValueError, match="refused"):
        openai(FakeOpenAI(ANSWER, refusal="I can't help with that.")).start(MESSAGES, TOOLS, SCHEMA, "reply").step()


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]"])
def test_openai_tool_arguments_must_be_a_json_object(raw):
    with pytest.raises(ValueError, match="tool arguments"):
        openai(FakeOpenAI([("call_1", {})], raw_arguments=raw)).start(MESSAGES, TOOLS, SCHEMA, "reply").step()


def test_openai_tools_are_strict_functions():
    fake = FakeOpenAI(ANSWER)
    openai(fake).start(MESSAGES, TOOLS, SCHEMA, "reply").step()

    function = fake.calls[0]["tools"][0]["function"]
    assert fake.calls[0]["tools"][0]["type"] == "function" and function["strict"] is True and function["parameters"] == TOOLS[0].schema
    assert fake.calls[0]["response_format"]["json_schema"]["strict"] is True
