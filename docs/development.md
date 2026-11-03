[← Documentation](README.md)

# Development

# Development

## Backend without Docker

Python 3.12. The backend needs Redis for sessions and rate limits; SQLite works
in place of PostgreSQL.

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
docker run -d --name infradeck-dev-redis -p 127.0.0.1:6379:6379 redis:7-alpine   # if Redis isn't running (no password: for local development only)
DATABASE_URL=sqlite:///./dev.db REDIS_URL=redis://localhost:6379/0 \
  uvicorn src.backend.main:app --reload --host 0.0.0.0 --port 8060
```

## Frontend dev server

Node 20 (`.nvmrc`). The Vite dev server proxies `/api` to `localhost:8060` (set `BACKEND_URL` to point it elsewhere),
matching the production nginx setup.

```bash
nvm install && nvm use
cd src/frontend
npm install
npm run dev -- --host 0.0.0.0
```

Vite prints the URL (5173, or the next free port if the Compose stack already
uses it). `./start_frontend.sh` does the same in one step.


After changing frontend or collector code, rebuild the containers:
`docker compose up -d --build`. Otherwise the browser keeps getting the old bundle.

## Tests

```bash
source venv/bin/activate
pip install -r requirements-dev.txt   # the app's requirements plus pytest
pytest
```

No Docker, PostgreSQL or Redis is needed; the suite takes about two minutes. The chart
helpers have their own tests that need only Node 20 or newer: `cd src/frontend && node --test`. See
[TESTS.md](../TESTS.md) for how the tests work, what they don't cover, and what every
test is for.

## Database migrations

Change the models in `src/backend/models.py`, then generate a revision against a database that is at the
latest revision (the development stack's PostgreSQL, or any empty database the backend has started on once):

```bash
DATABASE_URL=postgresql+psycopg://infradeck:<password>@127.0.0.1:5432/infradeck alembic revision --autogenerate -m "add servers.location"
```

Read the generated file in `src/backend/migrations/versions/` before committing it: Alembic can't tell a
renamed column from a dropped and added one, and data changes (backfills) have to be written by hand. Name
revisions with the next number (`--rev-id 0002`). The backend applies new revisions at its next start;
`alembic upgrade head` and `alembic downgrade -1` do it by hand. `tests/history/test_migrations.py` fails when
the models and the revisions describe different schemas; set `MIGRATION_TEST_POSTGRES_URL` to an empty
PostgreSQL database to run it there as well as on SQLite.

## Project layout

```text
compose.yml                 development stack (default for `docker compose`)
docker-compose.server.yml   server stack behind Traefik
collector-compose.yml       collector-only stack for monitored machines
README.md                   what it is and how to start it (the short version)
docs/                       the full documentation (start at docs/README.md)
SECURITY.md                 what each component can touch, and its limits
TESTS.md                    how to run the tests and what each one checks
requirements-dev.txt        app requirements plus pytest, for running the tests
pytest.ini                  test settings (lets plain `pytest` find the `src` package)
alembic.ini                 settings for the `alembic` command (writing new migrations)
.github/workflows/
  images.yml                builds, signs and publishes the collector images on a version tag
docker/
  backend.Dockerfile        FastAPI backend (uvicorn)
  frontend.Dockerfile       Vite build served by nginx
  collector.Dockerfile      standalone collector image
  docker-proxy.Dockerfile   read-only Docker API proxy image
  system-helper.Dockerfile  optional kernel-log / wtmp helper image
  nginx.conf                development: serves the UI, proxies /api to the backend
  nginx.strict.conf         server stack: public listener, collector endpoints refused
  nginx.collector.conf      server stack: collector gate (VPN and internal listeners)
  Caddyfile                 optional HTTPS front door (dev compose, --profile https)
src/
  backend/                  API: auth, monitoring, enrollment, rate limits, models
    alerts.py               alert engine: one incident per rule, auto-resolve
    rules.py                alert rules and default thresholds
    rollups.py              summarises check-ins into hourly/daily/weekly/monthly rollups
    migrate.py              runs the database migrations at start
    migrations/             Alembic revisions (versions/); 0001 is the baseline
    history.py              range-aware history behind the Statistics page
    stats.py                fleet statistics: incidents over time, availability, busy-hours heatmap
    totp.py                 one-time codes and recovery codes (standard library only)
    secrets_box.py          encrypts stored secrets with SECRETS_KEY; also prints a new key
    reset_2fa.py            command-line two-factor reset for a locked-out account
  agent/                    the incident agent; runs in the backend (chat) and the agent-worker container
    providers.py            the language model behind one interface (OpenAI)
    chat.py, prompts.py, SYSTEM_PROMPT.md  the incident chat and its instructions
    health_checks.py        the fixed, read-only checks the agent may ask for
    jobs.py                 job queue in Redis: published after an incident opens or worsens
    worker.py               the agent-worker container: runs the queued jobs
    usage.py                tokens, cost per model, monthly budget, daily chat limit
  collector/                runs on every monitored machine, each file its own small image (standard library only)
    collector.py            metrics collector
    docker_proxy.py         read-only Docker API proxy
    system_helper.py        optional kernel-log / wtmp / port-owners helper
  frontend/                 React + Tailwind dashboard (Vite); charts.jsx and chartMath.js are the
                            charts (distributions.jsx: columns, donut, bars, heatmap), StatisticsPage.jsx,
                            FleetStatistics.jsx, ServerInsights.jsx and ContainerStatistics.jsx the page
tests/                      pytest suite, in folders by area (accounts, collectors, monitoring, history, assistant, security); see TESTS.md
```
