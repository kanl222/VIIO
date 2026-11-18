<#
.SYNOPSIS
    Быстрый запуск платформы VIIO.
#>

[CmdletBinding()]
param(
    [switch]$Build,
    [string[]]$Profiles = @(),
    [switch]$NoBrowser
)

$TargetScript = Join-Path $PSScriptRoot "scripts\start.ps1"
& $TargetScript @PSBoundParameters
