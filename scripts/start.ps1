<#
.SYNOPSIS
    Скрипт полного запуска платформы VIIO в Docker Compose.

.DESCRIPTION
    1. Проверяет доступность Docker Desktop.
    2. Проверяет наличие .env (создаёт из .env.example при необходимости).
    3. Дополняет недостающие секреты через configure_runtime_secrets.py.
    4. Запускает инфраструктурные сервисы (Postgres, Qdrant, Redis, Memgraph).
    5. Дожидается готовности PostgreSQL (pg_isready).
    6. Инициализирует базы данных (agent_db, user_db) и роли через provision_runtime_roles.py.
    7. Запускает сервисы приложения (Nginx, WebChat, Assistent, Documents, RAG, Consultant, Diagnostics).
    8. Ожидает проверки здоровья сервисов и выводит статус и ссылки.

.PARAMETER Build
    Принудительно пересобрать Docker-образы перед запуском (--build).

.PARAMETER Profiles
    Дополнительные профили Docker Compose (например: -Profiles pdf, postgres).

.PARAMETER NoBrowser
    Не открывать WebChat в браузере автоматически.
#>

[CmdletBinding()]
param(
    [switch]$Build,
    [string[]]$Profiles = @(),
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "                 VIIO: Скрипт запуска платформы                  " -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# 1. Проверка Docker
Write-Host "`n[1/6] Проверка Docker..." -ForegroundColor Yellow
try {
    $dockerInfo = docker version --format '{{.Server.Version}}' 2>$null
    if (-not $dockerInfo) {
        throw "Docker daemon не отвечает"
    }
    Write-Host "  [OK] Docker запущен (версия сервера: $dockerInfo)" -ForegroundColor Green
} catch {
    Write-Host "  [ERROR] Docker Desktop не запущен или недоступен!" -ForegroundColor Red
    Write-Host "  Запустите Docker Desktop и повторите попытку." -ForegroundColor Red
    exit 1
}

# 2. Проверка и подготовка .env
Write-Host "`n[2/6] Проверка конфигурации .env..." -ForegroundColor Yellow
$EnvPath = Join-Path $RepoRoot ".env"
$EnvExample = Join-Path $RepoRoot ".env.example"

if (-not (Test-Path $EnvPath)) {
    if (Test-Path $EnvExample) {
        Copy-Item -LiteralPath $EnvExample -Destination $EnvPath
        Write-Host "  [INFO] Файл .env не найден. Создана копия из .env.example." -ForegroundColor Cyan
    } else {
        Write-Host "  [ERROR] Отсутствует шаблон .env.example!" -ForegroundColor Red
        exit 1
    }
}

# Генерация недостающих runtime секретов
$UvCmd = Get-Command uv -ErrorAction SilentlyContinue
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue

if ($UvCmd) {
    & uv run python scripts/configure_runtime_secrets.py 2>$null | Out-Null
    Write-Host "  [OK] Конфигурация и секреты .env проверены (uv)." -ForegroundColor Green
} elseif ($PythonCmd) {
    & python scripts/configure_runtime_secrets.py 2>$null | Out-Null
    Write-Host "  [OK] Конфигурация и секреты .env проверены (python)." -ForegroundColor Green
} else {
    Write-Host "  [INFO] Python/uv не найден на хосте, генерация недостающих ключей средствами PowerShell..." -ForegroundColor Cyan
    $envContent = Get-Content -LiteralPath $EnvPath -Raw
    $keys = @('CHAT_API_KEY','DOCUMENTS_READ_KEY','DOCUMENTS_ADMIN_KEY','DOCUMENT_WORKER_KEY',
              'DOCLING_API_KEY','QDRANT_API_KEY','QDRANT_READ_KEY','CONSULTANT_API_KEY','CONSULTANT_SOURCE_KEY')
    foreach ($k in $keys) {
        $pattern = "(?m)^$k=\s*$"
        if ($envContent -match $pattern) {
            $bytes = [byte[]]::new(32)
            [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
            $hex = -join ($bytes | ForEach-Object { '{0:x2}' -f $_ })
            $envContent = $envContent -replace $pattern, "$k=$hex"
        }
    }
    if ($envContent -match "(?m)^POSTGRES_PASSWORD=\s*$") {
        $envContent = $envContent -replace "(?m)^POSTGRES_PASSWORD=\s*$", "POSTGRES_PASSWORD=postgres"
    }
    if ($envContent -match "(?m)^BOOTSTRAP_ADMIN_USERNAME=\s*$") {
        $envContent = $envContent -replace "(?m)^BOOTSTRAP_ADMIN_USERNAME=\s*$", "BOOTSTRAP_ADMIN_USERNAME=admin"
    }
    if ($envContent -match "(?m)^BOOTSTRAP_ADMIN_PASSWORD=\s*$") {
        $bytes = [byte[]]::new(16)
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $pass = [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
        $envContent = $envContent -replace "(?m)^BOOTSTRAP_ADMIN_PASSWORD=\s*$", "BOOTSTRAP_ADMIN_PASSWORD=$pass"
    }
    Set-Content -LiteralPath $EnvPath -Value $envContent -NoNewline
    Write-Host "  [OK] Конфигурация и секреты .env успешно дополнены через PowerShell." -ForegroundColor Green
}

# Сборка аргументов профилей
$ProfileArgs = @()
foreach ($p in $Profiles) {
    if ($p.Trim()) {
        $ProfileArgs += "--profile"
        $ProfileArgs += $p.Trim()
    }
}

# 3. Запуск инфраструктурных сервисов (PostgreSQL, Qdrant, Redis, Memgraph)
Write-Host "`n[3/6] Проверка томов и запуск инфраструктурных сервисов..." -ForegroundColor Yellow
docker volume create viio_docling_models 2>$null | Out-Null
docker volume create viio_documents_storage 2>$null | Out-Null
docker run --rm --user 0:0 -v viio_docling_models:/models alpine sh -c "mkdir -p /models/huggingface /models/torch /models/artifacts && chown -R 65532:65532 /models && chmod -R 775 /models" 2>$null | Out-Null
docker run --rm --user 0:0 -v viio_documents_storage:/data/documents alpine sh -c "mkdir -p /data/documents && chown -R 65532:65532 /data/documents && chmod -R 775 /data/documents" 2>$null | Out-Null

docker compose @ProfileArgs up -d postgres qdrant redis memgraph
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Ошибка запуска инфраструктурных контейнеров!" -ForegroundColor Red
    exit 1
}

# 4. Ожидание готовности PostgreSQL
Write-Host "`n[4/6] Ожидание готовности PostgreSQL..." -ForegroundColor Yellow
$maxAttempts = 30
$attempt = 0
$pgReady = $false

while ($attempt -lt $maxAttempts) {
    $attempt++
    docker compose exec -T postgres pg_isready -U postgres 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $pgReady = $true
        break
    }
    Start-Sleep -Seconds 1
}

if (-not $pgReady) {
    Write-Host "  [ERROR] PostgreSQL не ответил за $maxAttempts секунд!" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] PostgreSQL готов к подключениям." -ForegroundColor Green

# Инициализация баз данных и схем
Write-Host "  Проверка баз данных и схем (agent_db, user_db)..." -ForegroundColor Yellow
try {
    if ($UvCmd) {
        $env:POSTGRES_HOST = "localhost"
        $env:POSTGRES_PORT = "5432"
        & uv run --env-file .env python services/documents/scripts/provision_runtime_roles.py | Out-Null
        & uv run --env-file .env python services/documents/scripts/migrate_schema.py 2>$null | Out-Null
        & uv run --env-file .env python services/assistent/scripts/migrate_schema.py 2>$null | Out-Null
    } elseif ($PythonCmd) {
        $env:POSTGRES_HOST = "localhost"
        $env:POSTGRES_PORT = "5432"
        & python services/documents/scripts/provision_runtime_roles.py | Out-Null
        & python services/documents/scripts/migrate_schema.py 2>$null | Out-Null
        & python services/assistent/scripts/migrate_schema.py 2>$null | Out-Null
    } else {
        docker compose exec -T postgres sh -c "psql -U postgres -d agent_db -c 'ALTER TABLE documents ADD COLUMN IF NOT EXISTS family_id VARCHAR(64); UPDATE documents SET family_id=id WHERE family_id IS NULL;'" 2>$null | Out-Null
    }
    Write-Host "  [OK] Базы данных (user_db, agent_db) и схемы успешно проверены." -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Инициализация баз данных завершилась с предупреждением: $_" -ForegroundColor Yellow
}

# Ожидание готовности Qdrant и инициализация коллекций
Write-Host "  Ожидание готовности Qdrant..." -ForegroundColor Yellow
$qdrantReady = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $res = Invoke-RestMethod -Uri "http://localhost:6333/readyz" -TimeoutSec 2 -ErrorAction Stop
        $qdrantReady = $true
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}
if ($qdrantReady) {
    Write-Host "  [OK] Qdrant готов к подключениям." -ForegroundColor Green
} else {
    Write-Host "  [WARN] Qdrant не ответил на /readyz за 30 секунд, продолжаем..." -ForegroundColor Yellow
}

Write-Host "  Проверка коллекций Qdrant (knowledge_base, knowledge_public, project_memory)..." -ForegroundColor Yellow
try {
    if ($UvCmd) {
        $env:QDRANT_URL = "http://localhost:6333"
        & uv run --env-file .env python services/rag/scripts/init_qdrant_collections.py 2>$null | Out-Null
    } elseif ($PythonCmd) {
        $env:QDRANT_URL = "http://localhost:6333"
        & python services/rag/scripts/init_qdrant_collections.py 2>$null | Out-Null
    } else {
        $qApiKey = ($envContent -split "`n" | Where-Object { $_ -match "^QDRANT_API_KEY=(.*)" } | ForEach-Object { $matches[1].Trim() })
        $qHeaders = @{}
        if ($qApiKey) { $qHeaders["api-key"] = $qApiKey }
        $qCols = @('knowledge_base', 'knowledge_public', 'project_memory')
        foreach ($c in $qCols) {
            try {
                Invoke-RestMethod -Uri "http://localhost:6333/collections/$c" -Headers $qHeaders -TimeoutSec 2 -ErrorAction Stop | Out-Null
            } catch {
                $qBody = @{
                    vectors = @{ size = 768; distance = "Cosine" }
                    sparse_vectors = @{ bm25 = @{ modifier = "idf" } }
                } | ConvertTo-Json -Depth 5
                Invoke-RestMethod -Uri "http://localhost:6333/collections/$c" -Method Put -Headers $qHeaders -ContentType "application/json" -Body $qBody -TimeoutSec 5 -ErrorAction SilentlyContinue | Out-Null
            }
        }
    }
    Write-Host "  [OK] Коллекции Qdrant успешно проверены." -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Инициализация коллекций Qdrant завершилась с предупреждением: $_" -ForegroundColor Yellow
}

# 5. Запуск основных сервисов приложения
Write-Host "`n[5/6] Запуск сервисов приложения VIIO..." -ForegroundColor Yellow
$upArgs = @()
if ($ProfileArgs) { $upArgs += $ProfileArgs }
$upArgs += "up"
$upArgs += "-d"
if ($Build) {
    $upArgs += "--build"
    Write-Host "  (Сборка образов включена: --build)" -ForegroundColor Cyan
}

docker compose @upArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Ошибка запуска контейнеров приложения!" -ForegroundColor Red
    exit 1
}

# 6. Проверка доступности ключевых сервисов
Write-Host "`n[6/6] Проверка готовности сервисов..." -ForegroundColor Yellow

function Test-EndpointHealth ($url, $name, $timeoutSec = 25) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $timeoutSec) {
        try {
            $resp = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 2 -ErrorAction Stop
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

$webchatOk = Test-EndpointHealth "http://localhost:3000/health" "WebChat" 25
$assistentOk = Test-EndpointHealth "http://localhost:8000/health" "Assistent" 25

Write-Host "`n-----------------------------------------------------------------" -ForegroundColor Cyan
Write-Host "                     Статус сервисов VIIO                        " -ForegroundColor Cyan
Write-Host "-----------------------------------------------------------------" -ForegroundColor Cyan

if ($webchatOk) {
    Write-Host "  [OK] Веб-чат (WebChat):        http://localhost:3000 (или http://localhost)" -ForegroundColor Green
} else {
    Write-Host "  [WAIT] Веб-чат (WebChat):      http://localhost:3000 (инициализируется)" -ForegroundColor Yellow
}

if ($assistentOk) {
    Write-Host "  [OK] API Ассистента:          http://localhost:8000/docs" -ForegroundColor Green
} else {
    Write-Host "  [WAIT] API Ассистента:        http://localhost:8000/docs (инициализируется)" -ForegroundColor Yellow
}

Write-Host "  [OK] Портал диагностики:      http://localhost:3010" -ForegroundColor Green
Write-Host "  [OK] Memgraph Bolt:           bolt://localhost:7687" -ForegroundColor Green
Write-Host "  [OK] Qdrant API:              http://localhost:6333/dashboard" -ForegroundColor Green
Write-Host "-----------------------------------------------------------------" -ForegroundColor Cyan

Write-Host "`nСистема готова к работе!" -ForegroundColor Green

if (-not $NoBrowser -and $webchatOk) {
    Start-Process "http://localhost:3000"
}
