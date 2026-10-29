"""Availability page: the service checks the collectors run, and the Test button (Milestone 11). See availability.py
for the logic; this module is only the HTTP layer."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query

from .. import availability, main
from ..alerts import resolve_alerts
from ..config import SESSION_COOKIE_NAME
from ..db import get_db
from ..models import Check, Server

router = APIRouter()


def _check_or_404(db, check_id: int) -> Check:
    check = db.get(Check, check_id)
    if not check:
        raise HTTPException(status_code=404, detail="Check not found.")
    return check


@router.get("/availability")
def get_availability(session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    user = main._current_user(session_id, db)
    is_admin = main._is_admin(db, user["id"])
    # Non-admins can't edit checks, so they get targets with credentials and query strings (possible secrets) removed.
    return {**availability.overview(db, redact=not is_admin), "can_edit": is_admin}


def _save_check(db, check: Check, payload: dict) -> dict:
    try:
        fields = availability.validate(payload, {server_id for (server_id,) in db.query(Server.id)})
    except ValueError as exc:
        print(f"Check not saved: {exc}", flush=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    servers = json.loads(fields["server_ids"])
    for server_id in servers:
        others = sum(server_id in availability.servers_of(other) for other in db.query(Check).filter(Check.id != (check.id or 0)))
        if others >= availability.MAX_CHECKS_PER_SERVER:
            raise HTTPException(status_code=400, detail=f"A server can run at most {availability.MAX_CHECKS_PER_SERVER} checks.")
    old_servers = availability.servers_of(check) if check.id else []
    for key, value in fields.items():
        setattr(check, key, value)
    if check.id is None:
        db.add(check)
    elif old_servers and old_servers[0] != servers[0]:
        resolve_alerts(db, old_servers[0], [availability.rule_key(check)])
    db.flush()
    availability.evaluate(db, check)
    db.commit()
    return availability.summary(db, check, dict(db.query(Server.id, Server.hostname).all()))


@router.get("/availability/checks/{check_id}")
def get_check(check_id: main.PathId, range: str = Query(default="24h"), session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._current_user(session_id, db)
    return availability.detail(db, _check_or_404(db, check_id), range)


@router.post("/availability/test")
def test_check(payload: dict, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Run a check once from its servers before saving it. Only servers whose collector can run checks are asked."""
    user = main._require_admin(session_id, db)
    servers = {server.id: server for server in db.query(Server)}
    try:
        fields = availability.validate(payload, set(servers))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    able = [server_id for server_id in json.loads(fields["server_ids"]) if availability.can_run_checks(servers[server_id])]
    if not able:
        raise HTTPException(status_code=400, detail="None of the chosen servers can run checks yet: their collector is older than 0.10.0.")
    try:
        # The expiry is set when the counter is created, so a failure between two calls can't leave one that never ends.
        main.redis_client.set(f"infradeck:checktest:user:{user['id']}", 0, ex=60, nx=True)
        if main.redis_client.incr(f"infradeck:checktest:user:{user['id']}") > 20:
            raise HTTPException(status_code=429, detail="At most 20 tests a minute. Try again shortly.")
        test_id = availability.start_test(main.redis_client, fields, able)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Testing needs Redis, which isn't answering.") from exc
    return availability.test_status(main.redis_client, test_id, {server_id: servers[server_id].hostname for server_id in servers})


@router.get("/availability/test/{test_id}")
def get_check_test(test_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    status = availability.test_status(main.redis_client, test_id, dict(db.query(Server.id, Server.hostname).all()))
    if status is None:
        raise HTTPException(status_code=404, detail="This test is over. Run it again.")
    return status


@router.post("/availability/checks")
def create_check(payload: dict, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    return _save_check(db, Check(), payload)


@router.patch("/availability/checks/{check_id}")
def update_check(check_id: main.PathId, payload: dict, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    return _save_check(db, _check_or_404(db, check_id), payload)


@router.post("/availability/checks/{check_id}/pause")
def pause_check(check_id: main.PathId, payload: dict, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    check = _check_or_404(db, check_id)
    check.paused = payload.get("paused") is True
    if not check.paused:
        check.state = "pending"
    availability.evaluate(db, check)
    db.commit()
    return availability.summary(db, check, dict(db.query(Server.id, Server.hostname).all()))


@router.delete("/availability/checks/{check_id}")
def delete_check(check_id: main.PathId, session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    main._require_admin(session_id, db)
    check = _check_or_404(db, check_id)
    availability.forget_check(db, check)
    db.commit()
    return {"status": "deleted", "id": check_id}
