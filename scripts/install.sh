#!/usr/bin/env bash
# ============================================================
#  Nexus-Core — Script de instalación para Raspberry Pi / Debian
#  Instala Docker Engine + Compose Plugin y prepara el entorno.
#
#  Uso:
#     chmod +x scripts/install.sh
#     sudo ./scripts/install.sh
#
#  Probado en Raspberry Pi OS Lite 64-bit (Debian Bookworm, ARM64).
# ============================================================
set -euo pipefail

# --------------------------- Colores ---------------------------
readonly C_RESET='\033[0m'
readonly C_INFO='\033[1;34m'
readonly C_OK='\033[1;32m'
readonly C_WARN='\033[1;33m'
readonly C_ERR='\033[1;31m'

log_info()  { echo -e "${C_INFO}[INFO]${C_RESET}  $*"; }
log_ok()    { echo -e "${C_OK}[ OK ]${C_RESET}  $*"; }
log_warn()  { echo -e "${C_WARN}[WARN]${C_RESET}  $*"; }
log_error() { echo -e "${C_ERR}[FAIL]${C_RESET}  $*" >&2; }

# --------------------- Comprobaciones previas ------------------
if [[ "${EUID}" -ne 0 ]]; then
    log_error "Este script debe ejecutarse como root: sudo ./scripts/install.sh"
    exit 1
fi

log_info "Detectando arquitectura del sistema..."
ARCH="$(uname -m)"
case "${ARCH}" in
    aarch64|arm64) log_ok "Arquitectura ARM64 detectada (${ARCH})." ;;
    armv7l|armhf)  log_warn "Arquitectura ARM 32-bit detectada (${ARCH}). Se recomienda 64-bit." ;;
    x86_64)        log_warn "Arquitectura x86_64 detectada (${ARCH}). No es una Raspberry Pi." ;;
    *)             log_error "Arquitectura no soportada: ${ARCH}"; exit 1 ;;
esac

# --------------------- 1. Paquetes base ------------------------
log_info "Actualizando índices de paquetes e instalando utilidades base..."
apt-get update -y
apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    nmap \
    iputils-ping \
    iproute2 \
    net-tools
log_ok "Paquetes base instalados."

# --------------------- 2. Docker Engine ------------------------
if command -v docker >/dev/null 2>&1; then
    log_ok "Docker ya está instalado: $(docker --version)"
else
    log_info "Instalando Docker Engine (repositorio oficial de Docker)..."
    install -m 0755 -d /etc/apt/keyrings
    if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
        curl -fsSL https://download.docker.com/linux/debian/gpg \
            -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
    fi

    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian \
$(. /etc/os-release && echo "${VERSION_CODENAME}") stable" \
      > /etc/apt/sources.list.d/docker.list

    apt-get update -y
    apt-get install -y --no-install-recommends \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
    log_ok "Docker Engine + Compose Plugin instalados."
fi

# --------------------- 3. Servicio Docker ----------------------
log_info "Habilitando e iniciando el servicio Docker..."
systemctl enable docker
systemctl start docker
log_ok "Servicio Docker activo: $(systemctl is-active docker)"

# --------------------- 4. Grupo docker -------------------------
# Añade al usuario que invocó sudo (o al usuario estándar de la Pi) al
# grupo docker para no tener que usar sudo en cada comando.
TARGET_USER="${SUDO_USER:-${USER}}"
if id "${TARGET_USER}" >/dev/null 2>&1; then
    log_info "Añadiendo al usuario '${TARGET_USER}' al grupo 'docker'..."
    usermod -aG docker "${TARGET_USER}"
    log_ok "Usuario '${TARGET_USER}' añadido al grupo docker."
    log_warn "Cierra sesión y vuelve a entrar (o ejecuta 'newgrp docker') para aplicarlo."
else
    log_warn "No se pudo determinar el usuario objetivo; añádelo manualmente: usermod -aG docker <usuario>"
fi

# --------------------- 5. Comprobación final -------------------
log_info "Verificando instalación..."
docker --version
docker compose version || true

if docker info >/dev/null 2>&1; then
    log_ok "El daemon de Docker responde correctamente."
else
    log_warn "El daemon de Docker no responde todavía; espera unos segundos y reintenta."
fi

cat <<'EOF'

============================================================
  Instalación completada.
  Siguientes pasos:
    1) cd Nexus-Core
    2) cp .env.example .env
    3) Edita .env y añade tu OPENAI_API_KEY (o configura Ollama)
    4) docker compose up -d --build
    5) Abre http://<IP-de-la-raspberry>:8000
============================================================
EOF
