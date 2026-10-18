$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cloudflared = Join-Path $env:USERPROFILE '.local\cloudflared\cloudflared.exe'
$logDir = Join-Path $env:TEMP 'opencode'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$outLog = Join-Path $logDir 'cloudflared-quick.log'
$errLog = "$outLog.err"

function Test-Hub {
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8780/api/health' -TimeoutSec 2
        return ($r.status -eq 'ok')
    } catch {
        return $false
    }
}

function Get-TunnelUrl {
    $text = ''
    foreach ($file in @($outLog, $errLog)) {
        if (Test-Path -LiteralPath $file) {
            $text += Get-Content -LiteralPath $file -Raw -ErrorAction SilentlyContinue
        }
    }
    if ($text -match 'https://[a-z0-9-]+\.trycloudflare\.com') {
        return $Matches[0]
    }
    return $null
}

function Show-Url([string]$url) {
    try { Set-Clipboard -Value $url } catch { }
    Write-Host ''
    Write-Host "URL pública: $url" -ForegroundColor Green
    Write-Host 'Copiada al portapapeles.' -ForegroundColor DarkGray
    try { Start-Process $url } catch { }
}

if (-not (Test-Hub)) {
    Write-Host 'El hub no está en marcha; lo arranco primero...' -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot 'start-hub.ps1')
    Start-Sleep -Seconds 2
    if (-not (Test-Hub)) {
        throw 'El hub no responde; no se puede abrir el túnel.'
    }
}

$existing = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -eq 'cloudflared.exe' -and $_.CommandLine -match '--url\s+http://127\.0\.0\.1:8780' }

if ($existing) {
    $url = Get-TunnelUrl
    if (-not $url) {
        throw "Hay un túnel activo (PID $($existing[0].ProcessId)) pero no encuentro la URL en el log. Ciérralo y vuelve a abrir este acceso."
    }
    Write-Host 'El túnel ya estaba en marcha.' -ForegroundColor Green
    Show-Url $url
    Write-Host "Túnel PID $($existing[0].ProcessId). Para cerrarlo: Stop-Process -Id $($existing[0].ProcessId)" -ForegroundColor DarkGray
    exit 0
}

if (-not (Test-Path -LiteralPath $cloudflared)) {
    throw "No encuentro cloudflared en $cloudflared. Descárgalo desde https://github.com/cloudflare/cloudflared/releases/latest"
}

Remove-Item -LiteralPath $outLog, $errLog -ErrorAction SilentlyContinue

$p = Start-Process -FilePath $cloudflared `
    -ArgumentList 'tunnel','--url','http://127.0.0.1:8780','--no-autoupdate' `
    -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru

$url = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    $url = Get-TunnelUrl
    if ($url) { break }
}

if (-not $url) {
    $tail = Get-Content -LiteralPath $errLog -Tail 20 -ErrorAction SilentlyContinue
    if ($tail) { $tail | Write-Host }
    throw 'Cloudflare no devolvió una URL. Revisa la conexión a Internet.'
}

Show-Url $url
Write-Host "Túnel PID $($p.Id). Para cerrarlo: Stop-Process -Id $($p.Id)" -ForegroundColor DarkGray
