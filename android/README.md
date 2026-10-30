# Nexus-Core Android

Cliente **nativo** de Nexus-Core para Android. Está hecho con **Kotlin + Jetpack Compose** y no usa WebView: toda la interfaz es nativa.

## Estado

- 14 secciones nativas: Resumen, Hosts, Contenedores, Servicios, Red, Alertas, Histórico, Monitores, Auditoría, Sistema, Seguridad, Terminal, Asistente y Ajustes.
- Login con **usuario + contraseña + TOTP** (Google Authenticator).
- Sesión persistente con cookie HTTP-only y **CSRF**.
- Selector global de host y datos del hub central.
- Auditor Lynis-style con puntuación, hallazgos, baseline y registro.
- Acciones principales: hosts, contenedores, servicios, alertas, monitores, tokens y sesiones.
- **Cambio de IP del hub** con planificación, watchdog, confirmación y reversión.
- **Gestión completa de 2FA**: alta, activación con código, códigos de recuperación y desactivación con contraseña.
- **Editor de IA**: proveedor, modelo, temperatura, URLs base y claves (las claves actuales nunca se muestran).
- Terminal nativa por WebSocket: PTY local del hub o shell remota transportada por el agente 1.1.0, sin SSH y sin depender de la red local.
- Asistente con propuestas pendientes de confirmación.
- Aviso de alertas en segundo plano con WorkManager cada 15 minutos.
- HTTPS obligatorio, sin tráfico en claro.

## Requisitos de instalaci?n

- **Android 8.0 o superior** (`minSdk 26`); target SDK 37.
- ABIs empaquetadas: `arm64-v8a`, `armeabi-v7a`, `x86` y `x86_64`.
- El APK disponible es de **debug**, firmado con la clave de debug; se puede instalar por sideload/ADB.
- Para Play Store hace falta una **release firmada**, keystore de producci?n, pol?tica de privacidad, ficha y AAB.
- URL del hub editable en el login; por defecto `https://nexus.example.com`. Solo HTTPS.
- El hub debe ser accesible por HTTPS y la cuenta debe tener 2FA activo.

## Distribución privada y licencias

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

## Compilar

Requisitos: Android Studio 2026.2 o compatible, SDK Android 37 y Build Tools 36.

Abre la carpeta `android/` directamente en Android Studio y pulsa **Run**, o desde consola:

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat :app:assembleDebug
```

APK:

```text
app\build\outputs\apk\debug\app-debug.apk
```

También hay una copia en:

```text
Nexus-Core-Android.apk
```

## Configuración

- URL por defecto: `https://nexus.example.com`.
- Usuario: el mismo del panel.
- 2FA: el código de Google Authenticator o un código de recuperación.
- La app no guarda la contraseña; guarda la sesión cifrada con Android Keystore.

## Límites conscientes

- **No FCM**: la sincronización en segundo plano usa WorkManager (mínimo 15 minutos). Para push inmediato habría que añadir Firebase/FCM al hub.
- **Cambio de IP** solo está soportado en el hub local con netplan; en un host remoto se hace desde el panel web.
- **Reconfigurar 2FA** invalida el secreto anterior en cuanto se inicia el alta; la app avisa y muestra los códigos nuevos una única vez.
- La terminal solo funciona en hosts con destino en `TERMINAL_TARGETS`; si no está configurado, muestra el motivo.
- La app consulta el hub en vivo; no replica la SQLite central ni guarda una copia completa de la base de datos.
- Los logs/stats locales de contenedores dependen de que el hub exponga esas rutas; en hosts remotos se ejecutan por el agente.
- APK de debug firmado con la clave de debug. Para distribuirlo por Play Store hay que crear una release firmada, política de privacidad y ficha de tienda.

## Seguridad

- HTTPS y validación de certificado del sistema.
- Sin `usesCleartextTraffic`.
- Cookie de sesión Secure/HttpOnly, CSRF en cada mutación.
- Credenciales de sesión en `EncryptedSharedPreferences`.
- No se incrustan tokens `write` en la app.
- Confirmación explícita para eliminar contenedores, reglas, hosts y limpiar Docker.
