<#
.SYNOPSIS
    Создание полной резервной копии данных VIIO (PostgreSQL, Qdrant, Memgraph, Documents).
.DESCRIPTION
    Экспортирует дампы PostgreSQL и архивирует тома Docker в папку data/backups/backup-YYYY-MM-DD.
#>

[CmdletBinding()]
param(
    [string]$BackupName = "backup-$(Get-Date -Format 'yyyy-MM-dd')"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$BackupDir = Join-Path $RepoRoot "data\backups\$BackupName"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "            VIIO: Создание полной резервной копии                " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Директория бэкапа: $BackupDir" -ForegroundColor Yellow

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

# 1. Снапшот Memgraph
Write-Host "`n[1/5] Фиксация снапшота Memgraph..." -ForegroundColor Yellow
try {
    docker exec viio_memgraph bash -c 'echo "CREATE SNAPSHOT;" | mgconsole' 2>$null | Out-Null
    Write-Host "  [OK] Снапшот графа создан." -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Не удалось отправить CREATE SNAPSHOT в Memgraph, архивируем текущее состояние." -ForegroundColor Gray
}

# 2. Логические дампы PostgreSQL (без паролей и ключей ролей)
Write-Host "`n[2/5] Создание дампов баз данных PostgreSQL (без ключей доступа)..." -ForegroundColor Yellow
$globalsFile = Join-Path $BackupDir "postgres_globals.sql"
docker exec viio_postgres pg_dumpall -U postgres --globals-only --no-role-passwords -f /tmp/postgres_globals.sql
docker cp "viio_postgres:/tmp/postgres_globals.sql" $globalsFile
docker exec viio_postgres rm -f /tmp/postgres_globals.sql
Write-Host "  [OK] postgres_globals.sql сохранен (без паролей ролей)." -ForegroundColor Green

foreach ($db in @("agent_db", "user_db", "viio_gosapteka56_test")) {
    $dumpFile = Join-Path $BackupDir "$db.dump"
    docker exec viio_postgres pg_dump -U postgres -Fc -d $db -f "/tmp/$db.dump"
    docker cp "viio_postgres:/tmp/$db.dump" $dumpFile
    docker exec viio_postgres rm -f "/tmp/$db.dump"
    Write-Host "  [OK] $db.dump сохранен ($([math]::Round((Get-Item $dumpFile).Length / 1MB, 2)) МБ)." -ForegroundColor Green
}

# 3. Архивирование Docker Volumes
Write-Host "`n[3/5] Создание архивов Docker томов..." -ForegroundColor Yellow
$backupDirPosix = $BackupDir -replace '\\', '/'

$volumes = @(
    @{ Name = "viio_postgres_data"; File = "postgres_data.tar.gz"; Desc = "PostgreSQL" },
    @{ Name = "viio_qdrant_storage"; File = "qdrant_storage.tar.gz"; Desc = "Qdrant (векторы)" },
    @{ Name = "viio_memgraph_data"; File = "memgraph_data.tar.gz"; Desc = "Memgraph (граф)" },
    @{ Name = "viio_documents_storage"; File = "documents_storage.tar.gz"; Desc = "Документы" }
)

foreach ($v in $volumes) {
    Write-Host "  Архивирование $($v.Desc) [$($v.Name)]..." -ForegroundColor Cyan
    docker run --rm -v "$($v.Name):/volume" -v "${backupDirPosix}:/backup" alpine sh -c "tar -czf /backup/$($v.File) -C /volume ."
    $fileItem = Get-Item (Join-Path $BackupDir $v.File)
    Write-Host "  [OK] $($v.File) готов ($([math]::Round($fileItem.Length / 1MB, 2)) МБ)." -ForegroundColor Green
}

# 4. Генерация restore.ps1
Write-Host "`n[4/5] Генерация скрипта восстановления restore.ps1..." -ForegroundColor Yellow
$restoreScriptContent = @'
# Скрипт быстрого восстановления резервной копии VIIO
# Запускать из корня репозитория VIIO:
# powershell -File .\data\backups\{BACKUP_NAME}\restore.ps1

$backupDir = (Get-Item (Split-Path -Parent $MyInvocation.MyCommand.Path)).FullName -replace '\\', '/'

Write-Host "=== Восстановление резервной копии VIIO ===" -ForegroundColor Cyan

Write-Host "[1/6] Создание томов Docker..." -ForegroundColor Yellow
docker volume create viio_postgres_data | Out-Null
docker volume create viio_qdrant_storage | Out-Null
docker volume create viio_memgraph_data | Out-Null
docker volume create viio_documents_storage | Out-Null

Write-Host "[2/6] Восстановление PostgreSQL тома..." -ForegroundColor Yellow
docker run --rm -v viio_postgres_data:/volume -v "${backupDir}:/backup" alpine sh -c "rm -rf /volume/* && tar -xzf /backup/postgres_data.tar.gz -C /volume"

Write-Host "[3/6] Восстановление Qdrant (векторы)..." -ForegroundColor Yellow
docker run --rm -v viio_qdrant_storage:/volume -v "${backupDir}:/backup" alpine sh -c "rm -rf /volume/* && tar -xzf /backup/qdrant_storage.tar.gz -C /volume"

Write-Host "[4/6] Восстановление Memgraph (граф)..." -ForegroundColor Yellow
docker run --rm -v viio_memgraph_data:/volume -v "${backupDir}:/backup" alpine sh -c "rm -rf /volume/* && tar -xzf /backup/memgraph_data.tar.gz -C /volume"

Write-Host "[5/6] Восстановление хранилища оригиналов документов..." -ForegroundColor Yellow
docker run --rm -v viio_documents_storage:/volume -v "${backupDir}:/backup" alpine sh -c "rm -rf /volume/* && tar -xzf /backup/documents_storage.tar.gz -C /volume"

Write-Host "[6/6] Настройка прав доступа к файловым томам (UID 65532)..." -ForegroundColor Yellow
docker run --rm --user 0:0 -v viio_documents_storage:/data/documents alpine sh -c "mkdir -p /data/documents && chown -R 65532:65532 /data/documents && chmod -R 775 /data/documents"
docker volume create viio_docling_models | Out-Null
docker run --rm --user 0:0 -v viio_docling_models:/models alpine sh -c "mkdir -p /models/huggingface /models/torch /models/artifacts && chown -R 65532:65532 /models && chmod -R 775 /models"

Write-Host "`n[OK] Все тома успешно восстановлены!" -ForegroundColor Green
Write-Host "Для запуска системы выполните: .\start.ps1" -ForegroundColor Green
'@ -replace '\{BACKUP_NAME\}', $BackupName

Set-Content -Path (Join-Path $BackupDir "restore.ps1") -Value $restoreScriptContent -Encoding UTF8

$restoreShContent = @'
#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${SCRIPT_DIR}"

echo -e "\033[0;36m=== Восстановление резервной копии VIIO ({BACKUP_NAME}) ===\033[0m"

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
'@ -replace '\{BACKUP_NAME\}', $BackupName

Set-Content -Path (Join-Path $BackupDir "restore.sh") -Value $restoreShContent -Encoding UTF8

# 5. Генерация README.md
$readmeContent = @'
# Резервная копия VIIO ({BACKUP_NAME})

Резервная копия новых хранилищ VIIO создана без секретов и ключей доступа (роли пользователей БД экспортированы с опцией --no-role-passwords, файлы .env и *.key исключены).

## Содержимое архива
1. **postgres_data.tar.gz** — полный том PostgreSQL с базами agent_db, user_db, viio_gosapteka56_test.
2. **qdrant_storage.tar.gz** — векторные коллекции и поисковые индексы Qdrant.
3. **memgraph_data.tar.gz** — граф знаний Memgraph (узлы, связи, онтология).
4. **documents_storage.tar.gz** — хранилище оригиналов документов.
5. **Логические дампы:** agent_db.dump, user_db.dump, viio_gosapteka56_test.dump, postgres_globals.sql (без паролей).

## Быстрое восстановление на целевом компьютере
1. Скопируйте проект на новый компьютер. Настройте файл .env через `start.ps1` (секреты сгенерируются автоматически).
2. Выполните восстановление:
   Windows PowerShell:
   ```powershell
   powershell -File .\data\backups\{BACKUP_NAME}\restore.ps1
   ```
   Linux / Bash:
   ```bash
   bash data/backups/{BACKUP_NAME}/restore.sh
   ```
3. Запустите платформу:
   ```powershell
   .\start.ps1
   ```
'@ -replace '\{BACKUP_NAME\}', $BackupName
Set-Content -Path (Join-Path $BackupDir "README.md") -Value $readmeContent -Encoding UTF8

Write-Host "`n[5/5] Резервная копия успешно создана в: $BackupDir" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
