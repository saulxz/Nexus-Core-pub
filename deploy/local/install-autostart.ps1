<#
.SYNOPSIS
  Instala el arranque automatico de Nexus-Core en Windows (hub + tunel fijo).

.DESCRIPTION
  Registra una tarea programada que se ejecuta al iniciar sesion, en segundo
  plano, y mantiene vivos:
    - el hub en 127.0.0.1:8780
    - el tunel de Cloudflare nexus-core (https://nexus.labsxz.com)

  No necesita privilegios de administrador: la tarea corre como tu usuario.
  Es idempotente: volver a ejecutarlo actualiza la tarea.
#>
$ErrorActionPreference = 'Stop'

$tarea   = 'Nexus-Core-Autostart'
$script  = Join-Path $PSScriptRoot 'start-nexus-stack.ps1'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$usuario = '{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME

if (-not (Test-Path -LiteralPath $script)) {
    throw "No encuentro $script"
}

$accion = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Supervisar"

$disparador = New-ScheduledTaskTrigger -AtLogOn -User $usuario
try { $disparador.Delay = 'PT20S' } catch { }

$ajustes = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -Hidden

$principal = New-ScheduledTaskPrincipal -UserId $usuario -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $tarea `
    -Action $accion `
    -Trigger $disparador `
    -Settings $ajustes `
    -Principal $principal `
    -Description 'Arranca Nexus-Core (hub + tunel nexus.labsxz.com) al iniciar sesion' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $tarea

Start-Sleep -Seconds 3
$estado = (Get-ScheduledTask -TaskName $tarea).State
Write-Host ''
Write-Host "Tarea '$tarea' instalada." -ForegroundColor Green
Write-Host "  Estado : $estado"
Write-Host "  Usuario: $usuario"
Write-Host "  Logs   : $env:LOCALAPPDATA\Nexus-Core\logs"
Write-Host ''
Write-Host 'Parar:     deploy\local\stop-nexus-stack.ps1 -IncluirHub' -ForegroundColor DarkGray
Write-Host 'Desinstalar: deploy\local\uninstall-autostart.ps1' -ForegroundColor DarkGray
