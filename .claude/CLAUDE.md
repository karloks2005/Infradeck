# Project
A self-hosted dashboard for monitoring all your homelab machines in one place. One machine runs the dashboard; every other machine runs a small collector that reports host and Docker container metrics back to it, and runs the uptime checks you define (HTTP, TCP, TLS, DNS, ping and more). It works the same whether the machines share a LAN or are spread across sites and VPSes.

- Stack:
  - Backend: Python 3.12, FastAPI + Uvicorn, SQLAlchemy + Alembic on PostgreSQL 16 (psycopg), Redis (sessions, rate limits, agent job queue), bcrypt + cryptography/Fernet, openai (optional incident agent). Tests: pytest.
  - Frontend: React 19 + react-router-dom 7, Vite 8, Tailwind CSS v4, oxlint; built with Node 20, served by nginx. Tests: `node --test`.
  - Collectors: Python 3.12, standard library only (collector, docker-proxy, system-helper are single-file modules).
  - Infra: Docker Compose (dev `compose.yml`, server `docker-compose.server.yml`); nginx frontend + collector-gate; optional Traefik and Caddy; signed collector images on GHCR (cosign); CI on GitHub Actions.

- Structure:
  - `src/backend/` — FastAPI app. `main.py` holds the app, middleware, startup, shared helpers and the baseline auth; HTTP routes live in `routes/` (account, admin, auth, availability, chat, incidents, monitoring, statistics). Domain/service modules alongside: `availability.py`, `incidents.py`, `alerts.py`, `rules.py`, `notify.py`, `dashboard.py`, `stats.py`, `history.py`, `rollups.py`, `retention.py`, `auth.py`, `totp.py`, `rate_limit.py`, `secrets_box.py`, `models.py`, `db.py`, `config.py`, `migrations/`.
  - `src/agent/` — optional incident agent: `providers.py`, `jobs.py`, `worker.py`, `diagnosis.py`, `chat.py`, `evidence.py`, `verify.py`, `safety.py`, `masking.py`, `usage.py`, `settings.py`, prompt `.md` files.
  - `src/collector/` — standalone, stdlib-only: `collector.py`, `docker_proxy.py`, `system_helper.py`.
  - `src/frontend/src/` — React, one folder per area: `dashboard/`, `availability/`, `agent/`, `statistics/`, `account/`, `admin/`, `auth/`, `more/`, plus shared `ui.jsx`/`api.js`/`shell.jsx`.
  - `docker/` (Dockerfiles, nginx, Caddyfile), `docs/`, `tests/` (mirror the backend areas), `floorplan/` (codebase map), `design/` (PDF mockups).

# Workflow
- For non-trivial changes, briefly outline the plan before writing code.
- Change only what the task requires; don't refactor or reformat unrelated code.
- Before saying a task is done, run tests, lint and typecheck and fix failures.
- Code, comments and commit messages in English; reply to me in Croatian.

# Ask before
- Committing, pushing or creating branches
- Running or writing database migrations
- Deleting files or data
- Touching `.env*`, CI config, auth logic or deployment config
- Adding any dependency (explain why it's needed, the alternatives, and why
  this one is best)

# Git & GitHub
- One issue = one branch = one PR. Branch name: `<type>/<issue-number>-<short-desc>`
  (e.g. `fix/42-login-redirect`).
- Commit messages: Conventional Commits (`feat:`, `fix:`, `refactor:`...).
- Every PR description must reference its issue with `Fixes #<number>`
  so the issue closes automatically on merge.
- Never close, reopen or relabel issues manually. Never merge PRs yourself.
- If work only partially solves an issue, use `Refs #<number>` instead of
  `Fixes`, and list what's left in the PR description.
- Before opening a PR: tests, lint and typecheck pass; summarize what
  changed and how it was verified.
- Never force-push to shared branches or push directly to `main`.
- Use english as default langauge for writing issues

# Security
- Validate and sanitize all external input (request body, params, headers, files).
- Use parameterized queries / the ORM; never build SQL from strings.
- Check authorization on every endpoint, not just authentication.
- Never hardcode secrets; read them from environment variables.
- Never log passwords, tokens or personal data.
- Don't weaken security settings (e.g. CORS `*`, disabled TLS verification,
  disabled CSRF) to make something work.
- Return generic error messages to clients; keep details in server logs.
- If unsure about a security decision, stop, ask, and explain the trade-offs.

# Code organization
- One file, one responsibility; if a file exceeds ~300 lines, suggest a split.
- Keep functions short and single-purpose.
- Follow the existing folder structure and naming conventions.
- Put new files where a person would naturally look for them.

## Simplicity
- Always choose the simplest solution that correctly solves the problem.
- Don't add abstractions, configuration or features that weren't asked for, in case you think adding abstraction is a better approach propose it to the user and explain why is it better.
- Don't add new dependencies if the problem can be solved with existing ones
  or a few lines of code; if a dependency is necessary, ask first and provide argumented explanation.
- Less code means a smaller attack surface: avoid unnecessary code, exposed
  endpoints, overly broad permissions and complex logic.
- Simpler solution is the default, propose the complex one with explanation