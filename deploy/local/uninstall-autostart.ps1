<#
.SYNOPSIS
  Quita el arranque automatico de Nexus-Core en Windows.

.DESCRIPTION
  Elimina la tarea programada. No detiene los procesos en marcha: para eso,
  ejecuta antes stop-nexus-stack.ps1.
#>
$ErrorActionPreference = 'Continue'
$tarea = 'Nexus-Core-Autostart'

$t = Get-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
if (-not $t) {
    Write-Host "La tarea '$tarea' no existe." -ForegroundColor DarkGray
    exit 0
}

if ($t.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $tarea -ErrorAction SilentlyContinue
}
Unregister-ScheduledTask -TaskName $tarea -Confirm:$false
Write-Host "Tarea '$tarea' eliminada." -ForegroundColor Green
