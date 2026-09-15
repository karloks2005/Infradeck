import threading

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


def make_service(fake_chat):
    service = AgentService.__new__(AgentService)
    service.chats = {(1, "conversation"): fake_chat}
    service.pending_commands = {}
    service.lock = threading.Lock()
    return service


def test_low_risk_command_executes_and_logs(monkeypatch):
    chat = FakeChat([
        "Check the current user.\n[RUN]\n[RISK]: VERY LOW\n[REASON]: Identify the process user.\n[COMMAND]: whoami\n[EOT]",
        "The command completed successfully.",
    ])
    service = make_service(chat)
    events = []
    monkeypatch.setattr(service, "_run_command", lambda command: "[SYSTEM-OUTPUT]\n[EXIT_CODE]: 0\n[STDOUT]:\nhomie\n[STDERR]:\n\n[END-OUTPUT]")

    result = service.send(1, "conversation", "Who am I?", events.append)

    assert result == "The command completed successfully."
    assert [event["event_type"] for event in events] == ["command_proposed", "command_executed"]
    assert events[0]["reason"] == "Identify the process user."
    assert events[1]["exit_code"] == 0
    assert events[1]["stdout"] == "homie"


def test_medium_risk_command_waits_for_approval_then_executes(monkeypatch):
    chat = FakeChat([
        "Restart the service.\n[RUN]\n[RISK]: MEDIUM\n[REASON]: Apply the requested service change.\n[COMMAND]: systemctl restart homie\n[EOT]",
        "Approved command output received.",
        "The service restart completed.",
    ])
    service = make_service(chat)
    events = []
    monkeypatch.setattr(service, "_run_command", lambda command: "[SYSTEM-OUTPUT]\n[EXIT_CODE]: 0\n[STDOUT]:\nactive\n[STDERR]:\n\n[END-OUTPUT]")

    pending = service.send(1, "conversation", "Restart the service.", events.append)
    result = service.send(1, "conversation", "[APPROVE]", events.append)

    assert "[APPROVAL REQUIRED]" in pending
    assert result == "The service restart completed."
    assert [event["event_type"] for event in events] == [
        "command_proposed",
        "approval",
        "command_executed",
    ]
    assert events[0]["approval_status"] == "pending"
    assert events[1]["approval_status"] == "approved"
    assert events[2]["approval_status"] == "approved"
    assert chat.messages[-2] == "[APPROVED] Execute the pending command now."
