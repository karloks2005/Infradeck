"""Alert engine: at most one active alert per server and rule, resolved once the rule clears.

Callers describe *conditions* by a stable ``rule_key``. Raising an alert that is
already active only refreshes its severity and text, so a condition that persists
across many check-ins never produces duplicates.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from .models import IncidentAlert

INFO, WARNING, CRITICAL = "INFO", "WARNING", "CRITICAL"
SEVERITIES = (INFO, WARNING, CRITICAL)

SERVER_OFFLINE_RULE = "server_offline"
CONTAINER_RULE_PREFIX = "container:"


def _active(db, server_id: int):
    return db.query(IncidentAlert).filter(IncidentAlert.server_id == server_id, IncidentAlert.status == "active")


def active_rule_keys(db, server_id: int, prefix: str) -> set[str]:
    return {key for (key,) in _active(db, server_id).filter(IncidentAlert.rule_key.startswith(prefix)).with_entities(IncidentAlert.rule_key)}


def raise_alert(db, server_id: int, rule_key: str, severity: str, title: str, description: str) -> bool:
    """Open an alert for ``rule_key``, or refresh the active one. Returns True if a new alert was opened."""
    if severity not in SEVERITIES:
        raise ValueError(f"Unknown severity: {severity}")
    existing = _active(db, server_id).filter(IncidentAlert.rule_key == rule_key).first()
    if existing:
        existing.severity, existing.title, existing.description = severity, title[:255], description
        return False
    db.add(IncidentAlert(server_id=server_id, rule_key=rule_key, severity=severity, title=title[:255], description=description))
    return True


def resolve_alerts(db, server_id: int, rule_keys: Iterable[str], now: datetime | None = None) -> int:
    """Resolve the active alerts for the given rules. Returns how many were resolved."""
    rule_keys = list(rule_keys)
    if not rule_keys:
        return 0
    return _active(db, server_id).filter(IncidentAlert.rule_key.in_(rule_keys)).update(
        {"status": "resolved", "resolved_at": now or datetime.now(timezone.utc)}, synchronize_session=False
    )


def sync_alerts(db, server_id: int, prefix: str, firing: dict[str, tuple[str, str, str]], now: datetime | None = None) -> None:
    """Make the active alerts under ``prefix`` match ``firing`` ({rule_key: (severity, title, description)}).

    Rules that are firing are raised or refreshed; active rules under the prefix that
    are no longer firing are resolved.
    """
    for rule_key, (severity, title, description) in firing.items():
        raise_alert(db, server_id, rule_key, severity, title, description)
    resolve_alerts(db, server_id, active_rule_keys(db, server_id, prefix) - firing.keys(), now)

