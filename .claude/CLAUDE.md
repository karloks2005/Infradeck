# Project
infradeck: a self-hosted dashboard for monitoring homelab machines. One machine runs the dashboard; every other machine runs a small collector that reports host and Docker container metrics and runs user-defined uptime checks (HTTP, TCP, TLS, DNS, ping…). Works the same on a LAN or across sites and VPSes.

## Stack
- Backend: Python 3.12, FastAPI + Uvicorn, SQLAlchemy + Alembic, PostgreSQL 16 (psycopg), Redis (sessions, rate limits, agent job queue). Tests: pytest.
- Frontend: React 19, react-router-dom 7, Vite 8, Tailwind CSS v4, oxlint, Node 24, served by nginx. Tests: `node --test`.
- Collectors: Python 3.12, standard library only.
- Infra: Docker Compose, nginx (frontend + collector-gate), optional Traefik/Caddy, signed collector images on GHCR (cosign), GitHub Actions CI.

## Where things live
- `src/backend/main.py`: app, middleware, startup, baseline auth. HTTP routes go in `src/backend/routes/`; domain logic in the modules next to it.
- `src/agent/`: optional incident agent (LLM). Prompts are `.md` files there.
- `src/collector/`: standalone single-file modules.
- `src/frontend/src/`: one folder per area; shared code in `ui.jsx`, `api.js`, `shell.jsx`.
- `tests/`: layout and conventions are documented in `TESTS.md`; read it before adding tests. `floorplan/` is the codebase map: check it before searching blindly.

## Commands
<!-- TODO: replace with the real commands -->
- Dev stack: `docker compose up`
- Backend tests: `pytest tests/`
- Frontend tests + lint: `cd src/frontend && node --test && npx oxlint`
- New migration: `alembic revision --autogenerate -m "<message>"` (review the generated file before applying)

# Workflow
- For non-trivial changes, outline the plan briefly before writing code.
- Change only what the task requires; don't refactor or reformat unrelated code.
- Before saying a task is done, run the tests and lint for the areas you touched and fix failures.
- Code, comments, commit messages, issues and PR descriptions in English. Reply to me in Croatian.

# Ask before
- Committing, pushing, creating branches or opening PRs
- Writing or running database migrations
- Deleting files or data
- Touching `.env*`, CI config, auth logic or deployment config
- Adding any dependency: explain why it's needed, the alternatives (including a few lines of our own code), and why this one is best

# Hard rules
- `src/collector/` stays standard library only. Never add third-party packages there.
- Never push directly to `main`, never force-push shared branches, never merge PRs without approval.
- Never close, reopen or relabel issues manually or without approval.
- Anything sent to an LLM provider from `src/agent/` goes through `masking.py` first.

# Git & GitHub
- One issue = one branch = one PR. Branch: `<type>/<issue-number>-<short-desc>` (e.g. `fix/42-login-redirect`).
- Commits: Conventional Commits (`feat:`, `fix:`, `refactor:`, `docs:`, `chore:` …).
- PR description: summary of changes, how it was verified, and `Fixes #<n>`. If the issue is only partly solved, use `Refs #<n>` and list what's left.
- Open issues may be edited. Never edit a closed issue; if closed work needs a
  follow-up or new scope, open a new issue and reference the old one (`Related: #<number>`).
- Always check if there are new issues that can be resolved/closed after commiting and pushing changes.

# Security
- Validate all external input (body, params, headers, files) with Pydantic models / explicit checks.
- Use the ORM or parameterized queries; never build SQL from strings.
- Check authorization on every endpoint, not just authentication. <!-- TODO: name the dependency/helper in main.py that does this -->
- Secrets stored in the DB are encrypted via `secrets_box.py`; secrets from config come from environment variables, never hardcoded.
- Never log passwords, tokens or personal data.
- Don't weaken security settings (CORS `*`, disabled TLS verification, disabled CSRF) to make something work.
- Return generic errors to clients; keep details in server logs.
- If unsure about a security decision, stop and explain the trade-offs.

# Code style
- Choose the simplest solution that correctly solves the problem. Less code = smaller attack surface.
- Don't add abstractions, config or features nobody asked for. If you think a more complex approach is better, propose it and explain why. Don't just do it.
- Keep functions short and single-purpose; one file, one responsibility.
- If a file you create or substantially change goes past ~300 lines, suggest a split.
- Follow the existing structure and naming; put new files where a person would look for them.