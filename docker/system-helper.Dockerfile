# Optional system helper: reads the kernel log and wtmp, publishes a sanitized summary.
# Standard library only. Run it with only CAP_SYSLOG, no network and a read-only filesystem.
FROM python:3.12.16-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7

WORKDIR /app
COPY src/collector/system_helper.py ./system_helper.py

ENV HOST_PROC=/proc \
    INFRADECK_WTMP_PATH=/host/wtmp \
    INFRADECK_HELPER_OUTPUT=/out/system.json \
    PYTHONUNBUFFERED=1

# Nothing to write to except the shared output volume.
USER 0:0
CMD ["python", "/app/system_helper.py"]
