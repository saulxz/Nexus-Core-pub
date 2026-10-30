Nexus-Core — App de escritorio (Windows)
========================================

1. Descomprime esta carpeta donde quieras.
2. Ejecuta Nexus-Core.exe.
3. La app arranca el hub local, levanta el tunel fijo de Cloudflare y abre
   el panel en una ventana nativa.

Mientras la app este abierta:
  - https://nexus.example.com sigue disponible para los usuarios Linux.
  - Los servidores con nexus-agent pueden conectar y monitorizarse.
Al cerrar la ventana se detienen el hub y el tunel.

Requisitos
----------
- Windows 10/11 con WebView2 (viene preinstalado en Windows 11 y en Edge).
- cloudflared en %USERPROFILE%\.local\cloudflared\cloudflared.exe
- Config del tunel en %LOCALAPPDATA%\Nexus-Core\cloudflared\config.yml
  (la app la copia automaticamente desde la carpeta del proyecto si existe).

Datos y logs
------------
- Datos: %USERPROFILE%\.local\nexus-data (si existe; si no,
  %LOCALAPPDATA%\Nexus-Core\data)
- Logs : %LOCALAPPDATA%\Nexus-Core\logs\desktop.log
- Tunel: %LOCALAPPDATA%\Nexus-Core\logs\tunnel.log

Notas
-----
- El login y el CSRF se mantienen; WebView2 guarda la sesion.
- En Windows no hay nmap, systemd, SMART ni Docker del host Linux: el panel
  monitoriza los servidores remotos a traves de sus agentes.
- Para parar todo si la ventana no responde: Cerrar Nexus.cmd
