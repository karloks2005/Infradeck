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

Node 24 (`.nvmrc`). The Vite dev server proxies `/api` to `localhost:8060` (set `BACKEND_URL` to point it elsewhere),
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
helpers have their own tests that need only Node 24 or newer: `cd src/frontend && node --test`. See
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

## Dependencies

Every dependency is pinned to an exact version, so a rebuild installs the same set until a pin changes: Python
packages in `requirements.txt` and `requirements-dev.txt`, the frontend in `src/frontend/package-lock.json`, base
images by tag and digest in `docker/*.Dockerfile` and the compose files, GitHub Actions by commit.

Dependabot (`.github/dependabot.yml`) opens one grouped pull request per ecosystem every Monday. Merge it once CI
passes. It skips jumps that need a decision: a new PostgreSQL, Redis or Caddy major, another Python minor, a new
Node major. Make those by hand, in their own pull request.

To add, remove or upgrade a Python package, edit `requirements.in` (`requirements-dev.in` for test-only tools)
and compile both lock files again. Run it in the backend's base image, so the versions resolve for Linux and
Python 3.12:

```bash
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/src -w /src python:3.12-slim sh -c '
  pip install -q --user pip-tools && export PATH=/tmp/.local/bin:$PATH &&
  pip-compile -q --strip-extras --no-emit-index-url -o requirements.txt requirements.in &&
  pip-compile -q --strip-extras --no-emit-index-url -o requirements-dev.txt requirements-dev.in'
```

Add `--upgrade-package <name>` to both `pip-compile` commands to move one package, or `--upgrade` for all of
them. To pin a new base image, take the digest from `docker buildx imagetools inspect <image>:<tag>` and write
`<image>:<tag>@<digest>`. Check for known vulnerabilities with `pip-audit -r requirements-dev.txt` and, in
`src/frontend`, `npm audit && npm audit signatures`.

## Project layout

```text
compose.yml                 development stack (default for `docker compose`)
docker-compose.server.yml   server stack behind your reverse proxy
deploy/                     ready-made additions for a reverse proxy (deploy/traefik.yml)
collector-compose.yml       collector-only stack for monitored machines
README.md                   what it is and how to start it (the short version)
docs/                       the full documentation (start at docs/README.md)
SECURITY.md                 what each component can touch, and its limits
TESTS.md                    how to run the tests and what each one checks
requirements.in             the app's direct Python dependencies (unpinned)
requirements.txt            every Python package, pinned; compiled from requirements.in
requirements-dev.in/.txt    the same plus pytest, for running the tests
pytest.ini                  test settings (lets plain `pytest` find the `src` package)
alembic.ini                 settings for the `alembic` command (writing new migrations)
.github/dependabot.yml      weekly pull requests that move the pins forward
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
    providers/              the language model behind one interface (answers and tool calls): Claude, OpenAI, Gemini; the choice and its encrypted key
    chat.py, prompts.py, SYSTEM_PROMPT.md  the incident chat and its instructions
    investigate.py          the look-up loop of a diagnosis or chat message: tools within a budget, each call audited
    tools/                  the read-only look-ups the agent may make (metrics, containers, events, checks, network)
    steps.py                each look-up in plain words for the page (no model involved)
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
