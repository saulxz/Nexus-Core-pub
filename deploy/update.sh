#!/usr/bin/env bash
# ============================================================
#  Nexus-Core — Actualizador del despliegue nativo.
#
#  Copia el código actualizado (backend + frontend) desde este
#  directorio a /opt/nexus-core, arregla el acceso al socket de Docker
#  (grupo docker) y reinicia el servicio.
#
#  Uso:
#     cd ~/nexus-src && sudo bash deploy/update.sh
# ============================================================
set -euo pipefail

APP_DIR="/opt/nexus-core"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ${EUID} -ne 0 ]]; then
  echo "Ejecútalo con sudo: sudo bash deploy/update.sh" >&2
  exit 1
fi

if [[ ! -d "${APP_DIR}" ]]; then
  echo "No existe ${APP_DIR}; usa primero deploy/install.sh" >&2
  exit 1
fi

echo "==> Copiando backend y frontend actualizados"
cp -a "${SCRIPT_DIR}/backend/." "${APP_DIR}/backend/"
cp -a "${SCRIPT_DIR}/frontend/." "${APP_DIR}/frontend/"

echo "==> Acceso al socket de Docker (grupo docker)"
if getent group docker >/dev/null 2>&1; then
  usermod -aG docker nexus 2>/dev/null || true
  systemctl daemon-reload
fi

echo "==> Reiniciando el servicio"
systemctl restart nexus.service
sleep 2
if systemctl is-active --quiet nexus.service; then
  echo "==> Nexus-Core actualizado y en marcha."
else
  echo "El servicio no arrancó; revisa: journalctl -u nexus -n 50" >&2
  exit 1
fi
