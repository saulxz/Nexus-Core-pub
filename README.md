# Nexus-Core

> **Panel web de orquestación de infraestructura y ciberseguridad con agente de IA, ejecutándose en el borde (edge) sobre Raspberry Pi.**

![Lenguaje principal](https://img.shields.io/github/languages/top/saulxz/Nexus-Core-pub)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115.6-009688?logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Raspberry Pi](https://img.shields.io/badge/Raspberry%20Pi-ARM64-C51A64?logo=raspberrypi&logoColor=white)

Nexus-Core convierte una Raspberry Pi (o cualquier servidor Linux Debian) en un
**panel de control centralizado** desde el que administrar contenedores Docker,
auditar la red local y consultar la telemetría del sistema, con un **agente de IA
capaz de ejecutar acciones reales** sobre el sistema operativo a partir de
instrucciones en lenguaje natural.

Proyecto desarrollado como *Trabajo Fin de Grado* del Ciclo Formativo de Grado
Superior en Sistemas Microinformáticos y Redes (SMR).

---

## Índice

- [Características](#características)
- [Arquitectura](#arquitectura)
- [Requisitos](#requisitos)
- [Instalación rápida](#instalación-rápida)
- [Configuración](#configuración)
- [API REST](#api-rest)
- [Agente de IA](#agente-de-ia)
- [Seguridad](#seguridad)
- [Estructura del proyecto](#estructura-del-proyecto)

---

## Descripción

La plataforma unifica en una única interfaz web tres capacidades que
habitualmente viven separadas: la **gestión de contenedores Docker**, el
**descubrimiento de hosts en la red local** mediante Nmap y la
**monitorización de la telemetría del sistema** (CPU, memoria, disco y
temperatura).

El elemento diferencial es el **agente de IA con *function calling***: no un
chatbot que genera texto, sino un componente que interpreta la petición en
lenguaje natural, decide qué herramienta del sistema operativo necesita y
devuelve **resultados reales obtenidos del propio sistema**. El catálogo de
herramientas es común; el backend lo traduce al formato que cada proveedor de
LLM exige.

El backend está construido sobre **Python 3.12 y FastAPI**, el frontend es una
interfaz autocontenida en **HTML, JavaScript y Tailwind CSS** servida como
estáticos por la propia API, y el despliegue se apoya en **Docker y Docker
Compose** optimizado para **ARM64**, lo que garantiza portabilidad, aislamiento
y reproducibilidad.

## Características

- **Panel web responsivo** en español, servido por FastAPI como estáticos sin
  paso de build en el frontend.
- **Telemetría del sistema**: uso de CPU, RAM, swap, disco raíz, carga media,
  tiempo de actividad y temperatura de la CPU. La temperatura se lee de forma
  nativa desde el sensor térmico de Linux, con respaldo a los sensores que
  exponga el sistema.
- **Gestión de contenedores**: listar, desplegar y destruir contenedores del
  host mediante el SDK oficial de Docker, con mapeo automático de puertos.
- **Auditoría de red**: descubrimiento de hosts activos con *ping sweep*
  (`nmap -sn`), incluyendo MAC y nombre de host cuando están disponibles, y un
  mecanismo de respaldo que consulta la tabla de vecinos ARP del núcleo.
- **Agente de IA ejecutable**: tres herramientas expuestas al modelo
  (escaneo de red, despliegue de contenedores y estado del sistema) con bucle
  de iteración acotado.
- **Multi-proveedor**: OpenAI, Anthropic, Google Gemini y Ollama (ejecución
  local, sin nube).
- **Configuración por entorno**: todo parametrizado mediante variables de
  entorno validadas con Pydantic, con plantilla `.env.example`.
- **Contenedor endurecido**: usuario no privilegiado, `capabilities` mínimas,
  `no-new-privileges` y rotación de logs para no degradar la microSD.
- **Healthcheck integrado** y script de verificación reutilizable.

## Arquitectura

```
┌───────────────────────────────────────────────────────────────┐
│                    Raspberry Pi 5 (Debian 64-bit)              │
│                                                               │
│   Navegador ──HTTP──►  Contenedor nexus-core (FastAPI)        │
│                         ├── /api/metrics      (psutil)        │
│                         ├── /api/containers   (Docker SDK)    │
│                         ├── /api/network-scan (nmap / ARP)    │
│                         └── /api/chat         (agente IA)     │
│                                  │                            │
│                    socket /var/run/docker.sock                │
│                                  ▼                            │
│                         Daemon Docker del host                │
└───────────────────────────────────────────────────────────────┘
```

Capas: **presentación** (frontend HTML/Tailwind) → **API** (FastAPI) →
**lógica de dominio** (módulos) → **abstracción del sistema** (Docker SDK, Nmap,
psutil) → **hardware** (Raspberry Pi).

## Requisitos

| Elemento | Versión recomendada |
|---|---|
| Hardware | Raspberry Pi 5 (4/8 GB) · microSD o NVMe |
| Sistema operativo | Raspberry Pi OS Lite 64-bit (Debian Bookworm) |
| Docker Engine | ≥ 27 |
| Docker Compose | ≥ 2.24 (plugin) |
| Python (solo desarrollo) | 3.12 |

## Instalación rápida

```bash
# 1. Instalar Docker y las dependencias del sistema (ARM64)
chmod +x scripts/install.sh
sudo ./scripts/install.sh

# 2. Configurar las variables de entorno
cp .env.example .env
nano .env      # añade OPENAI_API_KEY, o configura AI_PROVIDER=ollama

# 3. Levantar la plataforma
docker compose up -d --build

# 4. Comprobar el estado del servicio
./scripts/healthcheck.sh
```

Accede desde el navegador a `http://<IP-DEL-HOST>:8000`.
La documentación interactiva de la API (OpenAPI) está en
`http://<IP-DEL-HOST>:8000/docs`.

También se puede operar con `make` (`make up`, `make logs`, `make ps`,
`make down`, `make clean`); ejecuta `make help` para ver todos los objetivos.

## Configuración

Todas las variables disponibles están en `.env.example`. Las principales:

| Variable | Descripción | Por defecto |
|---|---|---|
| `APP_NAME` | Nombre de la aplicación | `Nexus-Core` |
| `APP_VERSION` | Versión reportada por la API | `1.0.0` |
| `HOST` | Interfaz de escucha | `0.0.0.0` |
| `PORT` | Puerto de la API | `8000` |
| `API_PREFIX` | Prefijo de las rutas | `/api` |
| `LOG_LEVEL` | Nivel de logging | `INFO` |
| `FRONTEND_DIR` | Directorio de estáticos | `frontend` |
| `ALLOWED_ORIGINS` | Orígenes permitidos por CORS (csv) | `*` |
| `DOCKER_SOCKET` | Socket del daemon Docker | `unix:///var/run/docker.sock` |
| `CONTAINER_NAME_PREFIX` | Prefijo de los contenedores gestionados | `nexus-` |
| `MAX_RUNNING_CONTAINERS` | Tope de contenedores gestionados en marcha | `15` |
| `ALLOWED_IMAGES` | Lista blanca de imágenes desplegables (csv) | `alpine,nginx,…` |
| `SCAN_DEFAULT_SUBNET` | Subred usada en los escaneos | `192.168.1.0/24` |
| `SCAN_TIMEOUT_SECONDS` | Tiempo máximo de escaneo | `120` |
| `AI_PROVIDER` | `openai` \| `anthropic` \| `gemini` \| `ollama` | `openai` |
| `AI_MODEL` | Modelo del LLM | `gpt-4o-mini` |
| `AI_TEMPERATURE` | Temperatura de muestreo | `0.2` |
| `AI_MAX_TOOL_ITERATIONS` | Iteraciones máximas del bucle de herramientas | `4` |
| `AI_REQUEST_TIMEOUT` | Timeout de las llamadas al LLM (s) | `60` |
| `OPENAI_API_KEY` | Clave de OpenAI (o API compatible) | *(vacío)* |
| `OPENAI_BASE_URL` | Endpoint OpenAI-compatible | `https://api.openai.com/v1` |
| `ANTHROPIC_API_KEY` | Clave de Anthropic | *(vacío)* |
| `GEMINI_API_KEY` | Clave de Google Gemini | *(vacío)* |
| `GEMINI_BASE_URL` | Endpoint de la API Generative Language | `https://generativelanguage.googleapis.com/v1beta` |
| `GEMINI_MODEL` | Modelo de Gemini | `gemini-2.5-flash` |
| `OLLAMA_BASE_URL` | Endpoint Ollama en el host | `http://host.docker.internal:11434/v1` |

> El fichero `.env` está excluido por `.gitignore`. Nunca subas claves reales al
> repositorio.

## API REST

Prefijo base: `/api`.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/health` | Estado del servicio |
| `GET` | `/api/metrics` | CPU, RAM, swap, disco, temperatura, carga y uptime |
| `GET` | `/api/containers` | Lista los contenedores del host |
| `POST` | `/api/containers` | Despliega un contenedor de una imagen autorizada |
| `DELETE` | `/api/containers/{id}` | Detiene y elimina un contenedor |
| `POST` | `/api/network-scan` | Descubre hosts activos en una subred privada |
| `POST` | `/api/chat` | Conversación con el agente de IA |

Documentación interactiva generada automáticamente: `/docs` (Swagger UI) y
`/redoc`.

## Agente de IA

El agente expone tres herramientas que el modelo puede invocar de forma
autónoma:

| Herramienta | Qué hace |
|---|---|
| `escanear_red_local(subnet)` | Descubre hosts activos en la subred indicada |
| `desplegar_servicio(imagen, puerto)` | Despliega un contenedor Docker validado |
| `obtener_estado_sistema()` | Devuelve la telemetría del sistema |

El bucle envía el mensaje junto al catálogo de herramientas; si el modelo pide
una herramienta, el backend la ejecuta y devuelve el resultado al modelo, que
itera hasta producir una respuesta final o alcanzar
`AI_MAX_TOOL_ITERATIONS`. El prompt de sistema instruye explícitamente al modelo
a no inventar datos y a responder siempre en español.

Proveedores soportados (se eligen con `AI_PROVIDER`):

| `AI_PROVIDER` | Servicio | Variable de clave |
|---|---|---|
| `openai` | OpenAI o cualquier API compatible | `OPENAI_API_KEY` |
| `anthropic` | Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| `gemini` | Google Gemini | `GEMINI_API_KEY` |
| `ollama` | Modelo local en el host, sin nube | — |

```bash
# Ejemplo: ejecución 100 % local con Ollama
AI_PROVIDER=ollama
AI_MODEL=llama3.2:3b
```

> **Nota técnica:** cada proveedor define un formato distinto de *function
> calling*. El backend traduce el catálogo común a cada formato: OpenAI usa
> `tools`, Anthropic `input_schema` y Gemini `functionDeclarations` con los tipos
> JSON Schema en mayúsculas (`STRING`, `INTEGER`, …).

## Seguridad

- **Socket Docker:** concede un nivel de privilegio equivalente a `root` sobre
  el host. Despliega la plataforma **solo en redes de confianza** (LAN
  doméstica o laboratorio) y nunca expuesta directamente a Internet.
- **Lista blanca de imágenes:** el gestor de Docker solo despliega imágenes
  presentes en `ALLOWED_IMAGES`, y aplica además un tope de contenedores
  gestionados en ejecución (`MAX_RUNNING_CONTAINERS`).
- **Escaneo restringido:** los escaneos solo se admiten sobre rangos privados
  RFC1918 o loopback, evitando que la plataforma funcione como escáner de redes
  de terceros.
- **Contenedor endurecido:** se eliminan todas las *capabilities* y solo se
  readmiten `NET_RAW` y `NET_ADMIN`, necesarias para Nmap, con
  `no-new-privileges` activo y usuario no privilegiado.
- **Rate limiting:** `/api/chat` está limitado por IP para frenar el abuso del
  endpoint que consume API del proveedor.
- **CORS configurable:** restringe `ALLOWED_ORIGINS` al dominio real en
  despliegues que no sean de laboratorio.

## Estructura del proyecto

```
Nexus-Core/
├── backend/
│   └── app/
│       ├── main.py               # Entrypoint FastAPI, CORS, estáticos, rate limit
│       ├── core/                 # Configuración tipada y logging estructurado
│       ├── api/                  # Endpoints REST de telemetría, Docker y red
│       ├── ai/                   # Agente, herramientas y ejecutor de tools
│       ├── modules/              # Docker, escaneo de red y telemetría
│       └── schemas/              # Modelos Pydantic de la API
├── backend/tests/                # Tests de la API
├── frontend/                     # Dashboard HTML/Tailwind + JS
├── scripts/                      # Instalación y healthcheck
├── docs/                         # Memoria técnica del proyecto
├── requirements.txt
├── requirements-dev.txt
├── Dockerfile
├── docker-compose.yml
└── Makefile
```

Desarrollo y pruebas locales (requiere las dependencias de
`backend/requirements-dev.txt`):

```bash
pip install -r requirements.txt -r backend/requirements-dev.txt

cd backend && python -m pytest -q    # suite de la API
ruff check backend                   # lint
mypy backend                         # tipado
```

El `Makefile` también expone `make test-local` y `make test` (este último ejecuta
`pytest` dentro del contenedor).

## Documentación

En `docs/` se encuentra la memoria técnica del proyecto: introducción y
objetivos, análisis de requisitos, diseño de la arquitectura, pruebas,
conclusiones, trabajo futuro y bibliografía.

## Licencia

Proyecto académico de uso educativo. El repositorio **no incluye un archivo
`LICENSE`**: los metadatos OCI de la imagen (`Dockerfile`) declaran `MIT`, pero
no se ha añadido el texto de la licencia al árbol de archivos. Aplica solo lo que
figure expresamente en esos metadatos hasta que se incorpore un `LICENSE`
explícito.
