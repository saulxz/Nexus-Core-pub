#!/usr/bin/env bash
# ============================================================
#  Nexus-Core — Instalador del despliegue nativo (VM Debian/Ubuntu)
#
#  Instala: usuario de sistema, directorios de datos, entorno virtual,
#  unidad systemd, helpers privilegiados (root), lista blanca de unidades
#  y reglas sudoers (validadas con visudo antes de instalarse).
#
#  Uso:
#     sudo bash deploy/install.sh
# ============================================================
set -euo pipefail

APP_DIR="/opt/nexus-core"
DATA_DIR="/var/lib/nexus"
SERVICE_USER="nexus"
HELPERS_DIR="/usr/local/lib/nexus"

if [[ ${EUID} -ne 0 ]]; then
  echo "Ejecuta este script como root: sudo bash deploy/install.sh" >&2
  exit 1
fi

echo "==> Comprobando dependencias del sistema"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Falta python3. Instálalo y vuelve a ejecutar." >&2
  exit 1
fi
if ! command -v visudo >/dev/null 2>&1; then
  echo "Falta visudo (paquete sudo). Instálalo y vuelve a ejecutar." >&2
  exit 1
fi

echo "==> Usuario de sistema '${SERVICE_USER}'"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

echo "==> Directorios"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0700 "${DATA_DIR}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0700 "${DATA_DIR}/staging"
install -d -o root -g "${SERVICE_USER}" -m 0750 /var/backups/nexus
install -d -m 0755 "${APP_DIR}"

echo "==> Copiando el proyecto a ${APP_DIR}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cp -a "${SCRIPT_DIR}/." "${APP_DIR}/"
chown -R root:root "${APP_DIR}"

echo "==> Entorno virtual de Python"
VENV_DIR="${APP_DIR}/.venv"
# Wheelhouse: se prefiere el del código fuente (garantizado) y, si no, el copiado.
WHEELHOUSE_DIR="${SCRIPT_DIR}/wheelhouse"
if [[ ! -d "${WHEELHOUSE_DIR}" ]]; then
  WHEELHOUSE_DIR="${APP_DIR}/wheelhouse"
fi
if [[ ! -x "${VENV_DIR}/bin/python" ]] || [[ ! -x "${VENV_DIR}/bin/pip" ]]; then
  rm -rf "${VENV_DIR}"
  if ! python3 -m venv "${VENV_DIR}" 2>/dev/null || [[ ! -x "${VENV_DIR}/bin/pip" ]]; then
    echo "==> ensurepip no disponible; creando el entorno con virtualenv (ruedas locales)"
    rm -rf "${VENV_DIR}"
    WHEELS_COUNT="$(ls "${WHEELHOUSE_DIR}" 2>/dev/null | wc -l)"
    echo "    ruedas disponibles en ${WHEELHOUSE_DIR}: ${WHEELS_COUNT}"
    if [[ ! -d "${WHEELHOUSE_DIR}" ]] || [[ "${WHEELS_COUNT}" -lt 10 ]]; then
      echo "Wheelhouse ausente o incompleto; instala python3-venv y reintenta." >&2
      exit 1
    fi
    BOOT="$(mktemp -d)"
    for wheel in "${WHEELHOUSE_DIR}"/virtualenv-*.whl "${WHEELHOUSE_DIR}"/distlib-*.whl "${WHEELHOUSE_DIR}"/filelock-*.whl "${WHEELHOUSE_DIR}"/platformdirs-*.whl "${WHEELHOUSE_DIR}"/packaging-*.whl "${WHEELHOUSE_DIR}"/python_discovery-*.whl; do
      if [[ -e "${wheel}" ]]; then
        python3 -m zipfile -e "${wheel}" "${BOOT}"
      fi
    done
    if ! PYTHONPATH="${BOOT}" python3 -m virtualenv --no-download "${VENV_DIR}"; then
      rm -rf "${BOOT}"
      echo "Fallo creando el entorno virtual con virtualenv." >&2
      exit 1
    fi
    rm -rf "${BOOT}"
  fi
fi

echo "==> Dependencias de Python"
PIP="${VENV_DIR}/bin/pip"
"${PIP}" install --upgrade pip
if [[ -d "${WHEELHOUSE_DIR}" ]] && [[ "$(ls "${WHEELHOUSE_DIR}" 2>/dev/null | wc -l)" -ge 10 ]]; then
  echo "    (usando las ruedas locales de ${WHEELHOUSE_DIR})"
  "${PIP}" install --find-links "${WHEELHOUSE_DIR}" -r "${APP_DIR}/requirements.txt"
else
  "${PIP}" install -r "${APP_DIR}/requirements.txt"
fi

echo "==> Helpers privilegiados (root)"
install -d -m 0755 "${HELPERS_DIR}"
install -m 0755 -o root -g root "${APP_DIR}/deploy/helpers/"* "${HELPERS_DIR}/"

echo "==> Lista blanca de unidades controlables"
install -d -m 0755 /etc/nexus
install -m 0644 -o root -g root "${APP_DIR}/deploy/nexus/allowed-units" /etc/nexus/allowed-units
install -m 0644 -o root -g root "${APP_DIR}/deploy/nexus/installable.json" /etc/nexus/installable.json

echo "==> Reglas sudoers (validando sintaxis antes de instalar)"
visudo -cf "${APP_DIR}/deploy/sudoers.d/nexus"
install -m 0440 -o root -g root "${APP_DIR}/deploy/sudoers.d/nexus" /etc/sudoers.d/nexus

echo "==> Unidad systemd"
install -m 0644 "${APP_DIR}/deploy/systemd/nexus.service" /etc/systemd/system/nexus.service
systemctl daemon-reload
systemctl enable --now nexus.service
# En reinstalaciones, garantiza que el servicio ejecuta el código recién copiado.
systemctl restart nexus.service

echo
echo "Nexus-Core instalado."
echo "  Estado : systemctl status nexus"
echo "  Logs   : journalctl -u nexus -f   (incluye el TOKEN DE CONFIGURACIÓN)"
echo "  Panel  : http://<IP-de-la-VM>:8000/login.html"
echo
echo "El token de primer arranque también está en: ${DATA_DIR}/setup_token"
echo "Unidades controlables desde el panel: /etc/nexus/allowed-units"
