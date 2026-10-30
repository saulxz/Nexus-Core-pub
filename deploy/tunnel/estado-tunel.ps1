# Diagnostico del tunel fijo de Nexus-Core.
# Uso:
#   powershell -ExecutionPolicy Bypass -File deploy\tunnel\estado-tunel.ps1
$ErrorActionPreference = 'Continue'

$hostname = 'nexus.example.com'
$cloudflared = Join-Path $env:USERPROFILE '.local\cloudflared\cloudflared.exe'
if (-not (Test-Path -LiteralPath $cloudflared)) {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { $cloudflared = $cmd.Source }
}
$cert = Join-Path $env:USERPROFILE '.cloudflared\cert.pem'

Write-Host '=== Nexus-Core - estado del tunel ===' -ForegroundColor Cyan

Write-Host '[1] Login de Cloudflare (cert.pem)'
if (Test-Path -LiteralPath $cert) {
    Write-Host "    OK: $cert" -ForegroundColor Green
} else {
    Write-Host '    FALTA: ejecuta deploy\tunnel\setup-named-tunnel.ps1' -ForegroundColor Red
}

Write-Host '[2] Tuneles de la cuenta'
if ($cloudflared -and (Test-Path -LiteralPath $cert)) {
    & $cloudflared tunnel list 2>&1 | Select-Object -First 10 | ForEach-Object { Write-Host "    $_" }
} else {
    Write-Host '    No se puede consultar sin login/cloudflared.' -ForegroundColor Yellow
}

Write-Host '[3] Config local (deploy\tunnel\config.yml)'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$config = Join-Path $repo 'deploy\tunnel\config.yml'
if (Test-Path -LiteralPath $config) {
    Write-Host "    OK: $config" -ForegroundColor Green
} else {
    Write-Host '    FALTA: ejecuta deploy\tunnel\setup-named-tunnel.ps1' -ForegroundColor Red
}

Write-Host '[4] Hub local (127.0.0.1:8780)'
try {
    $r = Invoke-RestMethod 'http://127.0.0.1:8780/api/health' -TimeoutSec 3
    Write-Host "    OK: $($r.status) $($r.version)" -ForegroundColor Green
} catch {
    Write-Host '    FALLO: el hub no responde. Arrancalo con Iniciar Nexus Core.cmd' -ForegroundColor Red
}

Write-Host '[5] DNS'
$dns = Resolve-DnsName $hostname -ErrorAction SilentlyContinue
if ($dns) {
    $dns | ForEach-Object { Write-Host "    $($_.Type) $($_.NameHost)$($_.IPAddress)" }
} else {
    Write-Host "    FALLO: $hostname no resuelve (falta el route dns del tunel)." -ForegroundColor Red
}

Write-Host '[6] HTTPS'
try {
    $code = curl.exe -sS -I -m 15 -o NUL -w '%{http_code}' "https://$hostname/" 2>$null
    if ($code -match '^[23]') {
        Write-Host "    OK: HTTP $code" -ForegroundColor Green
    } else {
        Write-Host "    FALLO: HTTP $code (tunel caido o hub parado)" -ForegroundColor Red
    }
} catch {
    Write-Host '    FALLO: no se pudo conectar por HTTPS.' -ForegroundColor Red
}
