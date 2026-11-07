# Standalone collector image: the collector uses only the Python standard library.
FROM python:3.12.16-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7

WORKDIR /app
COPY src/collector/collector.py ./collector.py
COPY VERSION ./VERSION

ENV INFRADECK_STATE_DIR=/var/lib/infradeck-collector \
    HOST_PROC=/host/proc \
    HOST_ROOT=/hostfs \
    DOCKER_SOCKET_PATH=/var/run/docker.sock \
    PYTHONUNBUFFERED=1

VOLUME ["/var/lib/infradeck-collector"]

CMD ["python", "/app/collector.py"]
