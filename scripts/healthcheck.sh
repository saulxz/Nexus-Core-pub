#!/usr/bin/env bash
# ============================================================
#  Nexus-Core — Healthcheck del servicio
#  Comprueba que el endpoint /api/health responde correctamente.
#  Se puede usar desde Docker, cron o CI.
#
#  Uso:
#     chmod +x scripts/healthcheck.sh
#     ./scripts/healthcheck.sh
#     ./scripts/healthcheck.sh http://192.168.1.50:8000
# ============================================================
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
ENDPOINT="${BASE_URL}/api/health"
TIMEOUT=5

echo "[INFO]  Comprobando ${ENDPOINT} ..."

# curl -f devuelve código distinto de 0 ante errores HTTP (4xx/5xx).
if curl -fsS --max-time "${TIMEOUT}" "${ENDPOINT}" > /tmp/nexus_health.json; then
    echo "[ OK ]  El servicio está operativo. Respuesta:"
    cat /tmp/nexus_health.json
    echo
    exit 0
else
    echo "[FAIL]  El servicio no responde correctamente en ${ENDPOINT}" >&2
    exit 1
fi
