#!/usr/bin/env bash
set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${REPO_ROOT}/data/backups/backup-2026-11-17"

if [ ! -d "$BACKUP_DIR" ]; then
    echo -e "\033[0;31m[ERROR] Папка с резервной копией не найдена: $BACKUP_DIR\033[0m"
    echo -e "Поместите папку backup-2026-11-17 в data/backups/"
    exit 1
fi

echo -e "\033[0;36m=== Восстановление резервной копии VIIO (2026-11-17) ===\033[0m"

echo -e "\033[0;33m[1/6] Создание томов Docker...\033[0m"
docker volume create viio_postgres_data
docker volume create viio_qdrant_storage
docker volume create viio_memgraph_data
docker volume create viio_documents_storage
docker volume create viio_docling_models

echo -e "\033[0;33m[2/6] Восстановление PostgreSQL тома...\033[0m"
docker run --rm -v viio_postgres_data:/volume -v "${BACKUP_DIR}:/backup" alpine sh -c "rm -rf /volume/* && tar -xzf /backup/postgres_data.tar.gz -C /volume"

echo -e "\033[0;33m[3/6] Восстановление Qdrant (векторы)...\033[0m"
docker run --rm -v viio_qdrant_storage:/volume -v "${BACKUP_DIR}:/backup" alpine sh -c "rm -rf /volume/* && tar -xzf /backup/qdrant_storage.tar.gz -C /volume"

echo -e "\033[0;33m[4/6] Восстановление Memgraph (граф)...\033[0m"
docker run --rm -v viio_memgraph_data:/volume -v "${BACKUP_DIR}:/backup" alpine sh -c "rm -rf /volume/* && tar -xzf /backup/memgraph_data.tar.gz -C /volume"

echo -e "\033[0;33m[5/6] Восстановление хранилища оригиналов документов...\033[0m"
docker run --rm -v viio_documents_storage:/volume -v "${BACKUP_DIR}:/backup" alpine sh -c "rm -rf /volume/* && tar -xzf /backup/documents_storage.tar.gz -C /volume"

echo -e "\033[0;33m[6/6] Настройка прав доступа к файловым томам (UID 65532)...\033[0m"
docker run --rm --user 0:0 -v viio_documents_storage:/data/documents alpine sh -c "mkdir -p /data/documents && chown -R 65532:65532 /data/documents && chmod -R 775 /data/documents"
docker run --rm --user 0:0 -v viio_docling_models:/models alpine sh -c "mkdir -p /models/huggingface /models/torch /models/artifacts && chown -R 65532:65532 /models && chmod -R 775 /models"

echo -e "\n\033[0;32mВсе тома успешно восстановлены!\033[0m"
echo -e "\033[0;32mДля запуска выполните: ./start.sh (или docker compose up -d)\033[0m"
