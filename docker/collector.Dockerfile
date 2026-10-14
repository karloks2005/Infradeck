# Standalone collector image: the collector uses only the Python standard library.
FROM python:3.12-alpine

WORKDIR /app
COPY src/collector/collector.py ./collector.py

ENV INFRADECK_STATE_DIR=/var/lib/infradeck-collector \
    HOST_PROC=/host/proc \
    HOST_ROOT=/hostfs \
    DOCKER_SOCKET_PATH=/var/run/docker.sock \
    PYTHONUNBUFFERED=1

VOLUME ["/var/lib/infradeck-collector"]

CMD ["python", "/app/collector.py"]
