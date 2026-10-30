# Nexus-Core — despliegue en Internet (Fase 4)

Guía para publicar el hub con **HTTPS**, endurecerlo y conectar agentes de otros
servidores. El hub mantiene la base de datos y el panel; los agentes solo abren
conexiones **salientes** al hub por 443.

```
Navegador ──HTTPS──▶ Caddy :443 ──▶ uvicorn 127.0.0.1:8000
Agente remoto ──HTTPS saliente──▶ Caddy :443 /api/agent/*
```

## Requisitos

- VPS Linux (Debian 12 / Ubuntu 24.04) con IP pública.
- Dominio con registro DNS **A** apuntando a la IP del VPS.
- Puertos **80/443** abiertos (Caddy los necesita para emitir el certificado).
- El panel completo (Docker, systemd, SMART, apt) necesita un hub Linux. En un hub
  Windows algunas herramientas responden «no disponible», igual que en el agente.

## 1. DNS y firewall

Crea el registro A `nexus.example.com → IP_PUBLICA` y espera la propagación.

```bash
sudo apt update && sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

El puerto 8000 no se abre: uvicorn escuchará solo en `127.0.0.1`.

## 2. Instalar el hub

```bash
sudo apt install -y git python3 python3-venv
git clone https://github.com/<usuario>/Nexus-Core.git ~/nexus-src
cd ~/nexus-src
sudo bash deploy/install.sh
```

El instalador crea `/opt/nexus-core`, el usuario `nexus`, `/var/lib/nexus`, los
helpers, la unidad systemd y el token de primer arranque.

## 3. Entorno de producción

Genera el token de enrolamiento y crea el entorno con permisos 0600:

```bash
DOMAIN="nexus.example.com"
TOKEN="$(openssl rand -hex 32)"

sudo install -d -m 0750 /etc/nexus
sudo cp deploy/production/nexus.env.example /etc/nexus/nexus.env
sudo sed -i "s/nexus.example.com/${DOMAIN}/g; s/cambia-esto-por-openssl-rand-hex-32/${TOKEN}/" /etc/nexus/nexus.env
sudo chmod 0600 /etc/nexus/nexus.env
sudo systemctl restart nexus
```

La unidad carga `/etc/nexus/nexus.env` (`EnvironmentFile`) y usa `--proxy-headers`
para que la IP y el esquema reales lleguen desde Caddy. `NEXUS_BIND_HOST=127.0.0.1`
impide exponer uvicorn directamente.

## 4. TLS con Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

sudo cp deploy/caddy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i "s/nexus.example.com/${DOMAIN}/g" /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy emite y renueva el certificado automáticamente. Sirve HSTS, `nosniff`,
`DENY` de framing, `Referrer-Policy` y `Permissions-Policy`, y redirige HTTP a HTTPS.

Comprueba desde fuera:

```bash
bash deploy/production/check-production.sh "$DOMAIN"
```

## 5. Primera entrada

Abre `https://TU-DOMINIO/login.html`. El token de configuración inicial está en:

```bash
sudo cat /var/lib/nexus/setup_token
```

Crea el administrador y guarda la contraseña en un gestor. La cookie de sesión ya
viaja con `Secure` (HTTPS obligatorio) y `ALLOWED_HOSTS` limita el host aceptado.

## 6. Conectar un agente en otro servidor Linux

En el hub: pestaña **Hosts → Copiar token** (o `GET /api/hosts/enrollment`).
En el host vigilado:

```bash
sudo apt install -y git python3 python3-venv
git clone https://github.com/<usuario>/Nexus-Core.git ~/nexus-src
cd ~/nexus-src

sudo bash deploy/agent/install-agent.sh \
  --hub https://TU-DOMINIO \
  --enroll-token "TOKEN_DE_ENROLAMIENTO" \
  --name "nodo-1" \
  --docker
```

- `--docker` añade el usuario del agente al grupo Docker para gestionar contenedores.
- **No abras ningún puerto en el host vigilado**: el agente solo inicia conexiones
  salientes a 443.
- Comprueba `sudo systemctl status nexus-agent --no-pager` y que el host aparece
  «En línea» en **Hosts**.

## 7. Copias de seguridad

```bash
sudo install -m 0644 deploy/production/nexus-backup.service /etc/systemd/system/
sudo install -m 0644 deploy/production/nexus-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nexus-backup.timer
sudo systemctl start nexus-backup.service
sudo ls -l /var/backups/nexus
```

El script usa la API de backup de SQLite (snapshot consistente), verifica
`PRAGMA integrity_check`, incluye `config/` y conserva 14 copias con permisos 0600.
**Las copias contienen secretos** (token de enrolamiento y claves de IA): guárdalas
fuera del servidor y nunca las subas a un repositorio.
Para restaurar:

```bash
sudo systemctl stop nexus
sudo mkdir -p /tmp/nexus-restore
sudo tar -xzf /var/backups/nexus/nexus-AAAAmmdd-HHMMSS.tar.gz -C /tmp/nexus-restore
sudo install -o nexus -g nexus -m 0600 /tmp/nexus-restore/nexus.db /var/lib/nexus/nexus.db
sudo systemctl start nexus
```

## 8. Actualizaciones y rotación

```bash
cd ~/nexus-src && git pull
sudo bash deploy/update.sh
```

Si cambia la unidad systemd, reinstálala:

```bash
sudo install -m 0644 deploy/systemd/nexus.service /etc/systemd/system/nexus.service
sudo systemctl daemon-reload && sudo systemctl restart nexus
```

Rotar el token de enrolamiento: cambia `AGENT_ENROLL_TOKEN` en
`/etc/nexus/nexus.env` y reinicia. Los tokens de los agentes ya enrolados no se
ven afectados; el token anterior deja de servir para altas nuevas.

## Checklist de endurecimiento

- [ ] SSH con claves, sin login de root y `PasswordAuthentication no`.
- [ ] UFW: solo 22/80/443; el 8000 no es accesible desde fuera.
- [ ] `SESSION_COOKIE_SECURE=true` y `ALLOWED_HOSTS` con el dominio real.
- [ ] `AGENT_ENROLL_TOKEN` aleatorio y guardado como secreto.
- [ ] `/etc/nexus/nexus.env` en 0600 y `/var/lib/nexus` en 0700.
- [ ] Backups diarios activos y **restauración probada**.
- [ ] `unattended-upgrades` y `fail2ban` (opcional) instalados.
- [ ] Caddy con HSTS y cabeceras; `check-production.sh` en verde.
- [ ] Token de primer arranque eliminado tras crear el administrador.

## Limitaciones conocidas

- TLS necesita un dominio real y los puertos 80/443; no se puede probar en local.
- El hub Windows no expone Docker/systemd/SMART/apt; un hub Linux sí.
- El inventario remoto de volúmenes/imágenes/redes Docker todavía no está expuesto
  por el agente (sí listar/desplegar/eliminar/logs/stats).
