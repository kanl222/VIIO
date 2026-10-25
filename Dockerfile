FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.12-slim AS builder

WORKDIR /app

COPY --from=uv /uv /bin/uv

# Установка зависимостей проекта
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Копирование исходного кода и сборка пакета
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev

# Финальный легковесный образ
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src" \
    PATH="/app/.venv/bin:$PATH"

# Копируем виртуальное окружение и исходный код
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/README.md /app/README.md

EXPOSE 8000

CMD ["uvicorn", "apiagent:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
