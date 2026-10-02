# Optional system helper: reads the kernel log and wtmp, publishes a sanitized summary.
# Standard library only. Run it with only CAP_SYSLOG, no network and a read-only filesystem.
FROM python:3.12-alpine

WORKDIR /app
COPY src/agent/system_helper.py ./system_helper.py

ENV HOST_PROC=/proc \
    HOMELAB_WTMP_PATH=/host/wtmp \
    HOMELAB_HELPER_OUTPUT=/out/system.json \
    PYTHONUNBUFFERED=1

# Nothing to write to except the shared output volume.
USER 0:0
CMD ["python", "/app/system_helper.py"]
