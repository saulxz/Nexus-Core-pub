# Nexus-Core

## Plataforma Web de Orquestación de Infraestructura y Ciberseguridad con Agente de IA Integrado en Entorno Edge (Raspberry Pi)

---

### Trabajo Fin de Grado

| Campo | Valor |
|---|---|
| **Título** | Nexus-Core: Plataforma Web de Orquestación de Infraestructura y Ciberseguridad con Agente de IA Integrado en Entorno Edge (Raspberry Pi) |
| **Ciclo Formativo** | Grado Superior en Sistemas Microinformáticos y Redes (SMR) |
| **Autor/a** | `[Nombre del alumno/a]` |
| **Tutor/a** | `[Nombre del tutor/a]` |
| **Curso académico** | 2025 – 2026 |
| **Centro** | `[Nombre del centro educativo]` |
| **Fecha** | `[Fecha de entrega]` |

---

## Resumen

El presente Trabajo Fin de Grado describe el diseño, la implementación y la
validación de **Nexus-Core**, una plataforma web de orquestación de
infraestructura y ciberseguridad concebida para ejecutarse en un dispositivo de
borde (*edge*) como la **Raspberry Pi 5**. La plataforma unifica en un único
panel tres capacidades tradicionalmente dispersas: la administración de
contenedores **Docker**, la auditoría de la **red local** mediante Nmap y la
monitorización de la **telemetría del sistema** (CPU, memoria, disco y
temperatura).

El elemento diferencial del proyecto es la integración de un **Agente de
Inteligencia Artificial con capacidad de ejecución de acciones** (*Function
Calling*). A diferencia de un chatbot convencional, el agente interpreta
instrucciones en lenguaje natural y decide de forma autónoma qué herramientas
del sistema operativo invocar para cumplir el objetivo del usuario, devolviendo
resultados reales y no simulados. El sistema es compatible con proveedores en
la nube (**OpenAI**, **Anthropic**, **Google Gemini**) y con modelos locales
ejecutados mediante
**Ollama**, lo que permite un funcionamiento completamente offline, coherente
con la filosofía *edge*.

La solución se construye sobre **Python 3.12** y **FastAPI** en el backend, un
frontend web autocontenido (HTML/JavaScript + Tailwind CSS) y un despliegue
basado en **Docker** y **Docker Compose** optimizado para arquitectura
**ARM64**, garantizando portabilidad, aislamiento y reproducibilidad.

## Abstract

This Final Degree Project describes the design, implementation and validation
of **Nexus-Core**, a web platform for infrastructure orchestration and
cybersecurity intended to run on an edge device such as the **Raspberry Pi 5**.
The platform unifies three traditionally scattered capabilities into a single
dashboard: **Docker** container management, **local network** auditing with
Nmap, and **system telemetry** monitoring (CPU, memory, disk and temperature).

The distinguishing feature of the project is the integration of an **AI Agent
with Function Calling capabilities**. Unlike a conventional chatbot, the agent
interprets natural-language instructions and autonomously decides which
operating-system tools to invoke, returning real —not simulated— results. The
system supports cloud providers (**OpenAI**, **Anthropic**, **Google Gemini**)
as well as local
models served by **Ollama**, enabling fully offline operation, consistent with
the edge-computing philosophy.

The solution is built on **Python 3.12** and **FastAPI** on the backend, a
self-contained web frontend (HTML/JavaScript + Tailwind CSS), and a deployment
based on **Docker** and **Docker Compose** optimised for the **ARM64**
architecture, ensuring portability, isolation and reproducibility.

**Palabras clave:** Raspberry Pi, Docker, FastAPI, ciberseguridad, Nmap,
Inteligencia Artificial, Function Calling, edge computing, ARM64, Debian.

---

## Índice de contenidos

1. [Introducción y objetivos](#1-introducción-y-objetivos)
2. [Análisis de requisitos](#2-análisis-de-requisitos)
3. [Diseño de la arquitectura](#3-diseño-de-la-arquitectura)
4. [Conclusiones y trabajo futuro](#4-conclusiones-y-trabajo-futuro)
5. [Bibliografía](#5-bibliografía)
6. [Anexos](#6-anexos)

---

# 1. Introducción y objetivos

## 1.1 Contexto y justificación técnica

La administración de sistemas y redes ha evolucionado desde la gestión manual de
servidores físicos hacia modelos **automatizados, declarativos y observables**.
Tres tendencias definen el panorama actual de la profesión:

1. **Virtualización basada en contenedores.** Docker se ha consolidado como el
   estándar *de facto* para empaquetar y desplegar aplicaciones de forma
   aislada y reproducible. La gestión del ciclo de vida de los contenedores es
   hoy una competencia nuclear del administrador de sistemas.
2. **Edge computing.** El procesamiento cerca de donde se generan los datos
   reduce la latencia, el consumo de ancho de banda y la dependencia de la nube.
   Dispositivos como la Raspberry Pi permiten desplegar servicios con un coste y
   un consumo energético mínimos.
3. **Operación asistida por Inteligencia Artificial.** Los modelos de lenguaje
   (*LLM*) evolucionan desde la simple conversación hacia la **ejecución de
   acciones** mediante *Function Calling*, abriendo la puerta a interfaces
   operativas que traducen lenguaje natural en operaciones de sistema.

Estas tres tendencias confluyen en un problema real de la práctica profesional:
la gestión de un pequeño servidor o laboratorio doméstico exige manejar varias
herramientas heterogéneas (CLI de Docker, `nmap` o `htop` en terminal, scripts
dispersos) con una curva de aprendizaje elevada y sin una visión unificada.
**Nexus-Core** nace para cubrir esa necesidad, ofreciendo una interfaz única,
web y accesible desde cualquier dispositivo de la red, que además incorpora un
asistente inteligente capaz de operar el sistema.

## 1.2 Motivación y viabilidad en el ámbito de SMR

El proyecto es viable y plenamente pertinente en el ámbito de **SMR** porque
integra, en un único producto, competencias oficiales del ciclo:

- **Implantación de sistemas operativos:** instalación y configuración de
  Debian (Raspberry Pi OS Lite 64-bit).
- **Gestión de redes:** direccionamiento IP, subredes, descubrimiento de hosts
  y tabla ARP.
- **Administración de servicios y contenedores:** Docker, imágenes, volúmenes y
  redes bridge.
- **Seguridad y hardening:** gestión de *capabilities*, principio de mínimo
  privilegio, listas blancas y limitación de peticiones.
- **Programación de sistemas:** desarrollo de la lógica de control con Python y
  exposición de servicios web.

La elección de hardware de bajo coste (Raspberry Pi 5) garantiza la
reproducibilidad económica del trabajo, mientras que el uso exclusivo de
software libre elimina cualquier barrera de licencias.

## 1.3 Objetivos

### Objetivo general

Diseñar e implementar una plataforma web modular que centralice la
orquestación de contenedores Docker, la auditoría de red y la monitorización
del sistema sobre una Raspberry Pi, incorporando un agente de IA con ejecución
de acciones reales.

### Objetivos específicos

1. **OE-1.** Definir una arquitectura en capas robusta, modular y desacoplada.
2. **OE-2.** Implementar un módulo de gestión de contenedores Docker mediante el
   SDK oficial de Python, con listado, despliegue y destrucción seguros.
3. **OE-3.** Desarrollar un módulo de auditoría de red basado en Nmap que
   descubra hosts activos y devuelva datos estructurados en JSON.
4. **OE-4.** Implementar un módulo de telemetría que obtenga CPU, RAM, disco,
   carga media y **temperatura nativa** de la Raspberry Pi.
5. **OE-5.** Diseñar e integrar un **Agente de IA con Function Calling** capaz
   de invocar las funciones del sistema operativo.
6. **OE-6.** Construir un frontend tipo *dashboard* autocontenido, responsivo y
   con actualización periódica.
7. **OE-7.** Empaquetar la solución con Docker y Docker Compose sobre ARM64,
   aplicando medidas de *hardening*.
8. **OE-8.** Elaborar la documentación técnica y la memoria del proyecto.

## 1.4 Alcance y limitaciones

**Dentro del alcance:**

- Administración de contenedores del host a través del socket de Docker.
- Escaneo de descubrimiento de hosts (`nmap -sn`) sobre redes privadas.
- Telemetría del sistema en tiempo real mediante sondeo (*polling*).
- Agente de IA con tres herramientas operativas.
- Despliegue contenedorizado sobre ARM64.

**Fuera del alcance (o limitado):**

- La plataforma **no implementa autenticación de usuarios** en esta versión
  (ver *Trabajo futuro*); está pensada para redes de confianza.
- El escaneo es de **descubrimiento**, no de vulnerabilidades.
- La telemetría usa *polling*; los WebSockets se contemplan como evolución.
- El agente no ejecuta comandos arbitrarios: solo las herramientas autorizadas.

---

# 2. Análisis de requisitos

## 2.1 Requisitos de hardware

| Componente | Especificación | Justificación |
|---|---|---|
| Placa | Raspberry Pi 5 (4 u 8 GB RAM) | Mayor CPU y ancho de banda que generaciones previas |
| Almacenamiento | microSD Clase 10 (32 GB+) o SSD NVMe vía HAT | Sistema operativo, imágenes Docker y datos |
| Alimentación | Fuente oficial 5 V / 5 A (USB-C) | Estabilidad para cargas de trabajo Docker |
| Red | Ethernet Gigabit o Wi-Fi 5 | Acceso a la subred a auditar y a la API |
| Refrigeración | Disipador/ventilador activo | La temperatura afecta al rendimiento sostenido |
| Chasis | Carcasa con acceso a GPIO/ventilación | Protección y disipación |

> **Nota:** el software es igualmente desplegable en cualquier servidor x86-64
> con Debian, lo que facilita el desarrollo y las pruebas.

## 2.2 Requisitos de software

| Capa | Tecnología | Versión |
|---|---|---|
| Sistema operativo | Raspberry Pi OS Lite 64-bit (Debian Bookworm) | 12 |
| Motor de contenedores | Docker Engine | ≥ 27 |
| Orquestación | Docker Compose (plugin) | ≥ 2.24 |
| Lenguaje backend | Python | 3.12 |
| Framework API | FastAPI | 0.115.x |
| Servidor ASGI | Uvicorn | 0.32.x |
| Validación | Pydantic / pydantic-settings | 2.10.x / 2.7.x |
| Cliente Docker | docker (SDK Python) | 7.1.x |
| Auditoría de red | Nmap / python-nmap | 7.x / 0.7.1 |
| Telemetría | psutil | 6.1.x |
| Cliente HTTP/IA | httpx, openai, anthropic | 0.28.x / 1.59.x / 0.42.x |
| Frontend | HTML5, JavaScript (ES6), Tailwind CSS (CDN), Chart.js | 4.x |
| IA local (opcional) | Ollama | última estable |
| IA cloud (opcional) | OpenAI / Anthropic / Google Gemini | última estable |

## 2.3 Requisitos funcionales

| ID | Requisito | Prioridad |
|---|---|---|
| RF-01 | El sistema mostrará el uso de CPU, RAM, swap, disco y temperatura (°C) de la Raspberry Pi. | Alta |
| RF-02 | El sistema actualizará periódicamente la telemetría sin recargar la página. | Alta |
| RF-03 | El sistema listará los contenedores del host con ID, nombre, imagen, estado y puertos. | Alta |
| RF-04 | El usuario podrá desplegar un contenedor a partir de una imagen autorizada, con puerto opcional. | Alta |
| RF-05 | El usuario podrá destruir un contenedor indicando su ID o nombre. | Alta |
| RF-06 | El sistema escaneará una subred privada y listará los hosts activos (IP, estado, MAC, hostname). | Alta |
| RF-07 | El sistema expondrá un chat donde el Agente de IA responderá en lenguaje natural. | Alta |
| RF-08 | El Agente de IA podrá invocar herramientas del sistema (escaneo, despliegue, telemetría). | Alta |
| RF-09 | El sistema mostrará, junto a la respuesta de la IA, las acciones ejecutadas. | Media |
| RF-10 | El sistema validará que las imágenes a desplegar pertenezcan a una lista blanca. | Alta |
| RF-11 | El sistema rechazará escaneos sobre rangos de red públicos. | Alta |
| RF-12 | El sistema limitará la frecuencia de peticiones al endpoint del agente. | Media |

## 2.4 Requisitos no funcionales

| ID | Requisito | Criterio de aceptación |
|---|---|---|
| RNF-01 | **Seguridad** | Mínimo privilegio: sin *root*, *capabilities* mínimas, lista blanca de imágenes, `no-new-privileges`. |
| RNF-02 | **Rendimiento** | Telemetría < 1 s; API REST < 500 ms en operaciones ligeras sobre LAN. |
| RNF-03 | **Usabilidad** | Dashboard responsivo, accesible desde móvil y escritorio, en español. |
| RNF-04 | **Mantenibilidad** | Código modular por capas, tipado, documentado con docstrings. |
| RNF-05 | **Portabilidad** | Imagen Docker multi-arquitectura ARM64/x86-64 basada en Debian. |
| RNF-06 | **Disponibilidad** | `restart: unless-stopped` y healthcheck del contenedor. |
| RNF-07 | **Trazabilidad** | Logging estructurado con rotación (Loguru). |
| RNF-08 | **Extensibilidad** | Nuevas herramientas de IA añadibles sin tocar el *core*. |

## 2.5 Casos de uso

| CU | Actor | Descripción | RF asociados |
|---|---|---|---|
| CU-01 | Administrador | Consultar la telemetría del sistema en el dashboard. | RF-01, RF-02 |
| CU-02 | Administrador | Desplegar un contenedor nginx en el puerto 8080. | RF-04, RF-10 |
| CU-03 | Administrador | Eliminar un contenedor por su ID. | RF-05 |
| CU-04 | Analista de seguridad | Auditar la subred 192.168.1.0/24. | RF-06, RF-11 |
| CU-05 | Usuario | Pedir a la IA: «¿cómo está el rendimiento de la Raspberry?». | RF-07, RF-08, RF-09 |
| CU-06 | Usuario | Pedir a la IA: «escanea la red y dime cuántos equipos hay». | RF-07, RF-08 |
| CU-07 | Usuario | Pedir a la IA: «levanta un servidor web en el 8080». | RF-04, RF-07, RF-08, RF-10 |

## 2.6 Análisis de riesgos y medidas de seguridad

| Riesgo | Impacto | Probabilidad | Mitigación implementada |
|---|---|---|---|
| Acceso no autorizado a la API | Alto | Media | Uso en red de confianza; *rate limiting*; evolución a JWT |
| Fuga por el socket de Docker | Crítico | Media | Documentado; mínimo privilegio; sin exposición a Internet |
| Despliegue de imágenes maliciosas | Alto | Media | Lista blanca `ALLOWED_IMAGES` |
| Escaneo de redes de terceros | Medio | Baja | Validación RFC1918 obligatoria |
| Agotamiento de recursos | Medio | Media | Límite `MAX_RUNNING_CONTAINERS` |
| Sobrecalentamiento de la Pi | Medio | Media | Monitorización térmica y alertas en el panel |
| Escalada de privilegios en el contenedor | Alto | Baja | `cap_drop: ALL`, `no-new-privileges` |
| Robo de claves de API | Alto | Baja | Variables de entorno, `.env` ignorado por Git |

---

# 3. Diseño de la arquitectura

## 3.1 Arquitectura lógica por capas

Nexus-Core sigue una arquitectura **en capas** con dependencias unidireccionales
hacia abajo, lo que favorece el desacoplamiento y la testeabilidad:

```
┌──────────────────────────────────────────────────────────────┐
│  CAPA 1 · PRESENTACIÓN                                        │
│  Dashboard HTML + Tailwind + Chart.js (JS vanilla)            │
│  → fetch() a la API REST                                      │
├──────────────────────────────────────────────────────────────┤
│  CAPA 2 · API / CONTROLADORES                                 │
│  FastAPI: /api/health, /metrics, /containers, /network-scan,  │
│  /chat · CORS · Rate limiting · Validación (Pydantic)         │
├──────────────────────────────────────────────────────────────┤
│  CAPA 3 · LÓGICA DE DOMINIO                                   │
│  docker_manager · network_scanner · system_monitor            │
│  Agente IA: tools_schema · tool_executor · agent (function    │
│  calling)                                                     │
├──────────────────────────────────────────────────────────────┤
│  CAPA 4 · ABSTRACCIÓN DEL SISTEMA                             │
│  Docker SDK · python-nmap · psutil · httpx (LLM)              │
├──────────────────────────────────────────────────────────────┤
│  CAPA 5 · HARDWARE / S.O.                                     │
│  Daemon Docker · interfaz de red · /sys/class/thermal ·       │
│  Raspberry Pi 5 (ARM64)                                       │
└──────────────────────────────────────────────────────────────┘
```

**Principios aplicados:** separación de responsabilidades, inversión de
dependencias (los módulos de dominio no conocen FastAPI), configuración
centralizada y manejo de errores que nunca propaga excepciones al usuario.

## 3.2 Arquitectura física (despliegue)

```
┌───────────────────────────────────────────────────────────────────┐
│                     Raspberry Pi 5 · Debian Bookworm               │
│                                                                    │
│   LAN 192.168.1.0/24                                                │
│        │                                                            │
│        │ HTTP :8000                                                 │
│        ▼                                                            │
│   ┌───────────────────────────┐        ┌────────────────────────┐  │
│   │  Red bridge: nexus_net    │        │  Servicio del host      │  │
│   │  ┌─────────────────────┐  │        │  Ollama :11434          │  │
│   │  │ Contenedor          │  │◄──────►│  (IA local, opcional)   │  │
│   │  │ nexus-core          │  │ host.  └────────────────────────┘  │
│   │  │ Uvicorn + FastAPI   │  │ docker.                            │
│   │  │ usuario `nexus`     │  │ internal                           │
│   │  └─────────┬───────────┘  │                                    │
│   └────────────┼──────────────┘                                    │
│                │ socket unix                                       │
│                ▼                                                    │
│   ┌───────────────────────────┐   ┌────────────────────────────┐   │
│   │ Daemon Docker (/var/run/  │   │ Volumen nexus_data         │   │
│   │ docker.sock)              │   │ (/app/data: logs)          │   │
│   └─────────┬─────────────────┘   └────────────────────────────┘   │
│             │ administra                                            │
│             ▼                                                       │
│   ┌───────────────────────────────────────────────────────────┐    │
│   │ Contenedores gestionados (nginx, alpine, redis, …)        │    │
│   └───────────────────────────────────────────────────────────┘    │
└───────────────────────────────────────────────────────────────────┘
```

El contenedor `nexus-core` se une al grupo `docker` del host (GID 999) para
poder operar el socket sin ejecutarse como *root*. Se le añaden únicamente las
*capabilities* `NET_RAW` y `NET_ADMIN`, necesarias para Nmap, habiendo eliminado
el resto (`cap_drop: ALL`).

## 3.3 Modelo de datos y contratos de API

### Endpoints REST

| Método | Ruta | Cuerpo (JSON) | Respuesta |
|---|---|---|---|
| GET | `/api/health` | — | `{status, app, version}` |
| GET | `/api/metrics` | — | Objeto de telemetría |
| GET | `/api/containers` | — | `{containers: [...]}` |
| POST | `/api/containers` | `{image, port?, name?}` | `{success, message, container}` |
| DELETE | `/api/containers/{id}` | — | `{success, message}` |
| POST | `/api/network-scan` | `{subnet?}` | `{subnet, hosts[], count, scanned_at}` |
| POST | `/api/chat` | `{message, history?}` | `{reply, actions[], model, provider}` |

### Ejemplo de respuesta de `/api/metrics`

```json
{
  "cpu_percent": 12.4,
  "cpu_count": 4,
  "ram_percent": 38.1,
  "ram_used_mb": 1523.7,
  "ram_total_mb": 4096.0,
  "swap_percent": 0.0,
  "disk_percent": 41.2,
  "cpu_temp_c": 48.3,
  "uptime_seconds": 86412.0,
  "load_avg": [0.21, 0.18, 0.15],
  "timestamp": "2026-01-15T10:32:05.123456+00:00"
}
```

### Ejemplo de respuesta de `/api/chat`

```json
{
  "reply": "He escaneado la red 192.168.1.0/24 y hay 7 dispositivos activos.",
  "actions": [
    {
      "tool": "escanear_red_local",
      "arguments": {"subnet": "192.168.1.0/24"},
      "result": {"subnet": "192.168.1.0/24", "count": 7, "hosts": ["..."]}
    }
  ],
  "model": "gpt-4o-mini",
  "provider": "openai"
}
```

### Esquema de las herramientas de IA

| Herramienta | Parámetros | Acción en el backend |
|---|---|---|
| `escanear_red_local` | `subnet` (opcional) | `network_scanner.scan_network()` |
| `desplegar_servicio` | `imagen`, `puerto` (opcional) | `docker_manager.deploy_container()` |
| `obtener_estado_sistema` | — | `system_monitor.get_system_metrics()` |

### Adaptación del esquema a cada proveedor

El catálogo de herramientas se define **una sola vez** en `tools_schema.py` con
el formato de *Function Calling* de OpenAI. Como cada proveedor usa un
esquema distinto, el módulo `agent.py` incorpora funciones de traducción que
lo adaptan:

| Proveedor | Endpoint | Formato de herramientas | Particularidades |
|---|---|---|---|
| OpenAI / Ollama | `/chat/completions` | `tools: [{type:"function", function:{…}}]` | Mensajes: assistant con `tool_calls` + `role:"tool"` |
| Anthropic | `/v1/messages` | `tools: [{name, input_schema}]` | Respuestas en bloques `tool_use` / `tool_result` |
| Google Gemini | `/models/{model}:generateContent` | `tools: [{functionDeclarations:[…]}]` | Tipos en **mayúsculas** (`STRING`, `INTEGER`); `additionalProperties` no está soportado; roles `user` / `model` |

La función `_to_gemini_schema()` recorre el esquema y traduce los tipos
(`"string"` → `"STRING"`), descartando los campos que Gemini no soporta.
De este modo, añadir un proveedor nuevo no obliga a redefinir las herramientas,
sino únicamente su serialización.

## 3.4 Flujo de llamadas API y del Agente de IA

### Flujo REST convencional

```
Usuario → Navegador → fetch(/api/metrics) → FastAPI → system_monitor
        → psutil + /sys/class/thermal → JSON → Dashboard (render)
```

### Flujo del Agente de IA con Function Calling

```
Usuario            Frontend            FastAPI(/api/chat)          LLM            Módulos S.O.
  │                   │                       │                    │                 │
  │ "escanea la red"  │                       │                    │                 │
  ├──────────────────►│  POST /api/chat       │                    │                 │
  │                   ├──────────────────────►│  prompt + TOOLS    │                 │
  │                   │                       ├───────────────────►│                 │
  │                   │                       │                    │ tool_call:      │
  │                   │                       │◄───────────────────┤ escanear_red_   │
  │                   │                       │  execute_tool()    │ local           │
  │                   │                       ├────────────────────┼────────────────►│
  │                   │                       │                    │   nmap -sn      │
  │                   │                       │◄───────────────────┼─────────────────┤
  │                   │                       │  resultado real    │                 │
  │                   │                       ├───────────────────►│                 │
  │                   │                       │                    │ síntesis final  │
  │                   │◄──────────────────────┤◄───────────────────┤                 │
  │  reply + actions  │                       │                    │                 │
  │◄──────────────────┤  render burbujas/chips│                    │                 │
```

El bucle se repite hasta que el modelo emite una respuesta sin invocar
herramientas o se alcanza `AI_MAX_TOOL_ITERATIONS` (4 por defecto), momento en
el que se fuerza una síntesis final sin herramientas para evitar bucles
infinitos.

## 3.5 Telemetría en tiempo real: estado actual y evolución

En esta versión, la actualización del dashboard se realiza mediante **sondeo
controlado** (*polling*) cada 5 segundos. Se pausa automáticamente cuando la
pestaña del navegador no está visible (`document.visibilityState`) para no
malgastar CPU en la Raspberry Pi.

Como evolución (ver capítulo 4), se contempla la migración a **WebSockets**
(la dependencia `websockets` ya está incluida en `requirements.txt`), lo que
permitiría telemetría *push* en tiempo real sin peticiones repetidas.

## 3.6 Decisiones de diseño y tecnologías

| Decisión | Alternativa descartada | Justificación |
|---|---|---|
| FastAPI | Flask / Django | Asíncrono nativo, validación Pydantic, OpenAPI automático |
| SDK Docker Python | Invocar CLI `docker` | Tipado, manejo de errores estructurado, sin *parsing* de texto |
| `python-nmap` + fallback ARP | Solo `ping` | Datos más ricos (MAC/hostname) y robustez |
| Lectura de `/sys/class/thermal` | Librería externa | Nativo, sin dependencias, específico de la Pi |
| httpx para el LLM | SDK pesado | Homogeneiza OpenAI/Ollama/Anthropic/Gemini y controla timeouts |
| Tailwind por CDN | Build con npm | Cero *toolchain*; el frontend se sirve directamente |
| Docker Compose | systemd | Portabilidad y gestión declarativa del servicio |
| Volumen nombrado | Bind mount | Persistencia gestionada y portable |

---

# 4. Conclusiones y trabajo futuro

## 4.1 Logros técnicos obtenidos

1. **Arquitectura modular y profesional** en cinco capas desacopladas, con
   separación estricta entre API, dominio y acceso al sistema.
2. **Orquestación de contenedores funcional**: listado, despliegue con mapeo
   dinámico de puertos y destrucción, integrados con el socket del host.
3. **Auditoría de red operativa** mediante Nmap, con validación de rangos
   privados y mecanismo de respaldo basado en la tabla ARP.
4. **Telemetría completa** del dispositivo, incluida la lectura directa del
   sensor térmico de la Raspberry Pi.
5. **Agente de IA con Function Calling** que ejecuta acciones reales sobre el
   sistema, soportando proveedores en la nube y modelos locales (edge).
6. **Frontend tipo dashboard** responsivo, autocontenido y en español, con
   gráficas en tiempo real y chat interactivo.
7. **Despliegue contenedorizado endurecido**, optimizado para ARM64, con
   healthcheck, rotación de logs y gestión de privilegios.
8. **Documentación técnica completa** y memoria académica del proyecto.

## 4.2 Competencias de SMR adquiridas

- Implantación y configuración de sistemas operativos Linux (Debian).
- Gestión de redes: subredes, CIDR, descubrimiento de hosts y ARP.
- Administración de contenedores y orquestación con Docker/Compose.
- Aplicación de principios de seguridad y *hardening* en producción.
- Desarrollo de software de sistemas con Python y exposición de servicios web.
- Integración de Inteligencia Artificial como herramienta operativa.
- Documentación técnica y defensa de proyectos.

## 4.3 Limitaciones encontradas

- **Sin autenticación:** la versión actual asume una red de confianza.
- **Escaneo de descubrimiento:** no analiza vulnerabilidades ni puertos abiertos.
- **Polling vs. push:** la telemetría no es instantánea.
- **Recursos limitados de la Pi:** el despliegue de imágenes pesadas o modelos
  LLM grandes requiere paciencia o modelos cuantizados.
- **Dependencia del socket Docker:** otorga privilegios elevados; requiere
  cuidado en el diseño de la red.

## 4.4 Líneas de trabajo futuro

1. **Autenticación y autorización:** JWT + RBAC (roles administrador/auditor).
2. **Migración a WebSockets:** telemetría *push* y notificaciones en vivo.
3. **Ampliación de herramientas de IA:** gestión de volúmenes/redes, reinicio
   de servicios, análisis de puertos (`nmap -sV`), lectura de logs.
4. **Sistema de alertas:** umbrales de temperatura/CPU con notificaciones
   (correo, Telegram, MQTT).
5. **Histórico y persistencia:** base de datos (SQLite/PostgreSQL) con series
   temporales y panel de tendencias.
6. **Multi-host:** gestión centralizada de varias Raspberry Pi mediante un
   agente ligero remoto.
7. **Integración con Prometheus/Grafana:** exportación de métricas estándar.
8. **Endurecimiento adicional:** firma de imágenes, auditoría de acciones de la
   IA y confirmación humana para operaciones destructivas.
9. **Internacionalización (i18n):** español/inglés en la interfaz.
10. **CI/CD:** pipeline de GitHub Actions con tests y publicación de imagen
    multi-arquitectura.

---

# 5. Bibliografía

1. FastAPI. *FastAPI Documentation*. [En línea]. Disponible en:
   https://fastapi.tiangolo.com/
2. Pydantic. *Pydantic V2 Documentation*. [En línea]. Disponible en:
   https://docs.pydantic.dev/
3. Docker Inc. *Docker Engine Documentation*. [En línea]. Disponible en:
   https://docs.docker.com/engine/
4. Docker Inc. *Docker Compose Documentation*. [En línea]. Disponible en:
   https://docs.docker.com/compose/
5. Docker Inc. *Docker SDK for Python*. [En línea]. Disponible en:
   https://docker-py.readthedocs.io/
6. Gordon Lyon (Fyodor). *Nmap Network Scanning*. [En línea]. Disponible en:
   https://nmap.org/book/
7. python-nmap. *python-nmap Documentation*. [En línea]. Disponible en:
   https://xael.org/pages/python-nmap-en.html
8. psutil. *psutil Documentation*. [En línea]. Disponible en:
   https://psutil.readthedocs.io/
9. OpenAI. *Function Calling Guide*. [En línea]. Disponible en:
   https://platform.openai.com/docs/guides/function-calling
10. Anthropic. *Tool Use (Function Calling)*. [En línea]. Disponible en:
    https://docs.anthropic.com/
11. Google. *Gemini API — Function Calling*. [En línea]. Disponible en:
    https://ai.google.dev/gemini-api/docs/function-calling
12. Ollama. *Ollama Documentation*. [En línea]. Disponible en:
    https://ollama.com/
13. Raspberry Pi Foundation. *Raspberry Pi 5 Documentation*. [En línea].
    Disponible en: https://www.raspberrypi.com/documentation/
14. Debian Project. *Debian Bookworm Administration Manual*. [En línea].
    Disponible en: https://www.debian.org/doc/
15. OWASP. *Docker Security Cheat Sheet*. [En línea]. Disponible en:
    https://cheatsheetseries.owasp.org/
16. RFC 1918. *Address Allocation for Private Internets*. IETF, 1996.

---

# 6. Anexos

## Anexo A — Comandos de despliegue

```bash
# Instalación de Docker y dependencias del sistema (una sola vez)
chmod +x scripts/install.sh
sudo ./scripts/install.sh

# Configuración
cp .env.example .env
nano .env

# Puesta en marcha
docker compose up -d --build

# Verificación
./scripts/healthcheck.sh
docker compose logs -f nexus-core

# Parada
docker compose down

# Limpieza total (elimina volumen de datos e imagen)
docker compose down -v --rmi local
```

## Anexo B — Variables de entorno principales

| Variable | Descripción | Por defecto |
|---|---|---|
| `APP_NAME` | Nombre de la aplicación | `Nexus-Core` |
| `PORT` | Puerto de la API | `8000` |
| `API_PREFIX` | Prefijo de las rutas REST | `/api` |
| `LOG_LEVEL` | Nivel de logging | `INFO` |
| `ALLOWED_ORIGINS` | Orígenes CORS (csv) | `*` |
| `DOCKER_SOCKET` | Socket del daemon Docker | `unix:///var/run/docker.sock` |
| `CONTAINER_NAME_PREFIX` | Prefijo de nombres gestionados | `nexus-` |
| `MAX_RUNNING_CONTAINERS` | Límite de contenedores gestionados | `15` |
| `ALLOWED_IMAGES` | Imágenes desplegables (csv) | `alpine,nginx,...` |
| `SCAN_DEFAULT_SUBNET` | Subred de escaneo por defecto | `192.168.1.0/24` |
| `SCAN_TIMEOUT_SECONDS` | Timeout del escaneo | `120` |
| `AI_PROVIDER` | Proveedor LLM | `openai` |
| `AI_MODEL` | Modelo LLM | `gpt-4o-mini` |
| `OPENAI_API_KEY` | Clave de OpenAI | *(vacío)* |
| `OPENAI_BASE_URL` | Endpoint compatible OpenAI | `https://api.openai.com/v1` |
| `ANTHROPIC_API_KEY` | Clave de Anthropic | *(vacío)* |
| `GEMINI_API_KEY` | Clave de Google Gemini | *(vacío)* |
| `GEMINI_BASE_URL` | API Generative Language | `https://generativelanguage.googleapis.com/v1beta` |
| `GEMINI_MODEL` | Modelo de Gemini | `gemini-2.5-flash` |
| `OLLAMA_BASE_URL` | Endpoint de Ollama | `http://host.docker.internal:11434/v1` |

## Anexo C — Glosario de términos

| Término | Definición |
|---|---|
| **ARM64 / AArch64** | Arquitectura de 64 bits usada por la Raspberry Pi 5. |
| **ARP** | Protocolo de resolución de direcciones; relaciona IP con MAC. |
| **Capability** | Permiso granular de Linux (p. ej. `NET_RAW`) más fino que ser root. |
| **CIDR** | Notación de direccionamiento (p. ej. `192.168.1.0/24`). |
| **Contenedor** | Unidad de software empaquetada y aislada que comparte el kernel. |
| **Edge computing** | Procesamiento de datos cerca de su origen, en el borde de la red. |
| **FastAPI** | Framework web asíncrono de Python basado en Starlette y Pydantic. |
| **Function Calling** | Capacidad de un LLM de invocar funciones/herramientas externas. |
| **Hardening** | Conjunto de medidas para reducir la superficie de ataque. |
| **Healthcheck** | Comprobación periódica del estado de un servicio. |
| **LLM** | Modelo de lenguaje de gran tamaño. |
| **Nmap** | Escáner de red y auditoría de seguridad. |
| **Ollama** | Ejecutor local de modelos de lenguaje. |
| **Polling** | Consulta periódica al servidor para obtener datos. |
| **RFC1918** | Rangos de direcciones IP privadas. |
| **Socket** | Interfaz de comunicación entre procesos; el de Docker está en `/var/run/docker.sock`. |
| **StaticFiles** | Mecanismo de FastAPI/Starlette para servir ficheros estáticos. |
| **Uvicorn** | Servidor ASGI de alto rendimiento. |
| **WebSocket** | Canal de comunicación bidireccional en tiempo real. |
| **Whitelist** | Lista blanca de elementos permitidos. |

---

*Fin de la memoria.*
