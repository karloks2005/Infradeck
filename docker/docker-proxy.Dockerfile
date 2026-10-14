# Read-only Docker API proxy: the only container that mounts the Docker socket.
# Standard library only. See src/agent/docker_proxy.py for exactly what it allows.
FROM python:3.12-alpine

WORKDIR /app
COPY src/agent/docker_proxy.py ./docker_proxy.py

ENV DOCKER_SOCKET_PATH=/var/run/docker.sock \
    INFRADECK_DOCKER_PROXY_SOCKET=/run/docker-proxy/docker.sock \
    PYTHONUNBUFFERED=1

USER 0:0
CMD ["python", "/app/docker_proxy.py"]
