# syntax=docker/dockerfile:1
# Build from the repo root: docker build -f services/salesforce-loader/Dockerfile .
# Loads the Maven CRM sample into a Salesforce org. Auth: SF_AUTH_URL (see entrypoint.sh).
FROM node:22-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    REVINTEL_RAW_DIR=/data/raw REVINTEL_PROCESSED_DIR=/data/processed \
    SF_DISABLE_TELEMETRY=true SF_AUTOUPDATE_DISABLE=true PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/* \
 && npm install --global @salesforce/cli && npm cache clean --force \
 && python3 -m venv /opt/venv
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .
COPY data/raw /data/raw
COPY salesforce ./salesforce
COPY services/salesforce-loader/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN rm -rf salesforce/data && chmod +x /usr/local/bin/entrypoint.sh salesforce/scripts/load.sh \
 && useradd --system --uid 10001 --create-home app && chown -R app /app
USER app
ENTRYPOINT ["entrypoint.sh"]
