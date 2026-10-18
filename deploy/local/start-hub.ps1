$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$venv = Join-Path $env:USERPROFILE '.local\nexus-venv\Scripts\python.exe'
$backend = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'
$data = Join-Path $env:USERPROFILE '.local\nexus-data'
$logDir = Join-Path $env:TEMP 'opencode'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Test-Hub {
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8780/api/health' -TimeoutSec 2
        return ($r.status -eq 'ok')
    } catch {
        return $false
    }
}

function Open-Panel {
    try { Start-Process 'http://127.0.0.1:8780' } catch { }
}

if (Test-Hub) {
    Write-Host 'El hub ya estaba en marcha.' -ForegroundColor Green
    Open-Panel
    exit 0
}

if (-not (Test-Path -LiteralPath $venv)) {
    throw "No encuentro el entorno virtual: $venv"
}

$env:NEXUS_DATA_DIR = $data
$env:FRONTEND_DIR = $frontend
$env:SESSION_COOKIE_SECURE = 'false'

$log = Join-Path $logDir 'nexus-hub.log'
$errLog = "$log.err"
$p = Start-Process -FilePath $venv `
    -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8780' `
    -WorkingDirectory $backend -WindowStyle Hidden `
    -RedirectStandardOutput $log -RedirectStandardError $errLog -PassThru

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Hub) {
        Write-Host "Hub iniciado (PID $($p.Id))." -ForegroundColor Green
        Open-Panel
        exit 0
    }
}

throw "El hub no respondió. Revisa $errLog"
