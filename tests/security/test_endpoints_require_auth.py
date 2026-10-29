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
    seen = []
    for route in main.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or path.startswith(SKIP_PREFIXES):
            continue
        for method in sorted(methods & METHODS):
            seen.append((method, path))
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
