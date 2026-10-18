#!/usr/bin/env bash
# ============================================================
#  Nexus-Core — comprobación rápida de producción
#  Uso: bash deploy/production/check-production.sh nexus.example.com
#  Comprueba salud, redirección a HTTPS y cabeceras de Caddy.
# ============================================================
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "${DOMAIN}" ]]; then
  echo "Uso: $0 <dominio-sin-esquema>" >&2
  exit 2
fi

BASE="https://${DOMAIN}"

echo "==> Salud de la API"
curl -fsS --max-time 10 "${BASE}/api/health" | grep -q '"status":"ok"'

echo "==> Redirección HTTP → HTTPS"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://${DOMAIN}/")"
if [[ "${CODE}" != "301" && "${CODE}" != "308" ]]; then
  echo "Esperaba 301/308 y llegó ${CODE}" >&2
  exit 1
fi

echo "==> Cabeceras de seguridad"
HEADERS="$(curl -fsSI --max-time 10 "${BASE}/login.html")"
echo "${HEADERS}" | grep -qi '^strict-transport-security:'
echo "${HEADERS}" | grep -qi '^x-content-type-options: nosniff'
echo "${HEADERS}" | grep -qi '^x-frame-options: DENY'
echo "${HEADERS}" | grep -qi '^referrer-policy:'
echo "${HEADERS}" | grep -qi '^permissions-policy:'

echo "OK: ${BASE} responde con TLS válido y cabeceras de seguridad."
