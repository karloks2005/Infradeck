"""The Statistics page's /stats endpoints: fleet-wide stats and per-server container rankings, busiest, CPU spikes
and the hour/weekday heatmap, each cached for a minute. Logic lives in stats.py. The per-server time-series
(history, live, sparklines, events) stay with the monitoring routes in main, where their path order matters."""
from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query

from .. import main, stats
from ..config import SESSION_COOKIE_NAME
from ..db import get_db
from ..models import Server

router = APIRouter()

STATS_CACHE_SECONDS = 60


@router.get("/stats/fleet")
def fleet_statistics(range_name: Annotated[main.RangeName, Query(alias="range")] = "24h", session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Incidents over time and per-server availability for the Statistics page's all-servers view."""
    main._current_user(session_id, db)
    # Keyed only by the validated range, so a request can't create an unbounded number of cache entries.
    cache_key = f"infradeck:stats:fleet:{range_name}"
    try:
        cached = main.redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:  # a cache problem must never break the page
        cached = None
    result = stats.fleet_stats(db, range_name)
    try:
        main.redis_client.set(cache_key, json.dumps(result), ex=STATS_CACHE_SECONDS)
    except Exception:
        pass
    return result

def _cached_server_stats(db, server_id: int, name: str, range_name: str, build) -> dict:
    """A per-server statistic, remembered for a minute. Only existing servers and known ranges get this far, so the
    cache can't grow without limit; a cache problem never breaks the page."""
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    cache_key = f"infradeck:stats:{name}:{server.id}:{range_name}"
    try:
        cached = main.redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    result = build(db, server, range_name)
    try:
        main.redis_client.set(cache_key, json.dumps(result), ex=STATS_CACHE_SECONDS)
    except Exception:
        pass
    return result

@router.get("/stats/servers/{server_id}/containers")
def server_container_usage(server_id: main.PathId, range_name: Annotated[main.RangeName, Query(alias="range")] = "24h", session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """Every container of a server over a range, busiest first, for the Statistics page's ranked table."""
    main._current_user(session_id, db)
    return _cached_server_stats(db, server_id, "containers", range_name, stats.container_usage)

@router.get("/stats/servers/{server_id}/busiest")
def server_busiest_containers(server_id: main.PathId, range_name: Annotated[main.RangeName, Query(alias="range")] = "24h", session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """The container using the most CPU in each period of a range, for the timeline's tooltip."""
    main._current_user(session_id, db)
    return _cached_server_stats(db, server_id, "busiest", range_name, stats.busiest_containers)

@router.get("/stats/servers/{server_id}/spikes")
def server_cpu_spikes(server_id: main.PathId, range_name: Annotated[main.RangeName, Query(alias="range")] = "24h", session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db)):
    """When the server's CPU was at or above 90% and for how long, for the Statistics page's findings."""
    main._current_user(session_id, db)
    return _cached_server_stats(db, server_id, "spikes", range_name, stats.cpu_spikes)

@router.get("/stats/servers/{server_id}/heatmap")
def server_heatmap(
    server_id: main.PathId, metric: Literal["cpu", "ram"] = "cpu", offset: Annotated[int, Query(ge=-stats.MAX_OFFSET_MINUTES, le=stats.MAX_OFFSET_MINUTES)] = 0,
    session_id: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None, db=Depends(get_db),
):
    """When a server is busy: its average per hour of the day and weekday over the last 30 days."""
    main._current_user(session_id, db)
    server = db.query(Server).filter(Server.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found.")
    # Only existing servers, known metrics and a bounded offset get this far, so the cache can't grow without limit.
    cache_key = f"infradeck:stats:heatmap:{server.id}:{metric}:{offset}"
    try:
        cached = main.redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    result = stats.hour_weekday_heatmap(db, server, metric, offset)
    try:
        main.redis_client.set(cache_key, json.dumps(result), ex=STATS_CACHE_SECONDS)
    except Exception:
        pass
    return result
