<#
.SYNOPSIS
  Cierra Nexus-Core en Windows: lanzador visible, tuneles, supervisor y,
  opcionalmente, el hub.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File deploy\local\stop-nexus-stack.ps1
  powershell -ExecutionPolicy Bypass -File deploy\local\stop-nexus-stack.ps1 -IncluirHub
#>
param(
    [switch]$IncluirHub
)

$ErrorActionPreference = 'Continue'
$tarea = 'Nexus-Core-Autostart'

$t = Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
if ($t) {
    if ($t.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
        Write-Host "Tarea '$tarea' detenida." -ForegroundColor Green
    } else {
        Write-Host "La tarea '$tarea' no estaba en ejecucion." -ForegroundColor DarkGray
    }
} else {
    Write-Host "No existe la tarea '$tarea'." -ForegroundColor DarkGray
}

# Supervisor lanzado a mano (fuera de la tarea)
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'start-nexus-stack\.ps1' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Tuneles de Nexus: el fijo (nexus-core) y el rapido (--url 127.0.0.1:8780)
Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -match 'tunnel\s+run\s+nexus-core' -or
            $_.CommandLine -match '--url\s+http://127\.0\.0\.1:8780'
        )
    } |
    ForEach-Object {
        & taskkill /T /F /PID $_.ProcessId 2>$null | Out-Null
        Write-Host "Tunel detenido (PID $($_.ProcessId))." -ForegroundColor Green
    }

if ($IncluirHub) {
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
            & taskkill /T /F /PID $p.ProcessId 2>$null | Out-Null
            Write-Host "Hub detenido (arbol PID $($p.ProcessId))." -ForegroundColor Green
        }
    }
}

# App de escritorio (Nexus-Core.exe)
Get-CimInstance Win32_Process -Filter "Name='Nexus-Core.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object {
        & taskkill /T /F /PID $_.ProcessId 2>$null | Out-Null
        Write-Host "App de escritorio cerrada (PID $($_.ProcessId))." -ForegroundColor Green
    }

# Lanzador visible (la ventana que se queda esperando ENTER)
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'run-nexus-visible\.ps1' } |
    ForEach-Object {
        & taskkill /T /F /PID $_.ProcessId 2>$null | Out-Null
        Write-Host "Lanzador cerrado (PID $($_.ProcessId))." -ForegroundColor Green
    }

# Ventana del .cmd del lanzador
Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'Abrir Nexus \(dominio\)\.cmd' } |
    ForEach-Object { & taskkill /T /F /PID $_.ProcessId 2>$null | Out-Null }

Write-Host ''
Write-Host 'Nexus-Core cerrado.' -ForegroundColor Green
Write-Host 'El dominio dejara de responder hasta que vuelvas a abrirlo.' -ForegroundColor DarkGray
