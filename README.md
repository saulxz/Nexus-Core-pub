# Nexus-Core

> **Plataforma web de administración de infraestructura y ciberseguridad con agentes remotos.**
> Trabajo Fin de Grado · Ciclo Formativo de Grado Superior en Sistemas Microinformáticos y Redes (SMR).

Nexus-Core convierte un servidor Linux en un **centro de control** para vigilar y
administrar otras máquinas. Desde una única página web se consulta la telemetría,
se gestionan contenedores Docker y servicios systemd, se inventaría la red, se revisan
puertos, actualizaciones y discos, se reciben alertas y se ejecutan acciones con
confirmación y auditoría.

El diseño prioriza tres ideas: **funcionar en local**, **no abrir puertos en las
máquinas vigiladas** y **mantener el control humano** sobre la IA.

---

## Documentación

| Documento | Contenido |
|---|---|
| [`docs/GUIA-EDUCATIVA.md`](docs/GUIA-EDUCATIVA.md) | Guía completa: arquitectura, datos, redes, seguridad, operación y laboratorios |
| [`Nexus-Core-Guia-Educativa.pdf`](Nexus-Core-Guia-Educativa.pdf) | La misma guía maquetada para imprimir o proyectar |
| [`deploy/PRODUCCION.md`](deploy/PRODUCCION.md) | TLS con Caddy, Cloudflare Tunnel, firewall, backups y agentes |

Los ejemplos de esta documentación usan dominios reservados (`nexus.example.com`) y
direcciones de documentación (`192.0.2.10`). No hay credenciales ni datos reales.

---

## Características

- **Flota multi-host**: un hub central y agentes en Linux/Windows. Alta con token,
  latido, reconexión automática y comandos con lista blanca.
- **Panel unificado**: resumen de sensores, histórico, contenedores, servicios,
  terminal local/por agente, red, puertos, actualizaciones, SMART, auditoría y seguridad.
- **Alertas**: umbrales con duración e histéresis, acknowledgment, snooze,
  notificaciones y monitores HTTP/TCP/SSL con página de estado.
- **Instalador apt con allowlist**: catálogo cerrado (nginx, Samba, dnsmasq, Docker,
  Fail2ban y Redis), confirmación, auditoría y helper root-owned; local y agentes Linux.
- **Terminal local/por agente**: pestaña con xterm.js, selector de hosts conectados y WebSocket
  autenticado; clave dedicada en el hub, CSRF/origen, known_hosts fijado y cierre
  por inactividad.
- **Seguridad**: 2FA TOTP con códigos de recuperación, sesiones con CSRF, cookies
  `Secure`, rate limiting, tokens de API, auditoría encadenada y backups verificados.
- **Auditoría de seguridad read-only**: 50 comprobaciones (+ nmap opcional) de
  sistema, identidad, archivos, SSH, systemd, Docker, red, firewall y aplicación,
  con severidad, evidencia, recomendación, baseline/comparación y exportación JSON.
  No modifica el host.
- **IA con control humano**: el asistente propone acciones; las sensibles quedan
  pendientes de confirmación.
- **Despliegue**: servicio systemd, Docker opcional, Caddy con TLS y Cloudflare
  Tunnel (Quick o Named) para acceso remoto sin abrir puertos.
- **Sin dependencias de nube**: el núcleo funciona en una VM o Raspberry Pi.

---

## Arquitectura

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

- **Hub**: panel, API, base de datos, motor de alertas, monitores, auditoría y
  despachador de comandos.
- **Agente**: proceso ligero que envía métricas, mantiene un long-poll y ejecuta
  herramientas permitidas. Solo abre conexiones salientes: funciona detrás de NAT.
- **Datos**: SQLite con muestras cada 5 s, agregados por minuto/hora y copias de
  seguridad consistentes.
- **Seguridad**: el hub escucha en localhost tras un proxy TLS; las cookies son
  `Secure`; el servicio no corre como root; las operaciones privilegiadas pasan por
  helpers acotados.

La guía educativa explica cada decisión y propone laboratorios de aula.

---

## Inicio rápido

### Hub (Linux)

```bash
sudo apt install -y git python3 python3-venv
git clone <URL_DEL_REPOSITORIO> nexus-core
cd nexus-core
sudo bash deploy/install.sh
```

El instalador crea el usuario `nexus`, el entorno virtual, los helpers, la unidad
systemd y el token de primer arranque. El panel queda en `http://<IP>:8000`.

```bash
sudo cat /var/lib/nexus/setup_token
```

Abre el panel, pega el token, crea el administrador y activa el 2FA.

### Agente en otro host

```bash
sudo bash deploy/agent/install-agent.sh \
  --hub https://nexus.example.com \
  --enroll-token '<TOKEN>' \
  --name "host-remoto"
```

No hay que abrir puertos en el host vigilado. El agente sale hacia el hub por HTTPS
y reconecta solo si el hub se reinicia. Si cambia la URL del hub (por ejemplo, un
Quick Tunnel nuevo), vuelve a ejecutar el instalador con la URL y el token actuales;
el host se reenrola.

### Android nativo

También hay un cliente **Android nativo** en [`android/`](android/README.md), escrito
en **Kotlin + Jetpack Compose** y sin WebView. Consume la misma API HTTPS, permite
login con 2FA, gestionar hosts/alertas/auditoría y las operaciones principales desde
el móvil. No replica la base SQLite del hub.

```bash
cd android
./gradlew :app:assembleDebug
```

El APK de debug queda en `android/app/build/outputs/apk/debug/app-debug.apk`.
**Android 8.0+ (`minSdk 26`)**, ABIs `arm64-v8a`, `armeabi-v7a`, `x86` y `x86_64`.
El APK de debug est? firmado con clave de debug y sirve para sideload; la release
de Play Store requiere firma de producci?n, pol?tica de privacidad y ficha.
La URL del hub se configura en la pantalla de login; el valor de ejemplo es
`https://nexus.example.com`.

### Publicación segura

- **Caddy + dominio**: TLS automático, HSTS y cabeceras; ver `deploy/PRODUCCION.md`.
- **Cloudflare Tunnel**: Quick Tunnel para pruebas o Named Tunnel + Cloudflare Access
  para una URL fija y protegida.
- **Nunca** expongas el puerto 8000 directamente a Internet.

---

## Estructura del proyecto

```text
backend/            API FastAPI, agentes, datos, alertas, seguridad y módulos
frontend/           panel web, login, ajustes y estilos
deploy/             instalación, systemd, agentes, Caddy, producción y backups
docs/               guía educativa y memoria del TFG
tests/              pruebas automatizadas del backend
```

## Tests

```bash
python -m pytest backend/tests -q
```

El proyecto incluye pruebas de autenticación, alertas, métricas, monitores,
multi-host, seguridad y sistema. La suite actual pasa con **247 tests y 1 omitido**.

---

## Uso educativo

La guía incluye explicaciones y ocho laboratorios seguros para clase: arquitectura,
monitorización de una VM, alertas de disco, servicio caído, inventario de red, 2FA,
backup/restauración y publicación segura. Todos los ejercicios deben realizarse en
redes propias y con autorización.

## Licencia

Proyecto académico — TFG de SMR. Uso educativo.
