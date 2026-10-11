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
