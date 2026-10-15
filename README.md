# Nexus-Core

> **Plataforma Web de Administración de Infraestructura y Ciberseguridad con Agente de IA.**
> Trabajo Fin de Grado — Ciclo Formativo de Grado Superior en Sistemas Microinformáticos y Redes (SMR).

Nexus-Core convierte un servidor Linux (máquina virtual o Raspberry Pi) en un
**panel de control centralizado** desde el que administrar los **servicios del
sistema** (Samba, DHCP, DNS…), **contenedores Docker**, la **red** (interfaces,
cambio de IP seguro) y la **telemetría** — con un **agente de IA** que propone
acciones en lenguaje natural y un administrador que las confirma.

---

## Tabla de contenidos

- [Características](#características)
- [Requisitos](#requisitos)
- [Instalación en VM (recomendada)](#instalación-en-vm-recomendada)
- [Primer arranque](#primer-arranque)
- [Actualización](#actualización)
- [Instalación con Docker](#instalación-con-docker-alternativa)
- [Panel web](#panel-web)
- [Agente de IA](#agente-de-ia)
- [API REST](#api-rest)
- [Seguridad](#seguridad)
- [Estructura del proyecto](#estructura-del-proyecto)

---

## Características

- **Autenticación integrada** (argon2id, sesiones con CSRF, límite de intentos)
  y **auditoría encadenada por hash** de cada acción.
- **Ajustes de IA desde la web**: proveedor, modelo y claves se guardan en el
  servidor y se aplican en caliente. El asistente es **opcional**.
- **Inventario y control de servicios** (systemd): buscador, filtros, estado,
  logs del journal y acciones *start/stop/restart/reload* con lista blanca y
  helper privilegiado auditado.
- **Configuración guiada** de Samba y dnsmasq: validación previa con la
  herramienta oficial del servicio, copia de seguridad, reemplazo atómico,
  recarga y **rollback automático** si algo falla. Copias restaurables.
- **DHCP con sonda previa**: detecta otros servidores DHCP en la red antes de
  activar el propio y avisa de concesiones fuera de rango.
- **Red**: tabla de interfaces (IP/CIDR, máscara, MAC, MTU, puerta de enlace) y
  **cambio de IP de la máquina con watchdog**: si no se confirma en 90 segundos
  desde la nueva dirección, la red se revierte automáticamente.
- **Contenedores Docker**: listar, desplegar y eliminar con lista blanca de
  imágenes y límite de contenedores.
- **Telemetría**: CPU, RAM, swap, disco raíz, temperatura, carga y uptime.
- **Escaneo de red** (nmap ping sweep) restringido a rangos privados RFC1918.
- **Agente de IA multi-proveedor** (OpenAI, Anthropic, Gemini, Ollama) con
  patrón *propose-only*: nunca ejecuta acciones mutantes sin confirmación humana.

---

## Requisitos

| Elemento | Recomendado |
|---|---|
| Sistema | Debian/Ubuntu con systemd (probado en Ubuntu 26.04, x86_64 y ARM64) |
| Python | 3.12 – 3.14 |
| Privilegios | `sudo` (la instalación crea un usuario de sistema y helpers root) |
| Red | LAN de confianza; puerto 8000 accesible desde el navegador |

Las funciones de gestión de servicios y red requieren que esos servicios existan
en la máquina (por ejemplo `sudo apt install samba dnsmasq`). Nexus los
**gestiona**, no los instala.

---

## Instalación en VM (recomendada)

```bash
git clone https://github.com/<usuario>/Nexus-Core.git
cd Nexus-Core
sudo bash deploy/install.sh
```

El instalador, paso a paso:

1. Crea el usuario de sistema `nexus` y los directorios de datos
   (`/var/lib/nexus`, `/var/backups/nexus`).
2. Copia el proyecto a `/opt/nexus-core` y crea el entorno virtual con las
   dependencias fijadas.
3. Instala los **seis helpers privilegiados** (`svc-ctl`, `conf-apply`,
   `conf-restore`, `net-apply`, `net-confirm`, `net-revert`), la lista blanca de
   unidades y las reglas sudoers (validadas con `visudo` **antes** de
   instalarse).
4. Registra el servicio systemd `nexus.service` y lo deja corriendo.

**Sin Internet:** coloca las ruedas `.whl` necesarias en `wheelhouse/` junto al
proyecto antes de instalar; el instalador las usará automáticamente (también
sabe crear el entorno con `virtualenv` embebido si el sistema no trae
`python3-venv`).

---

## Primer arranque

1. Abre en el navegador `http://<IP-DE-LA-VM>:8000/login.html`.
2. Lee el **token de configuración** que generó el servicio:

   ```bash
   sudo cat /var/lib/nexus/setup_token
   # o bien:
   sudo journalctl -u nexus | grep -i token
   ```

3. Pega el token en el asistente, crea tu usuario administrador y entra.
   A partir de ahí **todo se hace desde la web**: servicios, configuración,
   red y la API del asistente (Ajustes → Guardar → Probar conexión).

---

## Actualización

```bash
cd ~/Nexus-Core
git pull
sudo bash deploy/update.sh   # copia el código, arregla el acceso a Docker y reinicia
```

---

## Instalación con Docker (alternativa)

Para desarrollo o despliegues donde solo se necesiten telemetría, contenedores
y escaneo:

```bash
cp .env.example .env     # opcional: configura proveedor de IA y claves
docker compose up -d --build
```

> **Nota:** dentro del contenedor no hay acceso al systemd del host, así que las
> funciones de servicios/red/helpers requieren la instalación nativa. En Docker
> el panel queda accesible igualmente en `http://<IP>:8000` (login incluido).

---

## Panel web

| Pestaña | Qué ofrece |
|---|---|
| **Dashboard** | Telemetría en tiempo real con gráfica CPU/RAM |
| **Contenedores** | Listar, desplegar y eliminar contenedores del host |
| **Red** | Interfaces de la máquina (IP/máscara/MAC/MTU), escaneo de hosts y cambio de IP con reversión automática |
| **Servicios** | Inventario systemd con buscador y filtros, logs, control (start/stop/restart/reload) y configuración guiada de Samba y dnsmasq con copias de seguridad |
| **Ajustes** | Configuración del agente de IA: proveedor, modelo, claves y prueba de conexión en caliente |
| **Asistente IA** | Chat en lenguaje natural: consulta datos reales y **propone** acciones que tú confirmas |

---

## Agente de IA

Herramientas expuestas al modelo:

| Herramienta | Tipo | Qué hace |
|---|---|---|
| `escanear_red_local(subnet)` | lectura | Descubre hosts activos en la subred |
| `obtener_estado_sistema()` | lectura | Telemetría del sistema |
| `desplegar_servicio(imagen, puerto)` | mutante | Despliega un contenedor validado |
| `listar_servicios()` | lectura | Inventario systemd |
| `estado_servicio(unidad)` | lectura | Detalle de una unidad |
| `proponer_accion_servicio(unidad, accion)` | propuesta | Crea una propuesta que el humano confirma (caduca en 120 s) |
| `leer_config_servicio(servicio)` | lectura | Configuración gestionada de Samba o dnsmasq |

Las herramientas `proponer_*` **no ejecutan nada**: el panel muestra una tarjeta
de confirmación y la acción solo corre cuando la persona autenticada la acepta.
El autorizador real es el código (defensa frente a *prompt injection*).

Proveedores (se eligen en la web, o por entorno con `AI_PROVIDER`):

| `AI_PROVIDER` | Servicio | Clave |
|---|---|---|
| `openai` | OpenAI o cualquier API compatible | `OPENAI_API_KEY` |
| `anthropic` | Anthropic (Claude) | `ANTHROPIC_API_KEY` |
| `gemini` | Google Gemini | `GEMINI_API_KEY` |
| `ollama` | Modelo local, sin nube | — |
| *(vacío)* | Asistente desactivado (opcional) | — |

---

## API REST

Prefijo base: `/api`. Documentación interactiva en `/docs`.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/health` | Estado del servicio (público) |
| `POST` | `/api/auth/login` `/logout` | Sesión del panel |
| `GET` `PUT` | `/api/settings/ai` | Configuración de IA (enmascarada) |
| `POST` | `/api/settings/ai/test` | Prueba de conexión con el proveedor |
| `GET` | `/api/metrics` | Telemetría del sistema |
| `GET` `POST` `DELETE` | `/api/containers` | Gestión de contenedores Docker |
| `POST` | `/api/network-scan` | Escaneo de hosts (solo rangos privados) |
| `GET` | `/api/services` | Inventario systemd |
| `GET` | `/api/services/{name}` `/logs` | Detalle y logs de una unidad |
| `POST` | `/api/services/{name}/action` | Control con lista blanca |
| `GET` `PUT` | `/api/config/samba` `/api/config/dnsmasq` | Configuración guiada |
| `POST` | `/api/config/{s}/validate` `/restore` | Validación previa y restauración |
| `GET` | `/api/config/{s}/backups` | Copias de seguridad |
| `POST` | `/api/config/dnsmasq/dhcp-check` | Sonda de servidores DHCP |
| `GET` | `/api/network/state` | Interfaces, IPs, máscaras, MAC, rutas |
| `POST` | `/api/network/plan` `/apply` `/confirm` `/revert` | Cambio de IP con watchdog |
| `POST` | `/api/chat` | Agente de IA (propone acciones) |
| `GET` `POST` | `/api/chat/actions/...` | Propuestas de la IA: listar, confirmar, cancelar |

---

## Seguridad

- **Autenticación obligatoria** para todo el panel (argon2id, sesiones con
  caducidad y CSRF); `/api/health` es público para el healthcheck.
- **Auditoría** en JSONL encadenada por hash: cualquier manipulación del
  registro es detectable. Las claves nunca se escriben en la auditoría.
- **Helpers privilegiados**: la aplicación corre como usuario sin privilegios y
  solo puede ejecutar seis scripts root con lista blanca (sudoers exacto, sin
  comodines). Cada helper valida sus propios argumentos.
- **Configuración con rollback**: validación con la herramienta oficial del
  servicio antes y después de aplicar; copia previa y restauración automática.
- **Cambio de IP con watchdog**: reversión automática si no se confirma.
- **IA *propose-only*** con confirmación humana y defensa anti *prompt injection*.
- **Socket Docker**: equivale a root sobre el host. Despliega Nexus-Core solo en
  redes de confianza y **nunca lo expongas directamente a Internet** sin TLS y
  autenticación adicional (p. ej. tras un proxy inverso).
- **Escaneos** restringidos a rangos privados RFC1918.

---

## Estructura del proyecto

```
Nexus-Core/
├── backend/
│   ├── app/
│   │   ├── main.py            # Entrypoint FastAPI, routers, middleware, ciclo de vida
│   │   ├── core/              # Configuración, seguridad (auth/sesiones), auditoría, helpers
│   │   ├── api/               # Routers REST (auth, servicios, configuración, red, ajustes)
│   │   ├── ai/                # Agente, herramientas, propuestas (propose-only)
│   │   ├── modules/           # systemd, configurador, red, docker, nmap, telemetría
│   │   └── schemas/           # Modelos Pydantic de la API
│   └── tests/                 # Suite de pruebas (pytest)
├── frontend/                  # Panel: HTML + CSS + JS vanilla (Tailwind CDN)
├── deploy/
│   ├── install.sh             # Instalación nativa completa (VM)
│   ├── update.sh              # Actualización y reinicio del servicio
│   ├── helpers/               # Seis helpers privilegiados (root)
│   ├── nexus/allowed-units    # Lista blanca de unidades systemd
│   ├── sudoers.d/nexus        # Reglas sudoers (compatibles con sudo-rs)
│   └── systemd/nexus.service  # Unidad systemd
├── docs/                      # Memoria del TFG
├── requirements.txt           # Dependencias fijadas (Python 3.12 – 3.14)
├── Dockerfile
├── docker-compose.yml
└── Makefile
```

---

## Licencia

Proyecto académico — TFG de SMR. Uso educativo.
