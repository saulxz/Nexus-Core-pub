# ============================================================
#  Nexus-Core — Makefile de operaciones habituales
#  Ejecuta `make help` para ver todos los objetivos disponibles.
# ============================================================

COMPOSE := docker compose
SERVICE := nexus-core
IMAGE   := nexus-core:1.0.0

.DEFAULT_GOAL := help
.PHONY: help up down build rebuild restart logs ps test test-local clean scan-help

## help: muestra esta ayuda
help:
	@echo "Nexus-Core — objetivos disponibles:"
	@echo "  make up          Levanta la plataforma en segundo plano (--build)"
	@echo "  make down        Detiene y elimina los contenedores"
	@echo "  make build       Construye la imagen Docker"
	@echo "  make rebuild     Reconstruye sin caché"
	@echo "  make restart     Reinicia el servicio"
	@echo "  make logs        Muestra los logs en vivo"
	@echo "  make ps          Lista el estado de los contenedores"
	@echo "  make test        Ejecuta los tests dentro de Docker"
	@echo "  make test-local  Ejecuta pytest en el host (requiere deps)"
	@echo "  make scan-help   Muestra la subred configurada para escaneos"
	@echo "  make clean       Elimina contenedores, volúmenes e imagen"

## up: levanta la plataforma
up:
	$(COMPOSE) up -d --build

## down: detiene y elimina contenedores
down:
	$(COMPOSE) down

## build: construye la imagen
build:
	$(COMPOSE) build

## rebuild: reconstruye sin caché
rebuild:
	$(COMPOSE) build --no-cache

## restart: reinicia el servicio
restart:
	$(COMPOSE) restart $(SERVICE)

## logs: logs en vivo
logs:
	$(COMPOSE) logs -f --tail=200 $(SERVICE)

## ps: estado de los contenedores
ps:
	$(COMPOSE) ps

## test: tests dentro de Docker
test:
	$(COMPOSE) run --rm --entrypoint pytest $(SERVICE) -q || true

## test-local: tests en el host
test-local:
	python -m pytest -q

## scan-help: recuerda la subred a escanear
scan-help:
	@echo "Subred configurada en .env:"
	@grep -E '^SCAN_DEFAULT_SUBNET=' .env 2>/dev/null || echo "  SCAN_DEFAULT_SUBNET=192.168.1.0/24 (por defecto)"

## clean: limpieza total (¡borra el volumen de datos!)
clean:
	$(COMPOSE) down -v --rmi local
