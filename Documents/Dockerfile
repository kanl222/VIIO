# Both processes use the same domain library, deployed independently from agents.
FROM ghcr.io/astral-sh/uv:latest AS uv
FROM python:3.12-slim AS builder
WORKDIR /app
COPY --from=uv /uv /bin/uv
COPY Assistent/pyproject.toml Assistent/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY Assistent/src ./src
COPY Assistent/README.md ./README.md
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runner
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PATH="/app/.venv/bin:$PATH" SERVICE_ROLE=documents
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
EXPOSE 8000
CMD ["uvicorn", "assistent:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
