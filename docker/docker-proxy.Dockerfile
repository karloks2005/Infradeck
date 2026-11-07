# Read-only Docker API proxy: the only container that mounts the Docker socket.
# Standard library only. See src/collector/docker_proxy.py for exactly what it allows.
FROM python:3.12.16-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7

WORKDIR /app
COPY src/collector/docker_proxy.py ./docker_proxy.py

ENV DOCKER_SOCKET_PATH=/var/run/docker.sock \
    INFRADECK_DOCKER_PROXY_SOCKET=/run/docker-proxy/docker.sock \
    PYTHONUNBUFFERED=1

USER 0:0
CMD ["python", "/app/docker_proxy.py"]
