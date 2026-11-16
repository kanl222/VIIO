FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.12-slim AS builder

WORKDIR /app
ENV UV_LINK_MODE=copy

COPY --from=uv /uv /bin/uv

# Копирование манифестов workspace
COPY pyproject.toml uv.lock ./
COPY packages/viio-core/pyproject.toml packages/viio-core/
COPY services/rag/pyproject.toml services/rag/

# Установка зависимостей пакета
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-dev --package viio-rag

# Копирование исходного кода пакетов
COPY packages/viio-core packages/viio-core
COPY services/rag services/rag

# Сборка и установка пакета
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package viio-rag

# Финальный легковесный образ
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/services/rag/src" \
    PATH="/app/.venv/bin:$PATH"

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/rag/src /app/services/rag/src

RUN mkdir -p /traces && chown -R 65532:65532 /traces
USER 65532:65532
EXPOSE 8000

CMD ["uvicorn", "viio_rag.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
