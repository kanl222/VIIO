#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

echo -e "\033[0;36m=================================================================\033[0m"
echo -e "\033[0;36m                 VIIO: Скрипт запуска платформы                  \033[0m"
echo -e "\033[0;36m=================================================================\033[0m"

# 1. Проверка Docker
echo -e "\n\033[0;33m[1/5] Проверка Docker...\033[0m"
if ! docker info >/dev/null 2>&1; then
    echo -e "\033[0;31m[ERROR] Docker daemon не запущен или недоступен!\033[0m"
    exit 1
fi
echo -e "\033[0;32m[OK] Docker запущен.\033[0m"

# 2. Проверка .env
echo -e "\n\033[0;33m[2/5] Проверка конфигурации .env...\033[0m"
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo -e "\033[0;36m[INFO] Создан .env из .env.example.\033[0m"
    else
        echo -e "\033[0;31m[ERROR] Отсутствует шаблон .env.example!\033[0m"
        exit 1
    fi
fi

# Настройка секретов
if command -v uv >/dev/null 2>&1; then
    uv run python scripts/configure_runtime_secrets.py 2>/dev/null || true
elif command -v python3 >/dev/null 2>&1; then
    python3 scripts/configure_runtime_secrets.py 2>/dev/null || true
else
    docker run --rm -v "$REPO_ROOT:/work" -w /work python:3.12-alpine python scripts/configure_runtime_secrets.py 2>/dev/null || true
fi
echo -e "\033[0;32m[OK] Конфигурация проверена.\033[0m"

# 3. Запуск инфраструктуры
echo -e "\n\033[0;33m[3/5] Запуск инфраструктурных сервисов (PostgreSQL, Qdrant, Redis, Memgraph)...\033[0m"
docker volume create viio_docling_models >/dev/null 2>&1 || true
docker volume create viio_documents_storage >/dev/null 2>&1 || true
docker run --rm --user 0:0 -v viio_docling_models:/models alpine sh -c "mkdir -p /models/huggingface /models/torch /models/artifacts && chown -R 65532:65532 /models && chmod -R 775 /models" >/dev/null 2>&1 || true
docker run --rm --user 0:0 -v viio_documents_storage:/data/documents alpine sh -c "mkdir -p /data/documents && chown -R 65532:65532 /data/documents && chmod -R 775 /data/documents" >/dev/null 2>&1 || true

docker compose up -d postgres qdrant redis memgraph

echo -e "\n\033[0;33mОжидание готовности PostgreSQL...\033[0m"
for i in {1..30}; do
    if docker compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; then
        echo -e "\033[0;32m[OK] PostgreSQL готов.\033[0m"
        break
    fi
    sleep 1
done

# 4. Инициализация схем и хранилищ
echo -e "\n\033[0;33m[4/5] Проверка баз данных, ролей и векторных хранилищ...\033[0m"
if command -v uv >/dev/null 2>&1; then
    POSTGRES_HOST=localhost POSTGRES_PORT=5432 uv run --env-file .env python services/documents/scripts/provision_runtime_roles.py || true
    POSTGRES_HOST=localhost POSTGRES_PORT=5432 uv run --env-file .env python services/documents/scripts/migrate_schema.py || true
    POSTGRES_HOST=localhost POSTGRES_PORT=5432 uv run --env-file .env python services/assistent/scripts/migrate_schema.py || true
elif command -v python3 >/dev/null 2>&1; then
    POSTGRES_HOST=localhost POSTGRES_PORT=5432 python3 services/documents/scripts/provision_runtime_roles.py || true
    POSTGRES_HOST=localhost POSTGRES_PORT=5432 python3 services/documents/scripts/migrate_schema.py || true
    POSTGRES_HOST=localhost POSTGRES_PORT=5432 python3 services/assistent/scripts/migrate_schema.py || true
else
    docker compose exec -T postgres sh -c "psql -U postgres -d agent_db -c 'ALTER TABLE documents ADD COLUMN IF NOT EXISTS family_id VARCHAR(64); UPDATE documents SET family_id=id WHERE family_id IS NULL;'" >/dev/null 2>&1 || true
fi

echo -e "\n\033[0;33mОжидание готовности Qdrant...\033[0m"
for i in {1..30}; do
    if curl -s http://localhost:6333/readyz >/dev/null 2>&1; then
        echo -e "\033[0;32m[OK] Qdrant готов.\033[0m"
        break
    fi
    sleep 1
done

echo -e "\n\033[0;33mПроверка коллекций Qdrant...\033[0m"
if command -v uv >/dev/null 2>&1; then
    QDRANT_URL=http://localhost:6333 uv run --env-file .env python services/rag/scripts/init_qdrant_collections.py || true
elif command -v python3 >/dev/null 2>&1; then
    QDRANT_URL=http://localhost:6333 python3 services/rag/scripts/init_qdrant_collections.py || true
else
    QDRANT_KEY=$(grep -E '^QDRANT_API_KEY=' .env 2>/dev/null | cut -d '=' -f2- | tr -d ' \r\n')
    for col in knowledge_base knowledge_public project_memory; do
        curl -s -X PUT "http://localhost:6333/collections/$col" \
            -H "api-key: $QDRANT_KEY" \
            -H "Content-Type: application/json" \
            -d '{"vectors":{"size":768,"distance":"Cosine"},"sparse_vectors":{"bm25":{"modifier":"idf"}}}' >/dev/null 2>&1 || true
    done
fi
echo -e "\033[0;32m[OK] Коллекции Qdrant проверены.\033[0m"

# 5. Запуск основных сервисов
echo -e "\n\033[0;33m[5/5] Запуск приложений VIIO...\033[0m"
docker compose up -d "$@"

echo -e "\n\033[0;36m-----------------------------------------------------------------\033[0m"
echo -e "\033[0;32mСистема запущена!\033[0m"
echo -e "Веб-интерфейс чата:  http://localhost:3000 (или http://localhost)"
echo -e "API ассистента:      http://localhost:8000/docs"
echo -e "Векторная БД Qdrant: http://localhost:6333/dashboard"
echo -e "Memgraph Lab:        http://localhost:3001"
echo -e "\033[0;36m-----------------------------------------------------------------\033[0m"
