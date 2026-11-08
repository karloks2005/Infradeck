# Documentation

The [README](../README.md) is the short version: what the project is, how to start it. Everything else lives here.

## Using the dashboard

| Page | What is in it |
|---|---|
| [The dashboard and system health](dashboard.md) | Fleet cards, storage bars, the Network and System rows, active incidents, the optional system helper. |
| [Statistics](statistics.md) | History from the last hour to all time: servers, containers and the whole fleet. |
| [Metrics: what every number means](metrics.md) | CPU %, load, memory, swap, disk, inodes, network, container and system figures: how each is measured and its limits. |
| [Alerts and incidents](alerts.md) | Every rule, its default thresholds, and how incidents open and resolve. |
| [Accounts, roles and two-factor sign-in](accounts.md) | Passwords, roles, the Account page, authenticator codes, recovery codes, locked-out accounts. |
| [Incident agent](assistant.md) | The optional agent: diagnoses, watching the fix, notifications, chat, who sees what, and what it costs. |
| [How the agent investigates](agent-investigation.md) | The agent's look-ups end to end: what the model sees, who writes each part of what you see, the budget, what is checked and stored, and the limits. |

## Installing and running

| Page | What is in it |
|---|---|
| [The containers and why each one exists](containers.md) | Every container in the stack: what it does, how it works, why it is separate, and what happens when it stops. |
| [Deploying on a server](deployment.md) | The server stack behind your reverse proxy (ready-made for Traefik), the VPN collector gate, publishing the collector images, updating, other proxies. |
| [Deploy Infradeck behind Traefik](deploy-traefik.md) | Step by step on a server that already runs Traefik: `.env`, first start, admin account, verifying, upgrading and rolling back, common errors. |
| [Adding servers](adding-servers.md) | **Connect a server**, what its command does, how registration and approval work, networking. |
| [Configuration](configuration.md) | Every `.env` setting for the stack, the collector and the frontend build. |
| [Troubleshooting](troubleshooting.md) | Symptoms and what to check. |

## For developers

| Page | What is in it |
|---|---|
| [Development](development.md) | Running the backend and frontend without Docker, tests, project layout. |
| [Database schema](database.md) | Tables, summarised history, indexes and migrations. |
| [API overview](api.md) | Every endpoint, who may call it, and what it returns. |
| [../TESTS.md](../TESTS.md) | How the tests work, what they don't cover, and what each one checks. |
| [../SECURITY.md](../SECURITY.md) | What every component can touch, and the limits of each protection. |
