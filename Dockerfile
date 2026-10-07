# syntax=docker/dockerfile:1
# ============================================================
#  Nexus-Core — Imagen Docker
#  Objetivo: Raspberry Pi 5 / ARM64 · Debian Bookworm (64-bit)
#  Construida sobre la imagen oficial de Python 3.12 slim.
# ============================================================

FROM python:3.12-slim-bookworm

# --- Metadatos de la imagen (buenas prácticas OCI) ---
LABEL org.opencontainers.image.title="Nexus-Core" \
      org.opencontainers.image.description="Plataforma Web de Orquestacion de Infraestructura y Ciberseguridad con Agente de IA" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.authors="TFG SMR" \
      org.opencontainers.image.licenses="MIT"

# --- Variables de entorno de Python / sistema ---
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Madrid

WORKDIR /app

# --- Dependencias nativas del sistema operativo ---
# Se instalan aquí (antes de copiar el código) para aprovechar la caché
# de capas de Docker: si solo cambia el código, no se reinstala nada.
#
#   nmap           -> descubrimiento de hosts en la subred local
#   iptables       -> consulta de reglas de filtrado del firewall
#   iputils-ping   -> comprobaciones de conectividad ICMP
#   iproute2       -> comandos `ip` y tabla de vecinos ARP
#   net-tools      -> arp/ifconfig (compatibilidad con scripts legacy)
#   curl           -> healthcheck del contenedor
#   ca-certificates-> TLS para las APIs de OpenAI/Anthropic
#   tini           -> init ligero (reapea zombies y propaga señales)
RUN apt-get update && apt-get install -y --no-install-recommends \
        nmap \
        iptables \
        iputils-ping \
        iproute2 \
        net-tools \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# --- Dependencias de Python (capa cacheable independiente) ---
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# --- Código de la aplicación ---
# El backend se ejecuta como paquete `app` (uvicorn app.main:app).
COPY backend/app /app/app
# El frontend se sirve como estáticos desde FastAPI (StaticFiles).
COPY frontend /app/frontend

# --- Usuario no privilegiado ---
# Se crea el grupo `docker` (GID 999, el habitual del socket en Debian) de
# forma tolerante: si ya existiese, no se aborta la build. El usuario `nexus`
# (UID 1000) se añade a dicho grupo para poder operar el socket de Docker.
# docker-compose añade además `group_add: ["999"]` como refuerzo.
RUN groupadd --gid 999 docker 2>/dev/null || true && \
    useradd --create-home --shell /bin/bash --uid 1000 nexus && \
    usermod -aG docker nexus 2>/dev/null || true && \
    mkdir -p /app/data && \
    chown -R nexus:nexus /app

USER nexus

# --- Puerto de la API ---
EXPOSE 8000

# --- Healthcheck interno (Docker lo usa para marcar el contenedor "healthy") ---
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# --- Arranque ---
# tini como PID 1 y uvicorn sirviendo la app FastAPI.
# (En Raspberry Pi se recomienda 1 worker: la CPU es limitada.)
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
