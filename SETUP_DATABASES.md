# Local database setup

This project includes a simple local PostgreSQL + Redis stack for development.

## Start services

```bash
cd /media/karlo/data/Documents/programming/homelab-agent
docker compose up -d
```

## Useful connections

- PostgreSQL: `localhost:5432`
- Redis: `localhost:6379`

## PostgreSQL credentials

- Database: `homelab`
- User: `homelab`
- Password: `homelab_dev`

## Quick checks

```bash
# PostgreSQL readiness
docker compose exec postgres pg_isready -U homelab -d homelab

# Check the users table
docker compose exec postgres psql -U homelab -d homelab -c "SELECT id, email, role FROM users;"

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
