FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.12-slim AS builder

WORKDIR /app
ENV UV_LINK_MODE=copy

COPY --from=uv /uv /bin/uv

# Установка зависимостей проекта
COPY RAG/pyproject.toml /RAG/pyproject.toml
COPY RAG/src /RAG/src
COPY RAG/README.md /RAG/README.md
COPY Documents/pyproject.toml Documents/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev --no-editable

# Копирование исходного кода и сборка пакета
COPY Documents/src ./src
COPY Documents/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# Финальный легковесный образ
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src" \
    PATH="/app/.venv/bin:$PATH"

# Копируем виртуальное окружение и исходный код
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/README.md /app/README.md

RUN mkdir -p /traces && chown 65532:65532 /traces
USER 65532:65532
EXPOSE 8000

CMD ["uvicorn", "viio_documents:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
