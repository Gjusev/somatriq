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
COPY --from=builder /app/.venv /app/.venv
COPY services/ /app/services/
COPY packages/ /app/packages/
COPY database/ /app/database/
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "somatriq_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
