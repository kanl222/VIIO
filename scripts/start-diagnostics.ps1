<#
.SYNOPSIS
    Запуск локального веб-сервера диагностики VIIO Diagnostics Dashboard (http://localhost:3010).

.DESCRIPTION
    Инициализирует директории трассировки, настраивает переменные окружения и
    запускает локальный сервер диагностики без необходимости Docker.
#>

[CmdletBinding()]
param(
    [int]$Port = 3010,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " VIIO: Запуск портала локальной диагностики (Diagnostics Portal)" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# Создание необходимых каталогов трассировки
$TracesDir = Join-Path $RepoRoot "tmp\traces"
$ReviewsDir = Join-Path $RepoRoot "tmp\reviews"

$Subdirs = @("main", "consultant", "rag", "documents", "document-worker")
foreach ($sub in $Subdirs) {
    $dir = Join-Path $TracesDir $sub
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
if (-not (Test-Path $ReviewsDir)) {
    New-Item -ItemType Directory -Path $ReviewsDir -Force | Out-Null
}

Write-Host " Каталог трасс:    $TracesDir" -ForegroundColor Gray
Write-Host " Каталог разборов: $ReviewsDir" -ForegroundColor Gray

# Определение интерпретатора Python
$PythonExe = Join-Path $RepoRoot "services\rag\.venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = Join-Path $RepoRoot "services\assistent\.venv\Scripts\python.exe"
}
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

Write-Host " Python:           $PythonExe" -ForegroundColor Gray

# Переменные окружения для локального запуска
$env:TRACE_ROOTS = "$($TracesDir)\main,$($TracesDir)\consultant,$($TracesDir)\rag,$($TracesDir)\documents,$($TracesDir)\document-worker,$TracesDir"
$env:REVIEW_ROOT = $ReviewsDir
$env:BIND_HOST = "127.0.0.1"

$ServerScript = Join-Path $RepoRoot "services\diagnostics\server.py"

Write-Host "`n Сервер доступен по адресу: http://localhost:$Port" -ForegroundColor Green
Write-Host " Нажмите Ctrl+C для остановки сервера.`n" -ForegroundColor Yellow

if (-not $NoBrowser) {
    Start-Process "http://localhost:$Port"
}

& $PythonExe $ServerScript
