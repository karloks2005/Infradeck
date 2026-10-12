import threading

import pytest

from src.agent.service import AgentService


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeChat:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def send_message(self, message):
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

    result = service.send(1, "conversation", "Check the host.", events.append)

    assert result == "Server health is OK."
    assert checks.requested == ["server_health"]
    assert [event["event_type"] for event in events] == ["health_check_requested", "health_check_completed"]
    assert "HEALTH-CHECK-RESULT" in chat.messages[-1]


def test_arbitrary_command_is_rejected_before_execution():
    chat = FakeChat(['{"message":"I will run it.","action":{"command":"rm -rf /"}}'])
    service = make_service(chat)

    with pytest.raises(ValueError, match="outside the allow-list"):
        service.send(1, "conversation", "Delete everything")
