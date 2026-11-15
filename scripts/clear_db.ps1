# Скрипт очистки базы данных и векторного хранилища (сохраняет пользователей и сессии)
# Использование: .\scripts\clear_db.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$EnvFile = Join-Path $RootDir ".env"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   VIIO Database & Qdrant Cleanup Script  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# 1. Чтение .env
$QdrantPort = "6333"
$QdrantApiKey = ""
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#")) {
            $parts = $line.Split("=", 2)
            if ($parts.Length -eq 2) {
                $key = $parts[0].Trim()
                $val = $parts[1].Trim().Trim("'`"")
                if ($key -eq "QDRANT_PORT") { $QdrantPort = $val }
                if ($key -eq "QDRANT_API_KEY") { $QdrantApiKey = $val }
            }
        }
    }
}

# 2. Очистка PostgreSQL
Write-Host "`n[1/4] Очистка PostgreSQL..." -ForegroundColor Yellow

$sqlUser = "TRUNCATE TABLE messages, conversations CASCADE;"
docker exec viio_postgres psql -U postgres -d user_db -c $sqlUser | Out-Null
Write-Host "  [OK] user_db: conversations и messages очищены (users и sessions сохранены)" -ForegroundColor Green

$sqlAgent = "TRUNCATE TABLE document_publications, document_contents, document_upload_jobs, documents, checkpoint_blobs, checkpoint_writes, checkpoints, store, user_facts CASCADE;"
docker exec viio_postgres psql -U postgres -d agent_db -c $sqlAgent | Out-Null
Write-Host "  [OK] agent_db: документы и чекпоинты агента очищены" -ForegroundColor Green

# 3. Очистка локального хранилища документов
Write-Host "`n[2/4] Очистка локального хранилища документов..." -ForegroundColor Yellow
docker exec viio_document_worker sh -c "rm -rf /data/documents/*"
Write-Host "  [OK] /data/documents/* в viio_document_worker очищены" -ForegroundColor Green

# 4. Очистка Qdrant
Write-Host "`n[3/4] Очистка коллекций Qdrant..." -ForegroundColor Yellow
$headers = @{}
if ($QdrantApiKey) {
    $headers["api-key"] = $QdrantApiKey
}

try {
    $qdrantUrl = "http://localhost:$QdrantPort"
    $response = Invoke-RestMethod -Uri "$qdrantUrl/collections" -Headers $headers -Method GET
    $collections = $response.result.collections
    if ($collections -and $collections.Count -gt 0) {
        foreach ($col in $collections) {
            $colName = $col.name
            $delRes = Invoke-RestMethod -Uri "$qdrantUrl/collections/$colName" -Headers $headers -Method DELETE
            Write-Host "  [OK] Удалена коллекция Qdrant: $colName" -ForegroundColor Green
        }
    } else {
        Write-Host "  [OK] Коллекций в Qdrant нет (уже чистые)" -ForegroundColor Green
    }
} catch {
    Write-Warning "Ошибка при очистке Qdrant: $_"
}

# 5. Проверка состояния
Write-Host "`n[4/4] Проверка результатов..." -ForegroundColor Yellow
Write-Host "`nСостояние user_db:" -ForegroundColor Cyan
docker exec viio_postgres psql -U postgres -d user_db -c "SELECT 'users' as tbl, count(*) FROM users UNION ALL SELECT 'sessions', count(*) FROM sessions UNION ALL SELECT 'conversations', count(*) FROM conversations UNION ALL SELECT 'messages', count(*) FROM messages;"

Write-Host "`nСостояние agent_db:" -ForegroundColor Cyan
docker exec viio_postgres psql -U postgres -d agent_db -c "SELECT 'documents' as tbl, count(*) FROM documents UNION ALL SELECT 'checkpoints', count(*) FROM checkpoints;"

Write-Host "`nОчистка завершена успешно!`n" -ForegroundColor Green
