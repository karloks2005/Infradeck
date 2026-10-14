# Local database setup

This project includes a simple local PostgreSQL + Redis stack for development.

## Start services

```bash
cd /media/karlo/data/Documents/programming/Infradeck
docker compose up -d
```

## Useful connections

- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`

## PostgreSQL credentials

- Database: `infradeck`
- User: `infradeck`
- Password: `infradeck_dev`

## Quick checks

```bash
# PostgreSQL readiness
docker compose exec postgres pg_isready -U infradeck -d infradeck

# Check the users table
docker compose exec postgres psql -U infradeck -d infradeck -c "SELECT id, email, role FROM users;"

# Redis ping
docker compose exec redis redis-cli ping
```

## What Redis is good for right now

For a simple starter, Redis can handle:

- temporary session storage
- request rate limiting
- simple caching of dashboard data
- short-lived job/task status
- in-memory flags or locks

It is not the place for your main relational data model.
For users, roles, permissions, and long-term analytics, PostgreSQL should still be the source of truth.


## Upgrading an install from before the rename to Infradeck

Older installs created the database and its user as `homelab`. The compose files now default to `infradeck`,
so after `git pull` either keep the old names by adding these two lines to `.env`:

```
POSTGRES_DB=homelab
POSTGRES_USER=homelab
```

or rename them once (back up first; the backend and collector must be stopped so nothing is connected):

```bash
docker compose exec -T postgres pg_dump -U homelab -d homelab -Fc > before-rename.dump
docker compose stop backend collector
docker compose exec -T postgres psql -U homelab -d postgres -c "CREATE ROLE infradeck_rename SUPERUSER LOGIN;" -c "ALTER DATABASE homelab RENAME TO infradeck;"
docker compose exec -T postgres psql -U infradeck_rename -d postgres -c "ALTER ROLE homelab RENAME TO infradeck;"
docker compose exec -T postgres psql -U infradeck -d postgres -c "DROP ROLE infradeck_rename;"
docker compose up -d --build
```

The password stays the same (it is stored as SCRAM, which a rename keeps). Redis keys under the old `homelab:` prefix
move to `infradeck:` on the backend's next start, so nobody is signed out. Collectors already installed on other
machines keep working: their `HOMELAB_*` variables and their old state folder are still read.
