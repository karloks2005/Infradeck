import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

# Shown in the sidebar and compared with what each collector reports.
APP_VERSION = "0.9.2"

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://homelab:homelab_dev@localhost:5432/homelab",
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
AGENT_MODEL = os.getenv("AGENT_MODEL", "models/gemini-3.5-flash")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "homie_session")
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
# When false (the default), only the first account can be created, so a fresh install can still be set up and
# nobody else can sign themselves up. Set to true only if everyone who can reach the dashboard may get an account.
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "false").lower() == "true"
# Interactive API documentation (/docs, /openapi.json). Off unless asked for.
ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"
LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "5"))
LOGIN_RATE_WINDOW_SECONDS = int(os.getenv("LOGIN_RATE_WINDOW_SECONDS", "60"))
REGISTER_RATE_LIMIT = int(os.getenv("REGISTER_RATE_LIMIT", "3"))
REGISTER_RATE_WINDOW_SECONDS = int(os.getenv("REGISTER_RATE_WINDOW_SECONDS", "3600"))
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "").split(",")
    if origin.strip()
]

# "strict" makes the collector endpoints answer only requests that arrived through the VPN or
# internal listener (see docker/nginx.strict.conf); "open" keeps the single-listener behaviour.
COLLECTOR_ACCESS = os.getenv("COLLECTOR_ACCESS", "open").lower()
# What the Connect a server panel shows: the collector listener's address on your VPN, and the
# pinned images remote hosts install. All optional.
COLLECTOR_PUBLIC_URL = os.getenv("COLLECTOR_PUBLIC_URL", "").strip().rstrip("/")
COLLECTOR_IMAGE = os.getenv("COLLECTOR_IMAGE", "").strip()
DOCKER_PROXY_IMAGE = os.getenv("DOCKER_PROXY_IMAGE", "").strip()
JOIN_TOKEN_TTL_SECONDS = int(os.getenv("JOIN_TOKEN_TTL_SECONDS", "900"))

# A collector that reports much more often than its check interval is broken or hostile: it fills the database
# and the history queries. A check-in must arrive at least this share of the interval after the previous one
# (and never sooner than the minimum below). Set the ratio to 0 to switch the limit off.
INGEST_MIN_SPACING_RATIO = float(os.getenv("INGEST_MIN_SPACING_RATIO", "0.25"))
INGEST_MIN_SPACING_SECONDS = int(os.getenv("INGEST_MIN_SPACING_SECONDS", "5"))
# The most servers an install will hold, whichever way they are added (enrollment, approval or by hand).
MAX_SERVERS = int(os.getenv("MAX_SERVERS", "200"))
# Enrollments per address (and twice that per key) per hour. Raise it if many collectors share one address.
ENROLL_RATE_LIMIT = int(os.getenv("ENROLL_RATE_LIMIT", "30"))

# Creating the first account (which becomes the admin) needs a setup token, so nobody else can claim a
# fresh install. Leave unset to have one generated and printed in the backend log at every start until
# the first account exists; set it to choose your own.
ADMIN_BOOTSTRAP_TOKEN = os.getenv("ADMIN_BOOTSTRAP_TOKEN", "").strip()
