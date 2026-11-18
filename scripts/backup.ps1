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
    echo "CREATE SNAPSHOT;" | docker exec -i viio_memgraph mgconsole 2>$null | Out-Null
    Write-Host "  [OK] Снапшот графа создан." -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Не удалось отправить CREATE SNAPSHOT в Memgraph, архивируем текущее состояние." -ForegroundColor Gray
}

# 2. Логические дампы PostgreSQL
Write-Host "`n[2/5] Создание дампов баз данных PostgreSQL..." -ForegroundColor Yellow
docker exec viio_postgres pg_dumpall -U postgres --globals-only > (Join-Path $BackupDir "postgres_globals.sql")
Write-Host "  [OK] postgres_globals.sql сохранен." -ForegroundColor Green

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

Write-Host "[6/6] Настройка прав доступа к файловому тому (UID 65532)..." -ForegroundColor Yellow
docker run --rm --user 0:0 -v viio_documents_storage:/data/documents alpine chown -R 65532:65532 /data/documents

Write-Host "`n[OK] Все тома успешно восстановлены!" -ForegroundColor Green
Write-Host "Для запуска системы выполните: .\start.ps1" -ForegroundColor Green
'@ -replace '\{BACKUP_NAME\}', $BackupName

Set-Content -Path (Join-Path $BackupDir "restore.ps1") -Value $restoreScriptContent -Encoding UTF8

# 5. Генерация README.md
$readmeContent = @"
# Резервная копия VIIO ($BackupName)

## Содержимое архива
1. **postgres_data.tar.gz** — полный том PostgreSQL с базами agent_db, user_db, viio_gosapteka56_test.
2. **qdrant_storage.tar.gz** — векторные коллекции и поисковые индексы.
3. **memgraph_data.tar.gz** — граф знаний Memgraph (узлы, связи, онтология).
4. **documents_storage.tar.gz** — хранилище оригиналов документов.
5. **Логические дампы:** agent_db.dump, user_db.dump, viio_gosapteka56_test.dump, postgres_globals.sql.

## Быстрое восстановление на целевом компьютере
1. Скопируйте проект и файл .env на новый компьютер.
2. Выполните:
   ```powershell
   powershell -File .\data\backups\$BackupName\restore.ps1
   ```
3. Запустите платформу:
   ```powershell
   .\start.ps1
   ```
"@
Set-Content -Path (Join-Path $BackupDir "README.md") -Value $readmeContent -Encoding UTF8

Write-Host "`n[5/5] Резервная копия успешно создана в: $BackupDir" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Cyan
