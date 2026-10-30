<#
.SYNOPSIS
  Configura el tunel fijo de Cloudflare para Nexus-Core (nexus.example.com).

.DESCRIPTION
  Primera ejecucion:
    1. Abre el navegador para autorizar Cloudflare (elige la zona labsxz.com).
    2. Crea el tunel "nexus-core" si no existe.
    3. Enlaza el DNS nexus.example.com.
    4. Escribe deploy\tunnel\config.yml.

  Es idempotente: puedes ejecutarlo varias veces sin duplicar nada.
#>
# 'Continue' a proposito: cloudflared escribe su aviso de version por stderr y
# con 'Stop' PowerShell 5.1 aborta el script en la primera llamada nativa.
$ErrorActionPreference = 'Continue'

$nombreTunel = 'nexus-core'
$hostname    = 'nexus.example.com'
$hubUrl      = 'http://127.0.0.1:8780'
$zona        = 'labsxz.com'

function Get-Cloudflared {
    $ruta = Join-Path $env:USERPROFILE '.local\cloudflared\cloudflared.exe'
    if (Test-Path -LiteralPath $ruta) { return $ruta }
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'No encuentro cloudflared. Descargalo desde https://github.com/cloudflare/cloudflared/releases/latest'
}

function Get-Tuneles([string]$exe) {
    $raw = & $exe tunnel list --output json 2>$null
    $texto = ($raw | Out-String).Trim()
    if (-not $texto -or $texto -eq 'null') { return @() }
    try { return @($texto | ConvertFrom-Json) } catch { return @() }
}

$cloudflared = Get-Cloudflared
$configDir = Join-Path $env:USERPROFILE '.cloudflared'
$cert      = Join-Path $configDir 'cert.pem'
$repo      = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$config    = Join-Path $repo 'deploy\tunnel\config.yml'

New-Item -ItemType Directory -Path $configDir -Force | Out-Null

# ── 1. Autorizacion (solo la primera vez) ────────────────────────────
Write-Host '[1/4] Autorizacion de Cloudflare' -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath $cert)) {
    Write-Host "Se abrira el navegador. Inicia sesion con la cuenta duena de $zona" -ForegroundColor Yellow
    Write-Host 'y autoriza esa zona (no cierres la ventana hasta que termine).' -ForegroundColor Yellow
    & $cloudflared tunnel login

    if (-not (Test-Path -LiteralPath $cert)) {
        throw @"
No se creo $cert, asi que el login no se completo.

Revisa:
  - Repite la ejecucion y completa el navegador.
  - Al autorizar, elige explicitamente la zona $zona.
  - Si el navegador no se abre, copia en el navegador la URL que imprime cloudflared.
  - Comprueba que la cuenta de Cloudflare es la duena de $zona.
"@
    }
} else {
    Write-Host 'Ya existe cert.pem: login hecho.' -ForegroundColor Green
}

# ── 2. Tunel ─────────────────────────────────────────────────────────
Write-Host '[2/4] Tunel' -ForegroundColor Cyan
$tuneles = Get-Tuneles $cloudflared
$tunel = $tuneles | Where-Object { $_.name -eq $nombreTunel } | Select-Object -First 1

if (-not $tunel) {
    Write-Host "Creando tunel '$nombreTunel'..." -ForegroundColor Cyan
    & $cloudflared tunnel create $nombreTunel
    if ($LASTEXITCODE -ne 0) {
        throw "cloudflared tunnel create fallo. Codigo: $LASTEXITCODE"
    }
    $tuneles = Get-Tuneles $cloudflared
    $tunel = $tuneles | Where-Object { $_.name -eq $nombreTunel } | Select-Object -First 1
}

if (-not $tunel) {
    throw 'El tunel no aparece en la lista tras crearlo.'
}
Write-Host "Tunel: $($tunel.name)  UUID: $($tunel.id)" -ForegroundColor Green

$uuid = $tunel.id
$credenciales = Join-Path $configDir "$uuid.json"
if (-not (Test-Path -LiteralPath $credenciales)) {
    throw "No encuentro las credenciales del tunel en $credenciales"
}

# ── 3. DNS ───────────────────────────────────────────────────────────
Write-Host "[3/4] DNS $hostname" -ForegroundColor Cyan
$salidaDns = & $cloudflared tunnel route dns $nombreTunel $hostname 2>&1
$textoDns = ($salidaDns | Out-String)
$salidaDns | ForEach-Object { Write-Host "  $_" }

if ($textoDns -match 'zone not found|not authorized|unauthorized|code: 1001|code: 9109') {
    throw @"
No se pudo crear el DNS de $hostname.

Causa probable: la cuenta autorizada en cert.pem no es la duena de $zona.
Solucion:
  1. Borra $cert
  2. Repite este script y autoriza la cuenta correcta.
"@
}
if ($textoDns -match 'already exists|already configured') {
    Write-Host 'El registro DNS ya existia: se reutiliza.' -ForegroundColor Green
}

# ── 4. Configuracion local ───────────────────────────────────────────
Write-Host '[4/4] Configuracion local' -ForegroundColor Cyan
$contenido = @"
# Generado por deploy/tunnel/setup-named-tunnel.ps1 - no editar a mano.
tunnel: $uuid
credentials-file: $credenciales

ingress:
  - hostname: $hostname
    service: $hubUrl
  - service: http_status:404
"@
Set-Content -LiteralPath $config -Value $contenido -Encoding UTF8 -ErrorAction Stop

Write-Host ''
Write-Host "Listo: https://$hostname -> $hubUrl" -ForegroundColor Green
Write-Host "Configuracion: $config" -ForegroundColor DarkGray
Write-Host 'Arranca el tunel con: Abrir tunel Nexus (fijo).cmd' -ForegroundColor DarkGray
