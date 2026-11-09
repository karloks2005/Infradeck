"""What the agent's model calls cost: one row per call (AgentRun), prices per model that an admin enters, a monthly
cap that pauses the agent, and a daily limit on chat messages per person.

Prices are not fetched from the provider: they change, differ per account and aren't in the API, so the admin
copies them from the provider's pricing page. A call to a model without a price still counts its tokens.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from ..backend.models import AgentRun, AppSetting
from .masking import mask_text

PRICES_KEY = "agent_prices"
CAP_KEY = "agent_monthly_cap_usd"
DAILY_LIMIT_KEY = "agent_daily_chat_limit"
DEFAULT_DAILY_CHAT_LIMIT = 100
MAX_DAILY_CHAT_LIMIT = 10000
WARN_SHARE = 0.8
MAX_CAP_USD = 100000
MAX_PRICE_PER_MILLION = 10000
MAX_PRICED_MODELS = 30
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}")


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _setting(db, key: str):
    setting = db.get(AppSetting, key)
    return setting.value if setting else None


def get_prices(db) -> dict[str, dict[str, float]]:
    try:
        return validate_prices(json.loads(_setting(db, PRICES_KEY) or "{}"))
    except ValueError:
        return {}


def validate_prices(raw) -> dict[str, dict[str, float]]:
    """{model: {"input": USD per million tokens, "output": USD per million tokens}}. Raises ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("Prices must be an object of models.")
    if len(raw) > MAX_PRICED_MODELS:
        raise ValueError(f"At most {MAX_PRICED_MODELS} models can have a price.")
    prices = {}
    for model, price in raw.items():
        if not isinstance(model, str) or not MODEL_NAME.fullmatch(model.strip()):
            raise ValueError("A model name may only contain letters, digits and . _ : / -")
        if not isinstance(price, dict) or set(price) != {"input", "output"}:
            raise ValueError(f"The price of {model} needs an input and an output price.")
        values = {}
        for side in ("input", "output"):
            value = price[side]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_PRICE_PER_MILLION:
                raise ValueError(f"The {side} price of {model} must be between 0 and {MAX_PRICE_PER_MILLION} USD per million tokens.")
            values[side] = float(value)
        prices[model.strip()] = values
    return prices


def get_cap(db) -> float | None:
    try:
        value = float(_setting(db, CAP_KEY) or "")
    except ValueError:
        return None
    return value if value > 0 else None


def validate_cap(value) -> float | None:
    """None or 0 means no cap."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_CAP_USD:
        raise ValueError(f"The monthly budget must be between 0 and {MAX_CAP_USD} USD.")
    return float(value) or None


def get_daily_limit(db) -> int:
    try:
        return max(1, min(MAX_DAILY_CHAT_LIMIT, int(_setting(db, DAILY_LIMIT_KEY) or DEFAULT_DAILY_CHAT_LIMIT)))
    except ValueError:
        return DEFAULT_DAILY_CHAT_LIMIT


def validate_daily_limit(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_DAILY_CHAT_LIMIT:
        raise ValueError(f"The daily chat limit must be a whole number between 1 and {MAX_DAILY_CHAT_LIMIT}.")
    return value


def price_for(prices: dict, model: str) -> dict | None:
    """The price of ``model``; a dated name such as gpt-5-mini-2026-01-01 uses the price of gpt-5-mini."""
    if model in prices:
        return prices[model]
    matches = [name for name in prices if model.startswith(f"{name}-")]
    return prices[max(matches, key=len)] if matches else None


def cost_of(prices: dict, model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = price_for(prices, model)
    if price is None:
        return None
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def record(db, *, kind: str, model: str, status: str = "ok", input_tokens: int = 0, output_tokens: int = 0, duration_ms: int = 0,
           user_id: int | None = None, conversation_id: str | None = None, incident_id: int | None = None, error: str | None = None) -> AgentRun:
    run = AgentRun(kind=kind, status=status, model=model[:100], input_tokens=max(0, int(input_tokens or 0)), output_tokens=max(0, int(output_tokens or 0)),
                   cost_usd=cost_of(get_prices(db), model, input_tokens or 0, output_tokens or 0), duration_ms=max(0, int(duration_ms or 0)),
                   user_id=user_id, conversation_id=conversation_id, incident_id=incident_id, error=(error or None) and mask_text(error)[:300])
    db.add(run)
    db.commit()
    return run


def month_cost(db, now: datetime | None = None) -> tuple[float, int]:
    """This month's estimated cost so far, and how many calls had no price to count."""
    now = now or datetime.now(timezone.utc)
    prices = get_prices(db)
    since = AgentRun.created_at >= month_start(now)
    total = float(db.query(func.coalesce(func.sum(AgentRun.cost_usd), 0)).filter(since, AgentRun.cost_usd.isnot(None)).scalar() or 0)
    unpriced = 0
    rows = db.query(AgentRun.model, func.sum(AgentRun.input_tokens), func.sum(AgentRun.output_tokens), func.count(AgentRun.id)) \
        .filter(since, AgentRun.cost_usd.is_(None)).group_by(AgentRun.model).all()
    for model, input_tokens, output_tokens, count in rows:
        later = cost_of(prices, model, input_tokens or 0, output_tokens or 0)
        if later is None:
            unpriced += count if (input_tokens or output_tokens) else 0
        else:
            total += later
    return total, unpriced


def budget(db, now: datetime | None = None) -> dict:
    """{"cost", "cap", "share", "state"}: state is ok, warn (from 80 % of the cap) or paused (the cap is reached)."""
    cost, unpriced = month_cost(db, now)
    cap = get_cap(db)
    share = cost / cap if cap else None
    state = "paused" if share is not None and share >= 1 else "warn" if share is not None and share >= WARN_SHARE else "ok"
    return {"cost": round(cost, 6), "cap": cap, "share": share, "state": state, "unpriced_runs": unpriced}


def month_summary(db, now: datetime | None = None) -> dict:
    """Admin's usage card: totals, per model, and cost per day of this month."""
    now = now or datetime.now(timezone.utc)
    start = month_start(now)
    prices = get_prices(db)
    runs = db.query(AgentRun).filter(AgentRun.created_at >= start).all()
    by_model: dict[str, dict] = {}
    days: dict[str, float] = {}
    totals = {"calls": 0, "errors": 0, "chat_calls": 0, "diagnoses": 0, "input_tokens": 0, "output_tokens": 0}
    for run in runs:
        cost = run.cost_usd if run.cost_usd is not None else cost_of(prices, run.model, run.input_tokens, run.output_tokens)
        totals["calls"] += 1
        totals["errors"] += run.status != "ok"
        totals["chat_calls"] += run.kind == "chat"
        totals["diagnoses"] += run.kind == "diagnosis" and run.status == "ok"
        totals["input_tokens"] += run.input_tokens
        totals["output_tokens"] += run.output_tokens
        entry = by_model.setdefault(run.model, {"model": run.model, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "priced": price_for(prices, run.model) is not None})
        entry["calls"] += 1
        entry["input_tokens"] += run.input_tokens
        entry["output_tokens"] += run.output_tokens
        entry["cost"] += cost or 0
        day = run.created_at.astimezone(timezone.utc).date().isoformat()
        days[day] = days.get(day, 0) + (cost or 0)
    span = (now.date() - start.date()).days + 1
    daily = [{"day": (start + timedelta(days=offset)).date().isoformat(), "cost": round(days.get((start + timedelta(days=offset)).date().isoformat(), 0), 6)} for offset in range(span)]
    return {"month": start.date().isoformat()[:7], **totals, **budget(db, now), "by_model": sorted(by_model.values(), key=lambda entry: -entry["cost"]), "daily": daily,
            "prices": prices, "daily_chat_limit": get_daily_limit(db), "warn_share": WARN_SHARE}
