# Somatriq shared Python image — one build, isolated processes (compose x-python-base).
# Contains every service package and every shared package (uv workspace install).

FROM ghcr.io/astral-sh/uv:0.5.11 AS uv

FROM python:3.12-slim AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1
# Layer-friendly: workspace metadata first, then sources
COPY pyproject.toml uv.lock* ./
COPY services/ services/
COPY packages/ packages/
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
WORKDIR /app
# pg_dump/pg_restore in every python-service container (backup service, ADR
# 0016). Same major as the server (timescale/timescaledb-ha:pg16) — pg_dump
# refuses to dump a newer-major server, and Debian main ships a different
# major, so the client comes from the upstream PGDG repo. Fetch tools are
# purged again; only postgresql-client-16 (+ its libs) remain.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo "$VERSION_CODENAME")-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-16; \
    apt-get purge -y curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*
COPY --from=builder /app/.venv /app/.venv
COPY services/ /app/services/
COPY packages/ /app/packages/
COPY database/ /app/database/
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "somatriq_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
