[← Documentation](README.md)

# Homie assistant (monitoring-only)

The assistant has no code path for running shell commands. It may request
exactly one fixed, read-only check at a time: `server_health`,
`container_health`, or `daily_checkup`, implemented with Python system APIs and
Docker's Unix API socket, never the shell or Docker CLI.

The backend container does **not** mount `/var/run/docker.sock`. If you want
the assistant's container checks, point `DOCKER_SOCKET_PATH` at a hardened
Docker API proxy that permits only the required GET endpoints.

`daily_checkup` combines host and container checks. On a critical finding it
POSTs an alert only to `ADMIN_ALERT_WEBHOOK_URL`; without it, the result says
`not_configured` and no request is made. Schedule it from a trusted scheduler
(e.g. a systemd timer) rather than letting the agent create schedules.
