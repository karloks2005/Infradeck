"""Settings shared by every test."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest

from src.backend import main


@pytest.fixture(autouse=True)
def _no_ingest_spacing(monkeypatch):
    """Tests send check-ins back to back, so the minimum spacing between check-ins is off unless a test turns it on."""
    monkeypatch.setattr(main, "INGEST_MIN_SPACING_RATIO", 0)


@pytest.fixture(autouse=True)
def _no_real_model_calls(monkeypatch):
    """The backend reads .env, which may hold a real OPENAI_API_KEY. No test may reach the provider with it: tests
    that need a key set a fake one and replace the provider."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
