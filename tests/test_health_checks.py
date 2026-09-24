from src.agent.health_checks import HealthCheckRunner


def test_server_health_uses_python_system_interfaces(monkeypatch):
    runner = HealthCheckRunner()
    monkeypatch.setattr(runner, "_read_meminfo", lambda: {"MemTotal": 1000, "MemAvailable": 500})
    result = runner.server_health()

    assert result["check"] == "server_health"
    assert result["status"] in {"ok", "warning", "critical"}
    assert result["metrics"]["memory_used_percent"] == 50.0


def test_container_health_reports_unhealthy_container(monkeypatch):
    runner = HealthCheckRunner()
    monkeypatch.setattr(runner, "_docker_get", lambda path: [{"Names": ["/api"], "State": "running", "Status": "Up 1 minute (unhealthy)"}])

    result = runner.container_health()

    assert result["status"] == "critical"
    assert result["containers"]["unhealthy"] == ["api"]


def test_unknown_check_is_rejected():
    runner = HealthCheckRunner()

    try:
        runner.run("shell_command")
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown checks must be rejected")


def test_daily_checkup_alerts_only_for_critical_findings(monkeypatch):
    class Notifier:
        def __init__(self):
            self.findings = []

        def notify(self, finding):
            self.findings.append(finding)
            return {"status": "sent"}

    notifier = Notifier()
    runner = HealthCheckRunner(notifier=notifier)
    monkeypatch.setattr(runner, "server_health", lambda: {"check": "server_health", "status": "critical"})
    monkeypatch.setattr(runner, "container_health", lambda: {"check": "container_health", "status": "ok"})

    result = runner.daily_checkup()

    assert result["status"] == "critical"
    assert result["alert"] == {"status": "sent"}
    assert notifier.findings[0]["checks"][0]["check"] == "server_health"
