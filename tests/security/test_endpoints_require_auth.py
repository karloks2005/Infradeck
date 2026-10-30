"""Every route must reject a request that carries no session and no token — a guard against adding an endpoint
and forgetting its auth check.

Auth in this app is done inside each handler (``_current_user``, ``_require_admin``, ``_require_operator``,
``_account_session``, or a collector token), not as a FastAPI dependency, so nothing stops a new endpoint from
forgetting it. This test walks every registered route and asserts that, with no cookie and no token, the response
is **not** a success (2xx). The genuinely public routes (sign-in, registration, logout, the sign-in page's config,
health) are listed explicitly; adding a new public route means adding it here on purpose.

Limitation: a route whose body fails validation before the handler runs answers 422 here, which this test accepts
as "rejected". So this catches a route that returns 2xx with no auth, not every shape of mistake; the stronger,
framework-level default-deny is tracked separately (Option B).
"""
import os
import re

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.backend import main
from src.backend.models import Base
from tests.fakes import FakeRedis

# Routes that are allowed to answer without a session, by design. Each is public for a reason:
PUBLIC = {
    ("GET", "/"),                       # a static "Infradeck API" banner, no data
    ("GET", "/health"),
    ("GET", "/auth/config"),            # the sign-in page reads this before anyone signs in
    ("POST", "/auth/register"),         # guarded by a setup or invite token, checked inside
    ("POST", "/auth/login"),            # the sign-in itself
    ("POST", "/auth/login/2fa"),        # second sign-in step, guarded by a single-use pending-login token
    ("POST", "/auth/logout"),           # only clears the caller's own cookie
}
# FastAPI's own and non-app routes we don't police here.
SKIP_PREFIXES = ("/openapi", "/docs", "/redoc")
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _routes():
    # The OpenAPI schema lists every registered path and method, including those mounted from routers/* (which
    # aren't flattened into app.routes in this FastAPI build). That makes this check cover the whole API.
    seen = []
    for path, operations in main.app.openapi().get("paths", {}).items():
        if path.startswith(SKIP_PREFIXES):
            continue
        for method in operations:
            if method.upper() in METHODS:
                seen.append((method.upper(), path))
    return seen


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def get_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[main.get_db] = get_db
    monkeypatch.setattr(main, "redis_client", FakeRedis())   # empty: no session exists for anyone
    yield TestClient(main.app, raise_server_exceptions=False)
    main.app.dependency_overrides.clear()


def test_every_route_is_either_public_or_rejects_an_unauthenticated_request(client):
    leaks = []
    for method, path in _routes():
        if (method, path) in PUBLIC:
            continue
        request = re.sub(r"\{[^}]+\}", "1", path)   # /servers/{id} -> /servers/1
        response = client.request(method, request, json={})
        if 200 <= response.status_code < 300:
            leaks.append(f"{method} {path} -> {response.status_code} without a session or token")
    assert not leaks, "Endpoints that answered an unauthenticated request with success:\n" + "\n".join(leaks)


def test_the_public_list_only_names_routes_that_exist():
    # Keeps the allow-list honest: a renamed/removed public route must be noticed here, not silently ignored.
    everything = set(_routes())
    missing = [entry for entry in PUBLIC if entry not in everything]
    assert not missing, f"PUBLIC names routes that no longer exist: {missing}"


def test_public_routes_are_session_exempt():
    # A route the security policy treats as public must also be exempt from the baseline, or it would be blocked.
    for _method, path in PUBLIC:
        assert path in main.SESSION_EXEMPT_PATHS, f"{path} is public but not in SESSION_EXEMPT_PATHS"


def test_the_baseline_gate_blocks_non_exempt_paths_without_a_session(monkeypatch):
    # require_session is the default-deny gate (Option B): with no session it raises on a non-exempt path and
    # passes on an exempt one, before any handler runs.
    from types import SimpleNamespace

    from fastapi import HTTPException

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "redis_client", FakeRedis())   # empty: nobody has a session
    with sessionmaker(bind=engine)() as db:
        request = SimpleNamespace(url=SimpleNamespace(path="/availability"))
        with pytest.raises(HTTPException) as caught:
            main.require_session(request, session_id=None, db=db)
        assert caught.value.status_code in (401, 403)
        # an exempt path is let through without a session
        main.require_session(SimpleNamespace(url=SimpleNamespace(path="/health")), session_id=None, db=db)
