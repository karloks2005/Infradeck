# homelab-agent

## Frontend setup

This project needs Node 20 for the Vite frontend.

1. Install/use the correct Node version:
   ```bash
   cd /media/karlo/data/Documents/programming/homelab-agent
   nvm install
   nvm use
   ```
2. Start the app:
   ```bash
   cd /media/karlo/data/Documents/programming/homelab-agent/src/frontend
   npm install
   npm run dev -- --host 0.0.0.0
   ```
3. Open the app at:
   ```text
   http://localhost:5176/
   ```

## Backend setup

The backend API is in `src/backend` and supports user registration/login using a database and Redis-style session storage.

### Current dev behavior

In this environment, Docker access is blocked by local permissions, so the API can still run with a SQLite fallback for development while the real PostgreSQL + Redis stack remains configured in the project.

```bash
cd /media/karlo/data/Documents/programming/homelab-agent
source venv/bin/activate
DATABASE_URL=sqlite:///./dev.db REDIS_URL=redis://localhost:6379/0 uvicorn src.backend.main:app --host 0.0.0.0 --port 8000
```

### Real Postgres + Redis setup

This project includes a Docker Compose config for the real stack:

```bash
cd /media/karlo/data/Documents/programming/homelab-agent
docker compose up -d
```

Use the values already defined in the project `.env` file for the real stack:

```env
DATABASE_URL=postgresql+psycopg://homelab:homelab_dev@localhost:5432/homelab
REDIS_URL=redis://localhost:6379/0
```

The `users` table is initialized in `docker/postgres/init/01_create_users.sql`.

## Useful Redis ideas right now

Redis is best used for:
- user sessions
- rate limiting
- temporary dashboard cache
- task status or queue markers
- short-lived flags and counters

PostgreSQL remains the source of truth for durable data such as users, logs, and analytics.
