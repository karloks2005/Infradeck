"""Incidents (the Agent page): the inbox, one incident with its diagnosis, asking the agent to diagnose again, and
the operator actions (acknowledge, resolve, mute, unmute). Incident logic lives in incidents.py and the agent
package; this module is the HTTP layer. ``_agent_note`` and ``_incident_context`` stay in main (the Overview and
chat use them)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import incidents, main
from ...agent import jobs as agent_jobs, settings as agent_settings, usage as agent_usage, verify as agent_verify
from ...agent.diagnosis import latest as latest_report, report_json
from ...agent.providers import key_configured
from ..config import SESSION_COOKIE_NAME
from ..db import get_db
from ..models import IncidentAlert, Server, User

router = APIRouter()


class MuteRequest(BaseModel):
    minutes: int = Field(default=60, ge=5, le=7 * 24 * 60)


def _incident_or_404(db, incident_id: int) -> tuple[IncidentAlert, Server]:
    alert = db.get(IncidentAlert, incident_id)
    server = db.get(Server, alert.server_id) if alert else None
    if not alert or not server:
        raise HTTPException(status_code=404, detail="Incident not found.")
    return alert, server


@router.get("/incidents")
def list_incidents(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main._current_user(session_id, db)
    return {**incidents.inbox(db), "can_act": main._is_admin(db, user["id"])}


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main._current_user(session_id, db)
    alert, server = _incident_or_404(db, incident_id)
    users = dict(db.query(User.id, User.name).all())
    # Admins always see the agent's diagnosis; others only while Admin shows it to them (checked here, not in the page).
    visible = agent_settings.sees_diagnoses(db, "admin" if main._is_admin(db, user["id"]) else "user")
    report = report_json(latest_report(db, alert.id)) if visible else None
    if report and report["status"] == "ok" and alert.status == "active":
        report["watch"] = agent_verify.evaluate(db, alert, report["verify"])
    return {**incidents.summary(alert, server.hostname, datetime.now(timezone.utc), users), "diagnosis": incidents.diagnose(db, alert, server), "cpu_cores": server.cpu_cores,
            "agent": {"report": report, "hidden": not visible, "key_configured": key_configured(db), "budget_paused": agent_usage.budget(db)["state"] == "paused"}}


@router.post("/incidents/{incident_id}/diagnose")
def request_diagnosis(incident_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Ask the agent worker to diagnose this incident (again). It works through the queue, so the report appears
    on the incident when the worker gets to it."""
    user = main._require_operator(session_id, db)
    alert, _server = _incident_or_404(db, incident_id)
    if not key_configured(db):
        raise HTTPException(status_code=409, detail="The agent has no API key. An admin adds one in Admin › Incident agent.")
    if agent_usage.budget(db)["state"] == "paused":
        raise HTTPException(status_code=429, detail="This month's budget for the agent is used up. An admin can raise it in Admin › Incident agent.")
    main.enforce_limit(main.redis_client, scope="diagnose-user", subject=user["id"], limit=10, window_seconds=600)
    try:
        agent_jobs.publish(main.redis_client, agent_jobs.REQUESTED, alert.id, alert.severity)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Redis is not answering, so the request can't be queued.") from exc
    main.record_audit_log(db, user_id=user["id"], level="INFO", message=f"Asked the agent to diagnose {incidents.reference(alert.id)}", source="agent")
    return {"queued": True}


def _incident_action(db, incident_id: int, session_id, change, message: str) -> dict:
    user = main._require_operator(session_id, db)
    alert, server = _incident_or_404(db, incident_id)
    change(alert, user)
    db.commit()
    main.record_audit_log(db, user_id=user["id"], level="INFO", message=f"{message} {incidents.reference(alert.id)} ({alert.title})"[:255], source="incidents")
    return incidents.summary(alert, server.hostname, datetime.now(timezone.utc), dict(db.query(User.id, User.name).all()))


@router.post("/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    def change(alert, user):
        alert.acknowledged_at, alert.acknowledged_by = datetime.now(timezone.utc), user["id"]
    return _incident_action(db, incident_id, session_id, change, "Acknowledged")


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(incident_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Close an incident by hand. If the rule behind it is still firing at the next check-in, a new one opens."""
    def change(alert, user):
        if alert.status != "active":
            raise HTTPException(status_code=409, detail="This incident is already resolved.")
        alert.status, alert.resolved_at, alert.resolution = "resolved", datetime.now(timezone.utc), f"Marked resolved by {user['name']}"[:255]
    return _incident_action(db, incident_id, session_id, change, "Marked resolved")


@router.post("/incidents/{incident_id}/mute")
def mute_incident(incident_id: main.PathId, payload: MuteRequest | None = None, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    minutes = (payload or MuteRequest()).minutes

    def change(alert, user):
        alert.muted_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return _incident_action(db, incident_id, session_id, change, f"Muted for {minutes} min")


@router.post("/incidents/{incident_id}/unmute")
def unmute_incident(incident_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    def change(alert, user):
        alert.muted_until = None
    return _incident_action(db, incident_id, session_id, change, "Unmuted")
