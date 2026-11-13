#!/usr/bin/env bash
# ============================================================
#  Actualización controlada del agente Nexus-Core
#
#  Uso:
#    sudo bash deploy/agent/update-agent.sh --source /ruta/al/repo
#
#  No vuelve a enrolar el host ni toca /etc/nexus-agent/config.json.
#  Hace backup de los archivos reemplazados, instala el agente y el helper
#  de auditoría, elimina CRLF y reinicia el servicio.
# ============================================================
set -euo pipefail

SOURCE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Argumento desconocido: $1" >&2; exit 1 ;;
  esac
done

if [[ ${EUID} -ne 0 ]]; then
  echo "Ejecuta este script como root." >&2
  exit 1
fi

if [[ -z "${SOURCE}" ]]; then
  SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
SOURCE="$(cd "${SOURCE}" && pwd)"
for file in backend/agent.py backend/app/modules/security_audit.py deploy/helpers/pkg-install deploy/agent/security-audit-helper; do
  [[ -f "${SOURCE}/${file}" ]] || { echo "Falta ${SOURCE}/${file}" >&2; exit 1; }
done

STAMP="$(date -u +%Y%m%d%H%M%S)"
backup_dir="/var/backups/nexus-agent/${STAMP}"
install -d -m 0700 "${backup_dir}"
for file in /opt/nexus-agent/agent.py /opt/nexus-agent/app/modules/security_audit.py; do
  [[ -f "${file}" ]] && cp -a "${file}" "${backup_dir}/$(basename "${file}")"
done

install -m 0644 "${SOURCE}/backend/agent.py" /opt/nexus-agent/agent.py
install -m 0644 "${SOURCE}/backend/app/modules/security_audit.py" /opt/nexus-agent/app/modules/security_audit.py
install -m 0755 -o root -g root "${SOURCE}/deploy/helpers/pkg-install" /usr/local/lib/nexus-agent/pkg-install
install -m 0755 -o root -g root "${SOURCE}/deploy/agent/security-audit-helper" /usr/local/lib/nexus-agent/security-audit-helper
sed -i 's/\r$//' /opt/nexus-agent/agent.py /opt/nexus-agent/app/modules/security_audit.py /usr/local/lib/nexus-agent/pkg-install /usr/local/lib/nexus-agent/security-audit-helper
install -d -o nexus-agent -g nexus-agent -m 0700 /opt/nexus-agent/data

systemctl restart nexus-agent
sleep 2
systemctl is-active nexus-agent
printf 'Backup: %s\n' "${backup_dir}"
grep -m1 'AGENT_VERSION' /opt/nexus-agent/agent.py
