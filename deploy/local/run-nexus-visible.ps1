<#
.SYNOPSIS
  Arranca Nexus-Core en primer plano y abre https://nexus.labsxz.com.

.DESCRIPTION
  Todo ocurre en esta ventana, a la vista:
    - Arranca el hub (uvicorn) si no esta en marcha.
    - Arranca el tunel fijo de Cloudflare (nexus-core) si no esta en marcha.
    - Espera a que el dominio responda y lo abre en el navegador.
    - Al pulsar ENTER (o cerrar la ventana) se detienen los procesos que
      haya arrancado este script.

  Parametros:
    -NoBrowser   No abre el navegador.
    -SoloPrueba  Comprueba y se detiene solo (para tests).
#>
param(
    [switch]$NoBrowser,
    [switch]$SoloPrueba
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repo     = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$venv     = Join-Path $env:USERPROFILE '.local\nexus-venv\Scripts\python.exe'
$backend  = Join-Path $repo 'backend'
$frontend = Join-Path $repo 'frontend'
$data     = Join-Path $env:USERPROFILE '.local\nexus-data'
$config   = Join-Path $repo 'deploy\tunnel\config.yml'
$dominio  = 'https://nexus.labsxz.com'

$hubPid = $null
$tunelPid = $null

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

function Test-Dominio {
    $code = curl.exe -sS -m 10 --doh-url https://cloudflare-dns.com/dns-query -o NUL -w '%{http_code}' $dominio 2>$null
    return ($code -match '^[23]')
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Cyan
Write-Host '  Nexus-Core  ->  https://nexus.labsxz.com' -ForegroundColor Cyan
Write-Host '==============================================' -ForegroundColor Cyan
Write-Host ''

try {
    # â”€â”€ 1. Hub â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if (Test-Hub) {
        Write-Host '[hub] Ya estaba en marcha.' -ForegroundColor Green
    } else {
        if (-not (Test-Path -LiteralPath $venv)) {
            throw "No existe el entorno virtual: $venv"
        }

        # Restos de un arranque anterior que no responde: limpiar el arbol.
        $restos = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and
                $_.CommandLine -match 'uvicorn\s+app\.main:app' -and
                $_.CommandLine -match '--port\s+8780'
            }
        if ($restos) {
            $ids = @($restos | ForEach-Object { $_.ProcessId })
            $raices = @($restos | Where-Object { $ids -notcontains $_.ParentProcessId })
            foreach ($p in $raices) { & taskkill /T /F /PID $p.ProcessId 2>$null | Out-Null }
            Start-Sleep -Seconds 2
        }

        $env:NEXUS_DATA_DIR = $data
        $env:FRONTEND_DIR = $frontend
        $env:SESSION_COOKIE_SECURE = 'false'
        $env:ALLOWED_HOSTS = 'nexus.labsxz.com,127.0.0.1,localhost'
        $env:ENFORCE_HTTPS = 'true'

        Write-Host '[hub] Arrancando... (sus logs salen aqui abajo)' -ForegroundColor Yellow
        $procesoHub = Start-Process -FilePath $venv `
            -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8780' `
            -WorkingDirectory $backend -NoNewWindow -PassThru
        $hubPid = $procesoHub.Id

        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Seconds 1
            if (Test-Hub) { break }
            Write-Host '.' -NoNewline
        }
        Write-Host ''
        if (-not (Test-Hub)) {
            throw 'El hub no respondio en 40 segundos.'
        }
        Write-Host "[hub] OK (PID $hubPid)." -ForegroundColor Green
    }

    # â”€â”€ 2. Tunel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if (Get-ProcesosTunel) {
        Write-Host '[tunel] Ya estaba en marcha.' -ForegroundColor Green
    } else {
        if (-not (Test-Path -LiteralPath $config)) {
            Write-Host '[tunel] Falta la configuracion; generandola...' -ForegroundColor Yellow
            & (Join-Path $PSScriptRoot 'setup-named-tunnel.ps1')
            if (-not (Test-Path -LiteralPath $config)) {
                throw 'No se pudo generar la configuracion del tunel.'
            }
        }

        $cloudflared = Get-Cloudflared
        if (-not $cloudflared) { throw 'No encuentro cloudflared.' }

        Write-Host '[tunel] Arrancando... (sus logs salen aqui abajo)' -ForegroundColor Yellow
        $procesoTunel = Start-Process -FilePath $cloudflared `
            -ArgumentList "--config `"$config`" tunnel run nexus-core" `
            -NoNewWindow -PassThru
        $tunelPid = $procesoTunel.Id
    }

    # â”€â”€ 3. Esperar al dominio y abrirlo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    Write-Host "[web] Esperando a $dominio ..." -ForegroundColor Yellow
    $activo = $false
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Dominio) { $activo = $true; break }
        Start-Sleep -Seconds 2
        Write-Host '.' -NoNewline
    }
    Write-Host ''

    if ($activo) {
        Write-Host "[web] Activo: $dominio" -ForegroundColor Green
    } else {
        Write-Host '[web] El dominio aun no responde. Revisa los logs de arriba.' -ForegroundColor Yellow
    }

    if (-not (Resolve-DnsName 'nexus.labsxz.com' -ErrorAction SilentlyContinue)) {
        Write-Host '[dns] Vaciando la cache DNS local...' -ForegroundColor Yellow
        & ipconfig /flushdns 2>$null | Out-Null
        Start-Sleep -Seconds 1
    }
    if (-not (Resolve-DnsName 'nexus.labsxz.com' -ErrorAction SilentlyContinue)) {
        Write-Host '[aviso] Tu DNS local aun no resuelve el dominio.' -ForegroundColor Yellow
        Write-Host '        Cambia el DNS de la Wi-Fi a 1.1.1.1 o espera unos minutos.' -ForegroundColor Yellow
    }

    if (-not $NoBrowser -and $activo) {
        Start-Process $dominio | Out-Null
        Write-Host '[web] Abierto en el navegador.' -ForegroundColor Green
    }

    if ($SoloPrueba) {
        Write-Host '[test] Modo prueba: se detiene solo.' -ForegroundColor DarkGray
        return
    }

    Write-Host ''
    Write-Host 'Deja esta ventana abierta mientras quieras Nexus activo.' -ForegroundColor Cyan
    Write-Host 'Pulsa ENTER para detenerlo.' -ForegroundColor Cyan
    [void][Console]::ReadLine()
}
finally {
    Write-Host ''
    Write-Host '[stop] Deteniendo Nexus-Core...'
    if ($tunelPid) {
        & taskkill /T /F /PID $tunelPid 2>$null | Out-Null
        Write-Host "[stop] Tunel detenido (PID $tunelPid)."
    }
    if ($hubPid) {
        & taskkill /T /F /PID $hubPid 2>$null | Out-Null
        Write-Host "[stop] Hub detenido (PID $hubPid)."
    }
    Write-Host '[stop] Listo. El dominio dejara de responder hasta que lo vuelvas a abrir.'
}
