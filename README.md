# Nexus-Core

> **Plataforma Web de Administración de Infraestructura y Ciberseguridad con Agente de IA.**
> Trabajo Fin de Grado — Ciclo Formativo de Grado Superior en Sistemas Microinformáticos y Redes (SMR).

Nexus-Core convierte un servidor Linux (máquina virtual o Raspberry Pi) en un
**panel de control centralizado** desde el que administrar los **servicios del
sistema** (Samba, DHCP, DNS…), **contenedores Docker**, la **red** (interfaces,
cambio de IP seguro) y la **telemetría** — con un **agente de IA** que propone
acciones en lenguaje natural y un administrador que las confirma.

> **Documentación completa:** [Guía educativa y técnica](docs/GUIA-EDUCATIVA.md) ·
> [PDF de la guía](Nexus-Core-Guia-Educativa.pdf) ·
> [Despliegue en producción](deploy/PRODUCCION.md) ·
> [Memoria del TFG](docs/Memoria_TFG_Nexus-Core.md)

---

## Tabla de contenidos

- [Guía educativa y documentación](#guía-educativa-y-documentación)
- [Características](#características)
- [Requisitos](#requisitos)
- [Instalación en VM (recomendada)](#instalación-en-vm-recomendada)
- [Primer arranque](#primer-arranque)
- [Actualización](#actualización)
- [Instalación con Docker](#instalación-con-docker-alternativa)
- [Monitorización de varios hosts (agente remoto)](#monitorización-de-varios-hosts-agente-remoto)
- [Panel web](#panel-web)
- [Android nativo](#android-nativo)
- [Agente de IA](#agente-de-ia)
- [API REST](#api-rest)
- [Seguridad](#seguridad)
- [Estructura del proyecto](#estructura-del-proyecto)

---

## Guía educativa y documentación

Este repositorio incluye documentación pensada tanto para usar la plataforma como
para explicarla en clase:

| Documento | Contenido |
|---|---|
| [`docs/GUIA-EDUCATIVA.md`](docs/GUIA-EDUCATIVA.md) | Guía completa: arquitectura, datos, redes, seguridad, operación, laboratorios y preguntas de repaso |
| [`Nexus-Core-Guia-Educativa.pdf`](Nexus-Core-Guia-Educativa.pdf) | La misma guía maquetada para imprimir o proyectar |
| [`deploy/PRODUCCION.md`](deploy/PRODUCCION.md) | TLS con Caddy, Cloudflare Tunnel, firewall, backups, agente en Linux y checklist de seguridad |
| [`docs/Memoria_TFG_Nexus-Core.md`](docs/Memoria_TFG_Nexus-Core.md) | Memoria académica del Trabajo Fin de Grado |
| [`PENDIENTE.md`](PENDIENTE.md) | Trabajo pendiente y decisiones abiertas del desarrollo |

### Arquitectura en una imagen

```text
Navegador ──HTTPS──▶ Hub (FastAPI + SQLite + panel)
                         ▲
                         │ long-poll / comandos (saliente desde cada host)
                         │
          ┌──────────────┼──────────────┐
          │              │              │
     Agente Linux   Agente Linux   Agente Windows
     (systemd)      (Docker)       (limitado)
```

- **Hub**: panel, API, base de datos, alertas, monitores, auditoría y despachador.
- **Agentes**: recogen métricas y ejecutan una allowlist de herramientas; no abren
  puertos y funcionan detrás de NAT.
- **Datos**: SQLite con muestras cada 5 s, agregados por minuto/hora y backups
  consistentes.
- **Seguridad**: 2FA TOTP, sesiones con CSRF, rate limiting, cookies Secure,
  allowlists, auditoría encadenada, servicio sin root y helpers sudo acotados.

---

## Características

- **Flota multi-host**: hub central + agentes en Linux/Windows; alta con token,
  latido, reconexión automática y comandos por allowlist.
- **Alertas y monitores**: umbrales con duración e histéresis, acknowledgment,
  snooze, notificaciones, monitores HTTP/TCP/SSL y página de estado.
- **Instalador apt con allowlist**: catálogo cerrado (nginx, Samba, dnsmasq, Docker,
  fail2ban y Redis), confirmación explícita, auditoría y helper root-owned; funciona
  en el hub y en los agentes Linux.
- **Terminal local y por agente**: xterm.js con PTY local del hub o shell remota transportada por el WebSocket saliente del agente 1.1.0; sin SSH, sin puertos entrantes y con sesi?n/CSRF/origen. El host local usa PowerShell; Linux/Kali usan su shell. El SSH queda solo como fallback opcional. En agentes Linux/Kali se puede activar `NEXUS_AGENT_TERMINAL_ROOT=1`; el agente sigue como usuario de servicio y el PTY usa `sudo -n /bin/bash -i` con una regla sudoers limitada a shells. Es root explícito, solo para administrador con 2FA.

- **Tema claro/oscuro**: tokens de papel c?lido/tinta, Geist/Geist Mono y Lucide vendorizados; el tema se sincroniza entre web, escritorio y Android.
- **Seguridad reforzada**: 2FA TOTP con códigos de recuperación, cookies Secure,
  CSRF, rate limiting, tokens de API, auditoría encadenada y backups verificados.
- **Auditoría de seguridad read-only**: 50 comprobaciones (+ nmap opcional) de
  sistema, identidad, archivos, SSH, systemd, Docker, red, firewall y aplicación,
  con severidad, evidencia, recomendación, baseline/comparación y exportación JSON.
  No modifica el host.
- **Despliegue**: servicio systemd, Caddy con TLS, Cloudflare Tunnel (Quick/Named),
  entorno de producción separado y guía paso a paso.
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
en la máquina. Puedes instalarlos desde el catálogo apt del panel (3.4) o con
`sudo apt install samba dnsmasq`; Nexus los **gestiona** una vez presentes.

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
3. Instala los **siete helpers privilegiados** (`svc-ctl`, `conf-apply`,
   `conf-restore`, `net-apply`, `net-confirm`, `net-revert`, `pkg-install`), la lista blanca de
   unidades y las reglas sudoers (validadas con `visudo` **antes** de
   instalarse).
4. Registra el servicio systemd `nexus.service` y lo deja corriendo.

**Sin Internet:** coloca las ruedas `.whl` necesarias en `wheelhouse/` junto al
proyecto antes de instalar; el instalador las usará automáticamente (también
sabe crear el entorno con `virtualenv` embebido si el sistema no trae
`python3-venv`).

> **Exponerlo a Internet:** sigue [`deploy/PRODUCCION.md`](deploy/PRODUCCION.md)
> para TLS con Caddy, entorno seguro, backups diarios y enrolamiento de agentes
> remotos por HTTPS.

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

## Monitorización de varios hosts (agente remoto)

El panel no está atado a la máquina donde corre: un **agente** se instala en
cada servidor que quieras vigilar y se conecta **hacia fuera** al hub por HTTPS.
Funciona detrás de NAT porque **no hay ningún puerto que abrir en el agente**:
es el agente quien inicia la conexión contra el hub.

```
   servidor vigilado                        hub (el panel)
  ┌────────────────────┐                 ┌──────────────────────┐
  │ nexus-agent.service│── HTTPS ───────▶│ /api/agent/*         │
  │  latido cada 5 s   │  (salida)       │  histórico y alertas │
  │  long-poll comandos│◀─ comando ──────│  por host            │
  └────────────────────┘                 └──────────────────────┘
        sin puertos de entrada
```

El servidor donde corre el panel es el **host 1** («Este servidor») y sigue
funcionando exactamente igual que siempre: si no instalas ningún agente, todo es
igual que en el modo local.

### 1. Obtener el token de enrolamiento

Desde el panel (sesión de administrador), o por API con un token de API:

```bash
curl -s -b cookies.txt -H "X-CSRF-Token: $CSRF" https://nexus.example.com/api/hosts/enrollment
```

```json
{ "token": "…", "source": "generado",
  "command": "python3 agent.py enroll --hub https://… --enroll-token … --name NOMBRE" }
```

> **Es un secreto**: quien lo tenga puede dar de alta un host en tu panel. Se
> puede fijar con `AGENT_ENROLL_TOKEN` en el entorno del hub; si no se define, el
> hub genera uno y lo guarda en `$NEXUS_DATA_DIR/config/agent_enroll_token`.

### 2. Instalar el agente en el host remoto

```bash
git clone <repo> nexus-core && cd nexus-core/deploy/agent
sudo bash install-agent.sh \
  --hub https://nexus.example.com \
  --enroll-token "TOKEN_DEL_PANEL" \
  --name "nodo-almacen" \
  --docker            # opcional: para gestionar contenedores
```

El instalador crea el usuario de sistema `nexus-agent` (sin shell), copia el
backend a `/opt/nexus-agent`, instala el subset de dependencias
(`requirements-agent.txt`: httpx, psutil, docker, loguru, python-nmap, pydantic y
pydantic-settings), crea el venv en `/opt/nexus-agent/venv`, enrola el host desde
ese directorio y habilita `nexus-agent.service` con `Restart=always`.

No hay una instalación manual equivalente soportada: el enrolamiento necesita el
venv y el cwd de `/opt/nexus-agent`. Si el hub cambia de URL (por ejemplo, un Quick
Tunnel nuevo) o se reconstruye la base, vuelve a ejecutar el instalador con la URL
y el token actuales; se crea una entrada nueva y la anterior se elimina si queda
offline.

### 3. Comprobarlo

```bash
cd /opt/nexus-agent && sudo -u nexus-agent ./venv/bin/python agent.py status  # latido + herramientas
systemctl status nexus-agent
journalctl -u nexus-agent -f
```

En el panel, `GET /api/hosts` muestra cada host con su estado (`online` si el
último latido es de menos de 30 s) y sus series de métricas.

### Firewall

**No abras puertos en el host vigilado.** El agente solo hace peticiones
salientes al hub (`https://<hub>:443`, o el puerto inverso que uses). En el
firewall del host remoto no hay ninguna regla de entrada que añadir; en el del
hub, solo el puerto HTTPS que ya usa el panel.

### Herramientas que el agente puede ejecutar

El hub solo puede pedir herramientas de esta **allowlist** (nunca shell
arbitrario). Las que no existem en el host (Docker sin socket, `smartctl` sin
instalar, systemd ausente) devuelven un error estructurado, no un fallo:

| Herramienta | Subacciones |
|---|---|
| `metrics` | — |
| `docker` | `list`, `logs`, `stats`, `deploy`, `remove`, `prune` (exige `confirm`) |
| `services` | `list`, `detail`, `logs`, `action` |
| `network` | `interfaces`, `scan` |
| `system` | `ports`, `updates`, `smart` |
| `diagnostics` | *no implementado*: devuelve error |

Desde el panel, un comando se despacha y **espera** el resultado:

```bash
curl -s -X POST https://nexus.example.com/api/hosts/2/command \
  -H "X-CSRF-Token: $CSRF" -H "Content-Type: application/json" \
  -d '{"tool": "docker.list", "args": {}, "timeout_s": 30}'
```

`409` si el host está desconectado, `504` si no responde en el plazo (el comando
queda marcado como `expired`).

### Terminal remota sin SSH

El agente 1.1.0 abre un WebSocket saliente contra el hub y expone una PTY local.
El navegador solo habla con el hub; el hub reenv?a la sesi?n al agente. Funciona
detr?s de NAT, sin abrir puertos, sin SSH y sin que el puesto del administrador est?
en la misma red. En Windows la shell es PowerShell; en Linux/Kali se usa la shell
del servicio. La sesi?n exige administrador, 2FA, CSRF y mismo origen.

Archivos principales: `backend/app/core/agent_terminals.py`,
`backend/app/api/routes_agent.py`, `backend/app/api/routes_terminal.py` y
`backend/agent.py`.

### Desinstalar

```bash
sudo systemctl disable --now nexus-agent
sudo rm /etc/systemd/system/nexus-agent.service /etc/nexus-agent/config.json
sudo userdel nexus-agent
```

Y en el panel: `DELETE /api/hosts/{id}` (revoca el token y borra sus datos; el
host local no se puede eliminar).
---

## Panel web

| Pestaña | Qué ofrece |
|---|---|
| **Dashboard** | Telemetría en tiempo real con gráfica CPU/RAM |
| **Contenedores** | Listar, desplegar y eliminar contenedores del host |
| **Red** | Interfaces de la máquina (IP/máscara/MAC/MTU), escaneo de hosts y cambio de IP con reversión automática |
| **Servicios** | Inventario systemd con buscador y filtros, logs, control (start/stop/restart/reload) y configuración guiada de Samba y dnsmasq con copias de seguridad |
| **Terminal** | Terminal local o por agente en el navegador (xterm.js + WebSocket autenticado), con desplegable de hosts y transportes; sin SSH obligatorio |
| **Ajustes** | Configuración del agente de IA: proveedor, modelo, claves y prueba de conexión en caliente |
| **Asistente IA** | Chat en lenguaje natural: consulta datos reales y **propone** acciones que tú confirmas |

---

## Android nativo

Además del panel web, el repositorio incluye un cliente **Android nativo** en
[`android/`](android/README.md), escrito en **Kotlin + Jetpack Compose** y sin WebView.

- **14 secciones**: Resumen, Hosts, Contenedores, Servicios, Red, Alertas, Histórico,
  Monitores, Auditoría, Sistema, Seguridad, Terminal, Asistente y Ajustes.
- Login con **usuario + contraseña + TOTP** y sesión persistente con cookie/CSRF.
- Selector global de host y datos del hub central; no replica la SQLite.
- Cambio de IP con watchdog, gestión completa de 2FA y editor de configuración de IA.
- **Requisitos del APK**: Android 8.0+ (`minSdk 26`), target 37, ABIs `arm64-v8a`, `armeabi-v7a`, `x86` y `x86_64`. El APK de debug est? firmado con la clave de debug y es instalable por sideload.
- **No es todav?a un build de Play Store**: la variante release no est? firmada; faltan keystore de producci?n, ficha de tienda y pol?tica de privacidad.
- **APK de debug**: `android/app/build/outputs/apk/debug/app-debug.apk`.
- Compilación: abre `android/` en Android Studio (SDK 37) o ejecuta
  `android\gradlew.bat :app:assembleDebug`.
- Detalles y límites: [`android/README.md`](android/README.md).

---

## Edición privada self-hosted

Nexus-Core también puede distribuirse como **edición privada**: cada comprador
despliega su propio hub, su propio dominio, su propia base de datos y su propio
administrador. La APK es solo el cliente; no está atada a un hub central ni
incluye credenciales.

- La URL del hub se configura en el primer arranque y debe ser HTTPS.
- La licencia es independiente del hub y del dominio. La opción recomendada es
  un archivo de licencia firmado offline (Ed25519) con comprador, caducidad,
  límite de dispositivos y firma; la APK solo incorpora la clave pública.
- La APK de venta debe ser `release`, sin `debuggable`, firmada con un keystore
  privado y con R8/ProGuard.
- La compra se entrega por web/teléfono mediante un enlace privado con APK,
  licencia, SHA-256, huella del certificado e instrucciones.
- Android puede mostrar el aviso de “app de origen desconocido” al instalar
  fuera de Google Play; no se puede eliminar desde la app. Para evitarlo por
  completo hay que usar Play privado/closed testing o Managed Google Play.
- No se comparte el administrador del hub: cada comprador crea el suyo y activa
  su propio 2FA. La licencia no da acceso al hub de otra persona.

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
| `POST` | `/api/agent/enroll` | Enrola un agente (token de enrolamiento) |
| `POST` | `/api/agent/heartbeat` | Latido con métricas del host |
| `GET` | `/api/agent/commands` | Sondeo de comandos (long-poll) |
| `POST` | `/api/agent/commands/{id}/result` | Resultado de un comando |
| `GET` | `/api/hosts` | Inventario de la flota con estado |
| `GET` | `/api/hosts/enrollment` | Token de enrolamiento (secreto) |
| `GET` | `/api/hosts/{id}/metrics/history` | Histórico de un host |
| `POST` | `/api/hosts/{id}/command` | Ejecuta una herramienta en el host y espera |

---

## Seguridad

- **Autenticación obligatoria** para todo el panel (argon2id, sesiones con
  caducidad y CSRF); `/api/health` es público para el healthcheck.
- **Auditoría** en JSONL encadenada por hash: cualquier manipulación del
  registro es detectable. Las claves nunca se escriben en la auditoría.
- **Helpers privilegiados**: la aplicación corre como usuario sin privilegios y
  solo puede ejecutar siete scripts root con lista blanca (sudoers exacto, sin
  comodines). Cada helper valida sus propios argumentos.
- **Configuración con rollback**: validación con la herramienta oficial del
  servicio antes y después de aplicar; copia previa y restauración automática.
- **Cambio de IP con watchdog**: reversión automática si no se confirma.
- **Terminal local/por agente**: solo administrador; exige cookie de sesión, CSRF en el
  subprotocolo `csrf.<token>`, mismo origen y `known_hosts` fijado. El desplegable
  solo permite hosts del inventario; el agente 1.1.0 usa su canal saliente y SSH queda como fallback configurado en `TERMINAL_TARGETS`;
  la IP y el usuario SSH nunca vienen del navegador. La clave vive en el servidor
  (`/etc/nexus/terminal_key`), nunca en el cliente, y la sesión se cierra al cambiar
  de pestaña o por inactividad.
- **IA *propose-only*** con confirmación humana y defensa anti *prompt injection*.
- **Socket Docker**: equivale a root sobre el host. Despliega Nexus-Core solo en
  redes de confianza y **nunca lo expongas directamente a Internet** sin TLS y
  autenticación adicional (p. ej. tras un proxy inverso).
- **Escaneos** restringidos a rangos privados RFC1918.
- **Agentes remotos**: cada agente tiene su propio token (el hub solo guarda su
  SHA-256), no tiene sesión ni CSRF, ejecuta únicamente herramientas de una
  lista blanca y solo puede cerrar comandos **de su propio host**. No requiere
  abrir puertos en el host vigilado.

- **HTTPS obligatorio**: el panel público redirige HTTP a HTTPS (308), envía HSTS,
  CSP, COOP/CORP y `Permissions-Policy`, y sirve los assets JS sin CDN.
- **Secretos en reposo**: TOTP y backups se cifran con Fernet; las claves quedan fuera
  de `NEXUS_DATA_DIR` y de los backups. La clave de IA también se cifra en `ai.env`.
- **Autorización fina**: un token de API `read` no puede leer el token de enrolamiento;
  `/docs`, `/openapi.json` y Prometheus quedan cerrados por defecto.
- **Validación**: comentarios Samba sin CR/LF, agente solo HTTPS, límite de body y
  máximo de long-polls por host; el rate limiting usa la IP real de Cloudflare.

---

## Estructura del proyecto

```
Nexus-Core/
├── backend/
│   ├── app/
│   │   ├── main.py            # Entrypoint FastAPI, routers, middleware, ciclo de vida
│   │   ├── core/              # Configuración, seguridad (auth/sesiones), auditoría, helpers, flota
│   │   ├── api/               # Routers REST (auth, servicios, configuración, red, ajustes, flota, agente)
│   │   ├── ai/                # Agente, herramientas, propuestas (propose-only)
│   │   ├── modules/           # systemd, configurador, red, docker, nmap, telemetría
│   │   └── schemas/           # Modelos Pydantic de la API
│   ├── agent.py               # Agente remoto (enrolamiento + latido + comandos)
│   └── tests/                 # Suite de pruebas (pytest)
├── frontend/                  # Panel: HTML + CSS + JS vanilla (Tailwind/Chart vendorizados)
├── deploy/
│   ├── install.sh             # Instalación nativa completa (VM)
│   ├── update.sh              # Actualización y reinicio del servicio
│   ├── helpers/               # Siete helpers privilegiados (root)
│   ├── nexus/allowed-units    # Lista blanca de unidades systemd
│   ├── sudoers.d/nexus        # Reglas sudoers (compatibles con sudo-rs)
│   ├── systemd/nexus.service  # Unidad systemd
│   └── agent/                 # Instalador y unidad del agente remoto
├── android/                   # App Android nativa (Kotlin + Jetpack Compose)
├── docs/                      # Memoria del TFG
├── requirements.txt           # Dependencias fijadas (Python 3.12 – 3.14)
├── Dockerfile
├── docker-compose.yml
└── Makefile
```

---

## Licencia

Proyecto académico — TFG de SMR. Uso educativo.
