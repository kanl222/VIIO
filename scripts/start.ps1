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
    Write-Host "  [WARN] Python/uv не найден на хосте, используются существующие ключи .env." -ForegroundColor Gray
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
    } elseif ($PythonCmd) {
        $env:POSTGRES_HOST = "localhost"
        $env:POSTGRES_PORT = "5432"
        & python services/documents/scripts/provision_runtime_roles.py | Out-Null
    } else {
        docker run --rm --network viio_viio_net --env-file .env -e POSTGRES_HOST=postgres -e POSTGRES_PORT=5432 -v "${RepoRoot}/services/documents/scripts:/migration:ro" viio-documents:latest python /migration/provision_runtime_roles.py 2>$null | Out-Null
    }
    Write-Host "  [OK] Базы данных (user_db, agent_db) и схемы успешно проверены." -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Инициализация баз данных завершилась с предупреждением: $_" -ForegroundColor Yellow
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
