import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://homelab:homelab_dev@localhost:5432/homelab",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
AGENT_MODEL = os.getenv("AGENT_MODEL", "models/gemini-3.5-flash")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "homie_session")
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
# When false, only the first account can be created (so a fresh install can still be set up).
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "true").lower() == "true"
LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "5"))
LOGIN_RATE_WINDOW_SECONDS = int(os.getenv("LOGIN_RATE_WINDOW_SECONDS", "60"))
REGISTER_RATE_LIMIT = int(os.getenv("REGISTER_RATE_LIMIT", "3"))
REGISTER_RATE_WINDOW_SECONDS = int(os.getenv("REGISTER_RATE_WINDOW_SECONDS", "3600"))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5176").split(",")
    if origin.strip()
]
