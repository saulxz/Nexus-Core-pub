# 'Continue': el aviso de version de cloudflared va por stderr y con 'Stop'
# PowerShell 5.1 abortaria antes de arrancar el tunel.
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$config = Join-Path $repo 'deploy\tunnel\config.yml'

$cloudflared = Join-Path $env:USERPROFILE '.local\cloudflared\cloudflared.exe'
if (-not (Test-Path -LiteralPath $cloudflared)) {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) {
        $cloudflared = $cmd.Source
    } else {
        throw 'No encuentro cloudflared.'
    }
}

# Primera vez: configurar automaticamente (login + tunel + DNS + config).
if (-not (Test-Path -LiteralPath $config)) {
    Write-Host 'Falta la configuracion del tunel fijo; la genero ahora...' -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot 'setup-named-tunnel.ps1')
    if (-not (Test-Path -LiteralPath $config)) {
        throw 'La configuracion no se genero. Revisa los errores de arriba.'
    }
}

function Test-Hub {
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8780/api/health' -TimeoutSec 2
        return ($r.status -eq 'ok')
    } catch {
        return $false
    }
}

if (-not (Test-Hub)) {
    Write-Host 'El hub no esta en marcha; lo arranco primero...' -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot '..\local\start-hub.ps1')
    Start-Sleep -Seconds 2
    if (-not (Test-Hub)) {
        throw 'El hub no responde; no se puede abrir el tunel.'
    }
}

Write-Host 'Tunel fijo: https://nexus.labsxz.com (Ctrl+C para cerrar)' -ForegroundColor Cyan
& $cloudflared --config $config tunnel run nexus-core
