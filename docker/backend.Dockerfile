FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

# Runs as an unprivileged user with a fixed UID; the code is owned by root, so it can't change it.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin infradeck
USER 10001

EXPOSE 8000

# The API is only published on 127.0.0.1 and reached through the frontend's nginx,
# which sets X-Forwarded-For, so trusting proxy headers here is safe.
CMD ["uvicorn", "src.backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]