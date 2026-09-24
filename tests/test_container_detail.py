import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agent import collector
from src.backend import main
from src.backend.models import Base, ContainerMetric, Server, User


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(id=1, name="Owner", email="owner@example.com", password_hash="x"),
        User(id=2, name="Other", email="other@example.com", password_hash="x"),
    ])
    session.add(Server(id=10, owner_id=1, hostname="nas"))
    now = datetime.now(timezone.utc)
    for index in range(3):
        session.add(ContainerMetric(server_id=10, container_id="abc", name="web", status="running", cpu_pct=float(index), memory_used_mb=100.0 + index, memory_limit_mb=1024.0, pids=4, time=now - timedelta(minutes=3 - index)))
    session.commit()
    return session


def test_container_detail_returns_latest_and_chronological_history(monkeypatch):
    session = _session()
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 1})

    result = main.container_detail(10, "abc", limit=60, session_id="s", db=session)

    assert result["name"] == "web"
    assert result["latest"]["cpu_pct"] == 2.0
    assert result["latest"]["memory_limit_mb"] == 1024.0
    assert [sample["cpu_pct"] for sample in result["history"]] == [0.0, 1.0, 2.0]


def test_container_detail_hides_other_users_servers(monkeypatch):
    session = _session()
    monkeypatch.setattr(main, "_current_user", lambda session_id, db: {"id": 2})

    with pytest.raises(HTTPException) as exc:
        main.container_detail(10, "abc", limit=60, session_id="s", db=session)
    assert exc.value.status_code == 404


def test_container_usage_parses_docker_stats(monkeypatch):
    stats = {
        "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 1000, "online_cpus": 4},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 600},
        "memory_stats": {"usage": 300 * collector.MiB, "limit": 1000 * collector.MiB, "stats": {"inactive_file": 100 * collector.MiB}},
        "networks": {"eth0": {"rx_bytes": 2 * collector.MiB, "tx_bytes": collector.MiB}},
        "blkio_stats": {"io_service_bytes_recursive": [{"op": "read", "value": 5 * collector.MiB}, {"op": "write", "value": 3 * collector.MiB}]},
        "pids_stats": {"current": 7},
    }
    monkeypatch.setattr(collector, "_docker_request", lambda socket_path, endpoint: stats)

    usage = collector._container_usage("/sock", "abc")

    assert usage["cpu_pct"] == 200.0
    assert usage["memory_used_mb"] == 200.0
    assert usage["memory_pct"] == 20.0
    assert (usage["net_rx_mb"], usage["net_tx_mb"]) == (2.0, 1.0)
    assert (usage["block_read_mb"], usage["block_write_mb"]) == (5.0, 3.0)
    assert usage["pids"] == 7


def test_format_ports_lists_published_bindings():
    ports = {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}, {"HostIp": "::", "HostPort": "8080"}], "443/tcp": None}
    assert collector._format_ports(ports) == "8080->80/tcp"
