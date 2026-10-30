#!/usr/bin/env bash
# ============================================================
#  Nexus-Core — Instalador del AGENTE en un host remoto
#
#  Instala el agente que monitoriza este servidor y lo conecta
#  con el hub. El agente SOLO hace conexiones SALIENTES: no abre
#  ningún puerto ni necesita reglas de firewall de entrada (sirve
#  detrás de NAT).
#
#  Uso:
#     sudo bash deploy/agent/install-agent.sh \
#         --hub https://nexus.midominio.local \
#         --enroll-token TOKEN \
#         --name "nodo-almacen"
#
#  Alternativas:
#     --docker   añade el usuario al grupo `docker` (para que el
#                agente pueda listar y gestionar contenedores).
#
#  Qué hace:
#     1. Crea el usuario de sistema `nexus-agent` (sin shell).
#     2. Copia el proyecto a /opt/nexus-agent.
#     3. Instala las dependencias del subset requirements-agent.txt.
#     4. Enrola el host contra el hub y guarda el token (0600).
#     5. Instala y habilita la unidad systemd nexus-agent.service.
# ============================================================
set -euo pipefail

APP_DIR="/opt/nexus-agent"
CONF_DIR="/etc/nexus-agent"
SERVICE_USER="nexus-agent"
SERVICE_NAME="nexus-agent"
HUB=""
ENROLL_TOKEN=""
HOST_NAME=""
ADD_DOCKER_GROUP="no"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hub)           HUB="$2"; shift 2 ;;
    --enroll-token)  ENROLL_TOKEN="$2"; shift 2 ;;
    --name)          HOST_NAME="$2"; shift 2 ;;
    --docker)        ADD_DOCKER_GROUP="yes"; shift ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *)
      echo "Argumento desconocido: $1" >&2
      exit 1 ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  echo "Ejecuta este script como root: sudo bash deploy/agent/install-agent.sh ..." >&2
  exit 1
fi

if [[ -z "${HUB}" || -z "${ENROLL_TOKEN}" ]]; then
  echo "Faltan parámetros obligatorios: --hub y --enroll-token" >&2
  echo "El token de enrolamiento se obtiene en el panel: GET /api/hosts/enrollment" >&2
  exit 1
fi

if [[ ! "${HUB}" =~ ^https:// ]]; then
  echo "El hub debe usar HTTPS (https://...). No se permite enviar el token por HTTP." >&2
  exit 1
fi

echo "==> Comprobando dependencias del sistema"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Falta python3. Instálalo (apt install python3 python3-pip) y vuelve a ejecutar." >&2
  exit 1
fi
if [[ -z "${HOST_NAME}" ]]; then
  HOST_NAME="$(hostname -s 2>/dev/null || hostname)"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "==> Usuario de sistema '${SERVICE_USER}'"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

echo "==> Directorios"
install -d -m 0755 "${APP_DIR}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0700 "${CONF_DIR}"

echo "==> Copiando el proyecto a ${APP_DIR}"
# Solo el backend: el agente importa los módulos de app/ y su entrypoint.
mkdir -p "${APP_DIR}/app"
cp -a "${SCRIPT_DIR}/backend/app" "${APP_DIR}/"
cp -a "${SCRIPT_DIR}/backend/agent.py" "${APP_DIR}/"
install -m 0644 "${SCRIPT_DIR}/deploy/agent/requirements-agent.txt" "${APP_DIR}/requirements-agent.txt"
chown -R root:root "${APP_DIR}"
find "${APP_DIR}" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> Instalador apt (helper + catalogo)"
install -d -m 0755 /usr/local/lib/nexus-agent
install -m 0755 -o root -g root "${SCRIPT_DIR}/deploy/helpers/pkg-install" /usr/local/lib/nexus-agent/pkg-install
install -d -m 0755 /etc/nexus
install -m 0644 -o root -g root "${SCRIPT_DIR}/deploy/nexus/installable.json" /etc/nexus/installable.json
SUDOERS_PKG="/etc/sudoers.d/nexus-agent-pkg"
printf '%s\n' "nexus-agent ALL=(root) NOPASSWD: /usr/local/lib/nexus-agent/pkg-install" > "${SUDOERS_PKG}"
chown root:root "${SUDOERS_PKG}"
chmod 0440 "${SUDOERS_PKG}"
visudo -cf "${SUDOERS_PKG}"

echo "==> Entorno virtual del agente"
if [[ ! -x "${APP_DIR}/venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/venv"
fi
"${APP_DIR}/venv/bin/pip" install --no-input --upgrade pip
"${APP_DIR}/venv/bin/pip" install --no-input -r "${APP_DIR}/requirements-agent.txt"

if [[ "${ADD_DOCKER_GROUP}" == "yes" ]]; then
  if getent group docker >/dev/null 2>&1; then
    echo "==> Añadiendo '${SERVICE_USER}' al grupo docker"
    usermod -aG docker "${SERVICE_USER}"
  else
    echo "AVISO: no existe el grupo 'docker'; se omite el acceso a contenedores." >&2
  fi
fi

echo "==> Enrolando el host en el hub"
# El token se pasa por entorno, no por línea de comandos, para que no quede
# en la lista de procesos del sistema.
cd "${APP_DIR}"
NEXUS_DATA_DIR="${CONF_DIR}" NEXUS_AGENT_CONFIG="${CONF_DIR}/config.json" \
  runuser -u "${SERVICE_USER}" -- \
  "${APP_DIR}/venv/bin/python" "${APP_DIR}/agent.py" enroll \
    --hub "${HUB}" \
    --enroll-token "${ENROLL_TOKEN}" \
    --name "${HOST_NAME}"

chown "${SERVICE_USER}:${SERVICE_USER}" "${CONF_DIR}/config.json"
chmod 0600 "${CONF_DIR}/config.json"

echo "==> Unidad systemd"
install -m 0644 "${SCRIPT_DIR}/deploy/agent/nexus-agent.service" \
  /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}.service
systemctl restart ${SERVICE_NAME}.service

echo
echo "Agente instalado y conectado."
echo "  Host    : ${HOST_NAME} (hub ${HUB})"
echo "  Estado  : systemctl status ${SERVICE_NAME}"
echo "  Logs    : journalctl -u ${SERVICE_NAME} -f"
echo "  Credenciales: ${CONF_DIR}/config.json (solo root y ${SERVICE_USER})"
echo
echo "No hay que abrir ningún puerto: el agente sale hacia el hub."