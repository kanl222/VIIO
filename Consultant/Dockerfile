FROM ghcr.io/astral-sh/uv:latest AS uv
FROM python:3.12-slim AS builder
WORKDIR /app
ENV UV_LINK_MODE=copy
COPY --from=uv /uv /bin/uv
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM builder AS checks
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --dev
COPY tests ./tests
RUN .venv/bin/python -m pytest tests -q

FROM python:3.12-slim AS runner
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY --from=builder /app/.venv /app/.venv
COPY --from=checks /app/src /app/src
RUN mkdir -p /traces && chown 65532:65532 /traces
USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "viio_consultant:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
