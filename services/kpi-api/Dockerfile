# syntax=docker/dockerfile:1
# Build from the repo root: docker build -f services/kpi-api/Dockerfile .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REVINTEL_RAW_DIR=/data/raw REVINTEL_PROCESSED_DIR=/data/processed
WORKDIR /app
# Shared library first: it changes less often than the service code, so this layer caches.
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[api,kusto]"
COPY services/kpi-api/kpi_api ./kpi_api
RUN useradd --system --uid 10001 --home-dir /app app \
 && mkdir -p /data/processed && chown -R app /data /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "kpi_api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
