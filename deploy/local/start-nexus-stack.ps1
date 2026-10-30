<#
.SYNOPSIS
  Arranca y mantiene vivo Nexus-Core en Windows: hub local + tunel fijo de
  Cloudflare (nexus.labsxz.com).

.DESCRIPTION
  - Si el hub no responde en 127.0.0.1:8780, lo arranca.
  - Si el tunel "nexus-core" no esta corriendo, lo arranca.
  - Con -Supervisar entra en bucle y reinicia lo que se caiga (modo tarea
    programada). Sin el flag hace un solo ciclo y sale.

  Logs: %LOCALAPPDATA%\Nexus-Core\logs
#>
param(
    [switch]$Supervisar,
    [int]$IntervaloSegundos = 15
)

# 'Continue': cloudflared escribe su aviso de version por stderr y con 'Stop'
# PowerShell 5.1 abortaria la llamada nativa.
$ErrorActionPreference = 'Continue'

$repo     = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$venv     = Join-Path $env:USERPROFILE '.local\nexus-venv\Scripts\python.exe'
$backend  = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'
$data     = Join-Path $env:USERPROFILE '.local\nexus-data'
$config   = Join-Path $repo 'deploy\tunnel\config.yml'

$logDir = Join-Path $env:LOCALAPPDATA 'Nexus-Core\logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$supervisorLog = Join-Path $logDir 'supervisor.log'
$hubOut = Join-Path $logDir 'hub.log'
$hubErr = Join-Path $logDir 'hub.err.log'
$tunOut = Join-Path $logDir 'tunnel.log'
$tunErr = Join-Path $logDir 'tunnel.err.log'

function Write-Log([string]$mensaje) {
    $linea = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $mensaje
    Add-Content -LiteralPath $supervisorLog -Value $linea -Encoding UTF8
}

function Get-Cloudflared {
    $ruta = Join-Path $env:USERPROFILE '.local\cloudflared\cloudflared.exe'
    if (Test-Path -LiteralPath $ruta) { return $ruta }
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Test-Hub {
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8780/api/health' -TimeoutSec 2
        return ($r.status -eq 'ok')
    } catch {
        return $false
    }
}

function Get-ProcesosTunel {
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'tunnel\s+run\s+nexus-core' }
}

function Start-Hub {
    if (Test-Hub) { return $true }
    if (-not (Test-Path -LiteralPath $venv)) {
        Write-Log "FALLO: no existe el venv $venv"
        return $false
    }

    # Si hay procesos del hub pero no responde, matamos el arbol completo
    # (uvicorn en Windows usa un proceso padre + hijo).
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match 'uvicorn\s+app\.main:app' -and
            $_.CommandLine -match '--port\s+8780'
        }
    if ($procs) {
        $ids = @($procs | ForEach-Object { $_.ProcessId })
        $raices = @($procs | Where-Object { $ids -notcontains $_.ParentProcessId })
        foreach ($p in $raices) {
            Write-Log "Hub sin salud; matando arbol PID $($p.ProcessId)"
            & taskkill /T /F /PID $p.ProcessId 2>$null | Out-Null
        }
        Start-Sleep -Seconds 2
    }

    $env:NEXUS_DATA_DIR = $data
    $env:FRONTEND_DIR = $frontend
    $env:SESSION_COOKIE_SECURE = 'false'
    $env:ALLOWED_HOSTS = 'nexus.labsxz.com,127.0.0.1,localhost'
    $env:ENFORCE_HTTPS = 'true'

    Start-Process -FilePath $venv `
        -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8780' `
        -WorkingDirectory $backend -WindowStyle Hidden `
        -RedirectStandardOutput $hubOut -RedirectStandardError $hubErr | Out-Null

    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Hub) {
            Write-Log 'Hub arrancado.'
            return $true
        }
    }
    Write-Log 'FALLO: el hub no respondio en 30s.'
    return $false
}

function Start-Tunel {
    if (Get-ProcesosTunel) { return $true }

    $cloudflared = Get-Cloudflared
    if (-not $cloudflared) {
        Write-Log 'FALLO: no encuentro cloudflared.'
        return $false
    }
    if (-not (Test-Path -LiteralPath $config)) {
        Write-Log "FALLO: falta $config. Ejecuta setup-named-tunnel.ps1"
        return $false
    }

    Start-Process -FilePath $cloudflared `
        -ArgumentList "--config `"$config`" tunnel run nexus-core" `
        -WindowStyle Hidden `
        -RedirectStandardOutput $tunOut -RedirectStandardError $tunErr | Out-Null

    Start-Sleep -Seconds 8
    if (Get-ProcesosTunel) {
        Write-Log 'Tunel arrancado.'
        return $true
    }
    Write-Log 'FALLO: el tunel no arranco.'
    return $false
}

function Invoke-Ciclo {
    if (-not (Test-Hub)) {
        Write-Log 'Hub caido: arrancando.'
        [void](Start-Hub)
    }
    if (-not (Get-ProcesosTunel)) {
        Write-Log 'Tunel caido: arrancando.'
        [void](Start-Tunel)
    }
}

Invoke-Ciclo

if (-not $Supervisar) {
    exit 0
}

Write-Log "Supervisor activo (cada $IntervaloSegundos s)."
while ($true) {
    Start-Sleep -Seconds $IntervaloSegundos
    Invoke-Ciclo
}
