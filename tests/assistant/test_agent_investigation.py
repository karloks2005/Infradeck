"""The agent's investigation (#157): the diagnosis and the chat may look things up with the read-only tools, within a
budget of tool calls per answer, with every call written to the audit log. The model is a scripted fake; nothing
reaches a real API."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agent import chat, diagnosis, investigate, settings, tools
from src.backend.models import AgentReport, AgentRun, AppSetting, AuditLog, Base, ContainerMetric, HostMetric, IncidentAlert, Server
from tests.fakes import ScriptedProvider

NOW = datetime.now(timezone.utc)
CPU = ("host_metrics", {"server": "web1", "metrics": ["cpu"], "from": None, "to": None, "reason": "Was CPU high?"})
SCHEMA = chat.REPLY_SCHEMA
ANSWER = '{"message":"CPU is fine."}'


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Server(id=1, owner_id=1, hostname="web1", cpu_cores=4, total_ram_gb=16.0, check_interval_seconds=60, last_seen_at=NOW))
    for index in range(60):
        moment = NOW - timedelta(minutes=60 - index)
        session.add(HostMetric(server_id=1, time=moment, cpu_utilization_pct=20 + index % 5, ram_pct=60, swap_pct=0, load_1m=0.5))
        session.add(ContainerMetric(server_id=1, container_id="n" * 64, name="nginx", image="nginx:1.27", status="running", restart_count=0, cpu_pct=2, memory_used_mb=50,
                                    memory_limit_mb=512, time=moment))
    session.add(IncidentAlert(id=42, server_id=1, severity="CRITICAL", title="CPU on web1 is high", description="CPU above 90 % for 10 minutes.", status="active",
                              rule_key="host:cpu", created_at=NOW - timedelta(minutes=20)))
    session.commit()
    yield session
    session.close()


def run(db, provider, budget, audit=None, **options):
    return investigate.investigate(db, provider, [{"role": "user", "content": "Why?"}], SCHEMA, "agent_reply", budget=budget, effort="low",
                                   record=lambda completion, ms: None, audit=audit or (lambda entry: None), **options)


def audited(db):
    return db.query(AuditLog).filter(AuditLog.event_type == investigate.EVENT_TYPE).order_by(AuditLog.id).all()


# --- the budget setting ---------------------------------------------------------------------------------------

def test_the_budgets_default_to_8_per_diagnosis_and_4_per_chat_message(db):
    assert (settings.tool_budget(db, "diagnosis"), settings.tool_budget(db, "chat")) == (8, 4)


def test_a_stored_budget_is_used_and_a_broken_one_falls_back(db):
    db.add(AppSetting(key="agent_tool_budget_chat", value="0"))
    db.add(AppSetting(key="agent_tool_budget_diagnosis", value="many"))
    db.commit()

    assert (settings.tool_budget(db, "chat"), settings.tool_budget(db, "diagnosis")) == (0, 8)


@pytest.mark.parametrize("kind, value", [("diagnosis", 21), ("diagnosis", -1), ("chat", 11), ("chat", 2.5), ("chat", True), ("chat", "3")])
def test_a_budget_outside_its_range_is_refused(kind, value):
    with pytest.raises(ValueError, match="between 0 and"):
        settings.validate_tool_budget(kind, value)


# --- the loop -------------------------------------------------------------------------------------------------

def test_a_tool_call_runs_and_its_result_goes_back_to_the_model(db):
    provider = ScriptedProvider([CPU], ANSWER)

    found = run(db, provider, budget=4)

    assert found.completion.text == ANSWER and provider.allowed == [True, True]
    assert provider.started[0]["effort"] == "high" and provider.started[0]["tools"] == [tool.name for tool in tools.TOOLS]
    result = provider.results[0][0]
    assert not result.is_error and json.loads(result.content)["series"][0]["id"] == "metric:web1.cpu"
    assert "metric:web1.cpu" in found.labels and found.trail[0]["status"] == "ok" and found.trail[0]["tool"] == "host_metrics"


def test_without_a_budget_no_tools_are_offered_and_effort_stays_as_asked(db):
    provider = ScriptedProvider(ANSWER)

    run(db, provider, budget=0)

    assert provider.started == [{"tools": [], "effort": "low", "schema_name": "agent_reply"}] and provider.allowed == [False]


def test_calls_past_the_budget_are_refused_and_the_model_must_answer(db):
    provider = ScriptedProvider([CPU, CPU, CPU], ANSWER)

    found = run(db, provider, budget=2)

    assert [result.content for result in provider.results[0]][2] == investigate.BUDGET_USED_UP and provider.results[0][2].is_error
    assert [entry["status"] for entry in found.trail] == ["ok", "ok", "refused"]
    assert provider.allowed == [True, False] and len(provider.started) == 1   # the tool list never changes mid-conversation


def test_the_budget_counts_across_turns(db):
    provider = ScriptedProvider([CPU], [CPU], ANSWER)

    run(db, provider, budget=2)

    assert provider.allowed == [True, True, False]


def test_a_model_that_keeps_calling_tools_when_told_to_answer_is_stopped(db):
    provider = ScriptedProvider([CPU], [CPU], [CPU], [CPU])

    with pytest.raises(ValueError, match="kept asking for tools"):
        run(db, provider, budget=1)


def test_bad_arguments_go_back_to_the_model_as_an_error_it_can_correct(db):
    provider = ScriptedProvider([("host_metrics", {"server": "web1"}), ("rm_rf", {}), ("server_info", {"server": "nope", "reason": "What is it?"})], ANSWER)

    found = run(db, provider, budget=4)

    errors = provider.results[0]
    assert all(result.is_error for result in errors)
    assert "Give exactly these arguments" in errors[0].content and "no tool named" in errors[1].content and "nope" in errors[2].content
    assert [entry["status"] for entry in found.trail] == ["error", "error", "error"]


def test_a_failing_tool_tells_the_model_nothing_about_why(db, monkeypatch, caplog):
    def broken(*args):
        raise RuntimeError("password=hunter2 in a stack trace")
    monkeypatch.setitem(tools._REGISTRY, "server_info", (tools._REGISTRY["server_info"][0], broken))
    provider = ScriptedProvider([("server_info", {"server": "web1", "reason": "What is it?"})], ANSWER)

    run(db, provider, budget=2)

    assert provider.results[0][0].content == investigate.LOOKUP_FAILED and "hunter2" not in provider.results[0][0].content
    assert "server_info failed" in caplog.text


def test_reaching_the_spending_cap_stops_the_look_ups(db, monkeypatch):
    monkeypatch.setattr(investigate.usage, "budget", lambda db: {"state": "paused"})
    provider = ScriptedProvider(ANSWER)

    run(db, provider, budget=4)

    assert provider.allowed == [False]


def test_after_the_deadline_the_model_must_answer(db):
    provider = ScriptedProvider(ANSWER)

    run(db, provider, budget=4, deadline_seconds=0)

    assert provider.allowed == [False]


def test_every_tool_call_is_written_to_the_audit_log(db):
    provider = ScriptedProvider([CPU, ("server_info", {"server": "nope", "reason": "What is it?"})], [CPU], ANSWER)

    run(db, provider, budget=2, audit=investigate.audit_log(db, title="Why is web1 slow?", user_id=1, conversation_id="c1"))

    rows = audited(db)
    assert [(row.level, row.source, row.user_id, row.conversation_id, row.task_title) for row in rows] == [("INFO", "agent", 1, "c1", "Why is web1 slow?"), ("WARN", "agent", 1, "c1", "Why is web1 slow?"),
                                                                                                         ("WARN", "agent", 1, "c1", "Why is web1 slow?")]
    steps = json.loads(rows[0].steps)
    assert rows[0].message == "The agent looked up host_metrics" and steps[0] == "Reason: Was CPU high?" and '"server":"web1"' in steps[1] and "reason" not in steps[1]
    assert rows[2].message.endswith("(refused)")


def test_secrets_in_the_arguments_are_masked_in_the_trail(db):
    provider = ScriptedProvider([("server_info", {"server": "password=hunter2", "reason": "Is password=hunter2 set?"})], ANSWER)

    found = run(db, provider, budget=1)

    entry = found.trail[0]
    assert "hunter2" not in entry["arguments"] and "hunter2" not in entry["result"] and "hunter2" not in entry["reason"]


# --- the diagnosis --------------------------------------------------------------------------------------------

def tool_answer(evidence, verify=None):
    return json.dumps({"summary": "A runaway process.", "confidence": "high", "signals": [{"text": "CPU was high for an hour.", "evidence": evidence, "holds": True}],
                       "fix_steps": [], "verify": verify or {"type": "manual", "target": None, "threshold": None, "minutes": None}})


def test_a_diagnosis_may_cite_what_a_tool_found_and_keeps_the_trail(db):
    provider = ScriptedProvider([CPU], tool_answer(["metric:web1.cpu"]))

    report = diagnosis.diagnose(db, db.get(IncidentAlert, 42), provider)

    shown = diagnosis.report_json(report)
    assert report.status == "ok" and shown["signals"][0]["evidence"][0]["id"] == "metric:web1.cpu" and shown["dropped_signals"] == 0
    trail = json.loads(report.body)["investigation"]
    assert [entry["tool"] for entry in trail] == ["host_metrics"] and json.loads(trail[0]["result"])["server"] == "web1"
    step = shown["steps"][0]   # the page gets the look-up in plain words, never its raw result
    assert (step["reason"], step["asked"]) == ("Was CPU high?", "CPU on web1") and step["found"].startswith("CPU ") and "points" not in json.dumps(shown)
    assert db.query(AgentRun).count() == 2 and all(run.kind == "diagnosis" for run in db.query(AgentRun))
    assert audited(db)[0].task_title == "Diagnosis of INC-0042" and audited(db)[0].user_id is None
    assert "up to 8 tool calls" in provider.calls[0][-1]["content"]


def test_a_cited_id_no_tool_returned_is_still_dropped(db):
    report = diagnosis.diagnose(db, db.get(IncidentAlert, 42), ScriptedProvider([CPU], tool_answer(["metric:web1.memory"])))

    assert diagnosis.report_json(report)["dropped_signals"] == 1


def test_a_fix_cannot_be_verified_on_a_metric_only_a_tool_returned(db):
    verify = {"type": "metric_below_for", "target": "metric:web1.cpu", "threshold": 50, "minutes": 30}

    report = diagnosis.diagnose(db, db.get(IncidentAlert, 42), ScriptedProvider([CPU], tool_answer(["metric:web1.cpu"], verify)))

    assert diagnosis.report_json(report)["verify"]["type"] == "manual"   # verify.py can only watch the pack's metrics


def test_an_unusable_answer_is_asked_for_again_without_tools_in_the_same_conversation(db):
    provider = ScriptedProvider([CPU], "not json", tool_answer(["metric:web1.cpu"]))

    report = diagnosis.diagnose(db, db.get(IncidentAlert, 42), provider)

    assert report.status == "ok" and len(provider.started) == 1 and provider.allowed == [True, True, False]
    assert "could not be used" in provider.calls[-1][-1]["content"]


def test_with_the_diagnosis_budget_at_0_the_agent_gets_no_tools(db):
    db.add(AppSetting(key="agent_tool_budget_diagnosis", value="0"))
    db.commit()
    provider = ScriptedProvider(tool_answer(["metric:main"]))

    diagnosis.diagnose(db, db.get(IncidentAlert, 42), provider)

    assert provider.started[0]["tools"] == [] and "no tools this time" in provider.calls[0][-1]["content"]


def test_a_provider_error_midway_is_counted_once_and_raised(db):
    with pytest.raises(TimeoutError):
        diagnosis.diagnose(db, db.get(IncidentAlert, 42), ScriptedProvider([CPU], TimeoutError("timed out")))

    assert [run.status for run in db.query(AgentRun).order_by(AgentRun.id)] == ["ok", "error"] and db.query(AgentReport).count() == 0


# --- the chat -------------------------------------------------------------------------------------------------

def test_a_chat_message_can_look_things_up_within_the_chat_budget(db):
    provider, logged = ScriptedProvider([CPU], ANSWER), []

    reply = chat.AgentService().send(1, "c1", "Is web1 busy?", provider, db=db, budget=4, usage_logger=lambda completion, ms: logged.append(completion.output_tokens),
                                     audit=investigate.audit_log(db, title="Is web1 busy?", user_id=1, conversation_id="c1"))

    assert reply.message == "CPU is fine." and logged == [300, 300] and len(audited(db)) == 1
    assert [(step["tool"], step["reason"], step["asked"]) for step in reply.steps] == [("host_metrics", "Was CPU high?", "CPU on web1")]
    assert provider.started[0]["effort"] == "high"


def test_the_chat_remembers_questions_and_replies_but_not_earlier_look_ups(db):
    service = chat.AgentService()
    provider = ScriptedProvider([CPU], ANSWER, '{"message":"Still fine."}')

    service.send(1, "c1", "Is web1 busy?", provider, db=db, budget=4)
    service.send(1, "c1", "And now?", provider, db=db, budget=4)

    assert [message["role"] for message in provider.calls[-1]] == ["system", "user", "assistant", "user"]
