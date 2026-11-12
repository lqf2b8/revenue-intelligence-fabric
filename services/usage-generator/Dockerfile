# syntax=docker/dockerfile:1
# Build from the repo root: docker build -f services/usage-generator/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REVINTEL_RAW_DIR=/data/raw REVINTEL_PROCESSED_DIR=/data/processed
WORKDIR /app
# Shared library first: it changes less often than the service code, so this layer caches.
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[kusto,eventhub]"
COPY data/raw /data/raw
COPY services/usage-generator/usage_generator ./usage_generator
RUN useradd --system --uid 10001 --home-dir /app app \
 && mkdir -p /data/processed && chown -R app /data /app
USER app
ENTRYPOINT ["python", "-m", "usage_generator.main"]
