# Arranca un túnel público temporal de Cloudflare hacia el hub local.
# Uso:
#   powershell -ExecutionPolicy Bypass -File deploy/tunnel/start-quick-tunnel.ps1
#   powershell ... -HubUrl http://127.0.0.1:8000
#
# Requiere cloudflared. Si no está en la ruta indicada:
#   https://github.com/cloudflare/cloudflared/releases/latest
param(
    [string]$HubUrl = "http://127.0.0.1:8780",
    [string]$Cloudflared = "$env:USERPROFILE\.local\cloudflared\cloudflared.exe"
)

if (-not (Test-Path -LiteralPath $Cloudflared)) {
    throw "No encuentro cloudflared en $Cloudflared. Descárgalo o pasa -Cloudflared <ruta>."
}

Write-Host "Túnel público temporal hacia $HubUrl" -ForegroundColor Cyan
Write-Host "Pulsa Ctrl+C para cerrarlo. La URL cambia en cada arranque." -ForegroundColor DarkGray
& $Cloudflared tunnel --url $HubUrl --no-autoupdate
