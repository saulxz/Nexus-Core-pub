"""Modelos de datos (Pydantic v2) que definen los contratos de la API REST.

Cada modelo documenta mediante ``Field(description=...)`` el significado de sus
campos, de forma que ``/docs`` (OpenAPI) sirva como documentación viva.
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.monitors import (
    MAX_INTERVAL_S,
    MAX_TIMEOUT_S,
    MIN_INTERVAL_S,
    MIN_TIMEOUT_S,
    TargetError,
    parse_target,
)


# ============================ Contenedores ============================
class DeployContainerRequest(BaseModel):
    """Petición para desplegar un nuevo contenedor."""

    image: str = Field(
        ...,
        description="Nombre de la imagen Docker a desplegar (debe estar en la whitelist).",
        examples=["nginx"],
    )
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description="Puerto del host a mapear al puerto por defecto del contenedor.",
        examples=[8080],
    )
    name: str | None = Field(
        default=None,
        description="Nombre opcional del contenedor (se prefija automáticamente).",
        examples=["web-demo"],
    )


class ContainerInfo(BaseModel):
    """Información resumida de un contenedor Docker."""

    id: str = Field(..., description="Identificador completo del contenedor.")
    short_id: str = Field(..., description="Identificador corto (12 caracteres).")
    name: str = Field(..., description="Nombre del contenedor.")
    image: str = Field(..., description="Imagen utilizada.")
    status: str = Field(..., description="Estado textual (running, exited, ...).")
    state: str = Field(..., description="Estado normalizado.")
    ports: list[str] = Field(default_factory=list, description="Mapeos de puertos publicados.")
    created: str | None = Field(default=None, description="Fecha de creación (ISO8601).")


class DeployContainerResponse(BaseModel):
    """Respuesta al desplegar un contenedor."""

    success: bool = Field(..., description="Indica si la operación se completó.")
    message: str = Field(..., description="Mensaje descriptivo del resultado.")
    container: ContainerInfo | None = Field(default=None, description="Contenedor creado, si procede.")


class DeleteContainerResponse(BaseModel):
    """Respuesta al eliminar un contenedor."""

    success: bool = Field(..., description="Indica si la eliminación se completó.")
    message: str = Field(..., description="Mensaje descriptivo del resultado.")


# ============================== Métricas ==============================
class MetricsResponse(BaseModel):
    """Telemetría en tiempo real del sistema (Raspberry Pi)."""

    cpu_percent: float = Field(..., description="Uso de CPU (%).")
    cpu_count: int = Field(..., description="Número de núcleos lógicos.")
    ram_percent: float = Field(..., description="Uso de memoria RAM (%).")
    ram_used_mb: float = Field(..., description="RAM usada en MiB.")
    ram_total_mb: float = Field(..., description="RAM total en MiB.")
    swap_percent: float = Field(..., description="Uso de swap (%).")
    disk_percent: float = Field(..., description="Uso del disco raíz (%).")
    cpu_temp_c: float | None = Field(
        default=None, description="Temperatura de la CPU (°C); None si no hay sensor."
    )
    uptime_seconds: float = Field(..., description="Tiempo de actividad del sistema en segundos.")
    load_avg: list[float] = Field(..., description="Carga media (1, 5 y 15 minutos).")
    timestamp: str = Field(..., description="Momento de la medición (ISO8601 UTC).")


# ================================ Red =================================
class NetworkScanRequest(BaseModel):
    """Petición de escaneo de red."""

    subnet: str | None = Field(
        default=None,
        description="Subred en formato CIDR (RFC1918). Si se omite, usa la configurada.",
        examples=["192.168.1.0/24"],
    )


class HostInfo(BaseModel):
    """Host detectado en la red local."""

    ip: str = Field(..., description="Dirección IP del host.")
    status: str = Field(..., description="Estado del host (up/down).")
    mac: str | None = Field(default=None, description="Dirección MAC si es accesible.")
    hostname: str | None = Field(default=None, description="Nombre del host si se resuelve.")


class NetworkScanResponse(BaseModel):
    """Resultado del escaneo de red."""

    subnet: str = Field(..., description="Subred escaneada.")
    hosts: list[HostInfo] = Field(default_factory=list, description="Hosts activos encontrados.")
    count: int = Field(..., description="Número de hosts activos.")
    scanned_at: str = Field(..., description="Momento del escaneo (ISO8601 UTC).")
    error: str | None = Field(default=None, description="Error, si el escaneo falló.")


# ================================ Chat ================================
class ChatMessage(BaseModel):
    """Mensaje individual de una conversación."""

    role: Literal["user", "assistant", "system"] = Field(..., description="Rol del emisor.")
    content: str = Field(..., description="Contenido textual del mensaje.")


class ChatRequest(BaseModel):
    """Petición al Agente de IA."""

    message: str = Field(..., min_length=1, description="Mensaje del usuario en lenguaje natural.")
    history: list[ChatMessage] | None = Field(
        default=None, description="Historial previo de la conversación (opcional)."
    )


class ToolAction(BaseModel):
    """Acción (herramienta) ejecutada por el agente durante la conversación."""

    tool: str = Field(..., description="Nombre de la herramienta invocada.")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Argumentos usados.")
    result: dict[str, Any] = Field(default_factory=dict, description="Resultado de la ejecución.")


class ChatResponse(BaseModel):
    """Respuesta del Agente de IA."""

    reply: str = Field(..., description="Respuesta final en lenguaje natural.")
    actions: list[ToolAction] = Field(
        default_factory=list, description="Herramientas ejecutadas durante la interacción."
    )
    pending_actions: list[PendingActionInfo] = Field(
        default_factory=list,
        description="Acciones propuestas pendientes de confirmación humana.",
    )
    model: str = Field(..., description="Modelo LLM utilizado.")
    provider: str = Field(..., description="Proveedor LLM utilizado.")


# =============================== Health ===============================
class HealthResponse(BaseModel):
    """Estado del servicio."""

    status: str = Field(..., description="Estado ('ok').")
    app: str = Field(..., description="Nombre de la aplicación.")
    version: str = Field(..., description="Versión de la aplicación.")


# ============================ Autenticación ============================
class AuthSetupStatusResponse(BaseModel):
    """Estado del primer arranque."""

    needs_setup: bool = Field(
        ...,
        description="True si aún no existe administrador y hay que completar el asistente.",
    )


class AuthSetupRequest(BaseModel):
    """Petición del asistente de primer arranque."""

    token: str = Field(
        ...,
        min_length=8,
        description="Token de configuración mostrado en la consola del servicio.",
    )
    username: str = Field(..., min_length=3, max_length=64, description="Usuario administrador.")
    password: str = Field(
        ...,
        min_length=10,
        max_length=256,
        description="Contraseña del panel (mínimo 10 caracteres).",
    )


class AuthLoginRequest(BaseModel):
    """Petición de inicio de sesión."""

    username: str = Field(..., min_length=1, max_length=64, description="Usuario.")
    password: str = Field(..., min_length=1, max_length=256, description="Contraseña.")
    totp: str | None = Field(
        default=None,
        max_length=32,
        description=(
            "Código de verificación en dos pasos (6 dígitos TOTP) o un código de "
            "recuperación. Solo es obligatorio si la cuenta tiene 2FA activado."
        ),
    )


class AuthSessionResponse(BaseModel):
    """Estado de la sesión autenticada."""

    authenticated: bool = Field(..., description="Siempre True cuando la sesión es válida.")
    username: str = Field(..., description="Usuario de la sesión.")
    csrf: str = Field(
        ...,
        description="Token CSRF que el frontend debe enviar en la cabecera X-CSRF-Token.",
    )


class AuthPasswordChangeRequest(BaseModel):
    """Cambio de contraseña del administrador."""

    current_password: str = Field(..., description="Contraseña actual.")
    new_password: str = Field(
        ..., min_length=10, max_length=256, description="Nueva contraseña."
    )


# ============================ Ajustes de IA ============================
class AISecretState(BaseModel):
    """Estado de una clave de API (nunca se devuelve en claro)."""

    configured: bool = Field(..., description="True si hay una clave guardada.")
    masked: str = Field(..., description="Representación enmascarada (últimos 4 caracteres).")


class AISettingsResponse(BaseModel):
    """Configuración efectiva del agente de IA."""

    provider: str = Field(..., description="Proveedor activo: openai | anthropic | gemini | ollama.")
    model: str = Field(..., description="Modelo efectivo del proveedor activo.")
    temperature: float = Field(..., description="Temperatura de generación (0-2).")
    openai_base_url: str = Field(..., description="URL base de OpenAI / endpoints compatibles.")
    gemini_base_url: str = Field(..., description="URL base de Google Gemini.")
    ollama_base_url: str = Field(..., description="URL base de Ollama (host local).")
    keys: dict[str, AISecretState] = Field(
        ..., description="Estado (enmascarado) de las claves por proveedor."
    )


class AISettingsUpdateRequest(BaseModel):
    """Cambios de configuración de IA enviados desde la web.

    Un campo ausente o vacío: en los secretos significa «mantener la clave
    actual»; en el resto, «no cambiar».
    """

    provider: str | None = Field(default=None, description="Proveedor LLM.")
    model: str | None = Field(default=None, description="Modelo a usar.")
    temperature: float | None = Field(default=None, ge=0, le=2, description="Temperatura.")
    openai_api_key: str | None = Field(default=None, description="Nueva clave de OpenAI.")
    anthropic_api_key: str | None = Field(default=None, description="Nueva clave de Anthropic.")
    gemini_api_key: str | None = Field(default=None, description="Nueva clave de Gemini.")
    openai_base_url: str | None = Field(default=None, description="URL base de OpenAI.")
    gemini_base_url: str | None = Field(default=None, description="URL base de Gemini.")
    ollama_base_url: str | None = Field(default=None, description="URL base de Ollama.")


class AITestResponse(BaseModel):
    """Resultado de la prueba de conexión con el proveedor de IA."""

    ok: bool = Field(..., description="True si el proveedor respondió correctamente.")
    provider: str = Field(..., description="Proveedor probado.")
    model: str = Field(..., description="Modelo probado.")
    latency_ms: int | None = Field(default=None, description="Latencia de la prueba (ms).")
    detail: str = Field(..., description="Mensaje descriptivo del resultado.")


# ============================ Servicios ============================
class ServiceInfo(BaseModel):
    """Resumen de una unidad de servicio del sistema."""

    name: str = Field(..., description="Nombre de la unidad (p. ej. smbd.service).")
    friendly_name: str | None = Field(
        default=None, description="Nombre amigable si es un servicio objetivo del panel."
    )
    description: str = Field(default="", description="Descripción de la unidad.")
    load_state: str = Field(default="", description="Estado de carga (loaded, not-found...).")
    active_state: str = Field(default="", description="Estado activo (active, inactive, failed...).")
    sub_state: str = Field(default="", description="Subestado (running, exited...).")
    is_target: bool = Field(default=False, description="True si es un servicio objetivo del panel.")


class ServiceListResponse(BaseModel):
    """Inventario de servicios del sistema."""

    available: bool = Field(..., description="False si systemd no está disponible.")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    count: int = Field(..., description="Número de servicios listados.")
    services: list[ServiceInfo] = Field(default_factory=list, description="Servicios.")


class ServiceDetailResponse(BaseModel):
    """Detalle de una unidad de servicio."""

    name: str = Field(..., description="Nombre de la unidad.")
    friendly_name: str | None = Field(default=None, description="Nombre amigable si aplica.")
    description: str = Field(default="", description="Descripción de la unidad.")
    load_state: str = Field(default="", description="Estado de carga.")
    active_state: str = Field(default="", description="Estado activo.")
    sub_state: str = Field(default="", description="Subestado.")
    unit_file_state: str | None = Field(default=None, description="Estado del fichero de unidad.")
    main_pid: int = Field(default=0, description="PID del proceso principal (0 si no corre).")
    memory_bytes: int | None = Field(default=None, description="Memoria actual en bytes.")
    exec_main_status: int = Field(default=0, description="Código de salida del último proceso.")
    active_enter_timestamp: str | None = Field(
        default=None, description="Marca temporal de la última activación."
    )
    fragment_path: str | None = Field(default=None, description="Ruta del fichero de unidad.")
    is_target: bool = Field(default=False, description="True si es servicio objetivo del panel.")


class ServiceLogsResponse(BaseModel):
    """Últimas líneas del journal de una unidad."""

    available: bool = Field(..., description="False si journalctl no es accesible.")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    count: int = Field(..., description="Número de líneas devueltas.")
    lines: list[str] = Field(default_factory=list, description="Líneas del journal.")


class ServiceActionRequest(BaseModel):
    """Acción de control solicitada sobre un servicio."""

    action: Literal["start", "stop", "restart", "reload"] = Field(
        ..., description="Acción a ejecutar sobre la unidad."
    )


class ServiceActionResponse(BaseModel):
    """Resultado de una acción de control."""

    success: bool = Field(..., description="True si la acción se ejecutó.")
    message: str = Field(..., description="Mensaje descriptivo del resultado.")
    service: ServiceDetailResponse | None = Field(
        default=None, description="Estado actualizado de la unidad, si procede."
    )


# ======================= Configuración de servicios =======================
class SambaShare(BaseModel):
    """Share de Samba definida desde el panel."""

    name: str = Field(..., min_length=1, max_length=80, description="Nombre de la share.")
    path: str = Field(..., min_length=1, max_length=255, description="Ruta absoluta en el servidor.")
    comment: str = Field(default="", max_length=200, description="Comentario visible en la red.")
    read_only: bool = Field(default=True, description="Solo lectura.")
    browseable: bool = Field(default=True, description="Visible al explorar la red.")
    guest_ok: bool = Field(default=False, description="Permitir acceso invitado.")
    valid_users: str = Field(
        default="", max_length=300, description="Usuarios autorizados (csv), vacío = todos."
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}", value):
            raise ValueError(
                "Nombre de share no válido (letras, números, espacio, punto, guion)."
            )
        return value

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        if not value.startswith("/") or ".." in value:
            raise ValueError("La ruta debe ser absoluta y no puede contener '..'.")
        if not re.fullmatch(r"[A-Za-z0-9/._ -]{1,255}", value):
            raise ValueError("La ruta contiene caracteres no permitidos.")
        return value

    @field_validator("valid_users")
    @classmethod
    def _validate_users(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_, .@-]{1,300}", value):
            raise ValueError("Lista de usuarios no válida.")
        return value


class SambaConfig(BaseModel):
    """Configuración completa de Samba gestionada por el panel."""

    workgroup: str = Field(default="WORKGROUP", max_length=15, description="Grupo de trabajo.")
    server_string: str = Field(default="Nexus-Core Samba", max_length=64, description="Descripción del servidor.")
    map_to_guest: Literal["Never", "Bad User", "Bad Password"] = Field(
        default="Never", description="Tratamiento del acceso invitado."
    )
    shares: list[SambaShare] = Field(default_factory=list, max_length=50, description="Shares publicadas.")

    @field_validator("workgroup")
    @classmethod
    def _validate_workgroup(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,15}", value):
            raise ValueError("Grupo de trabajo no válido (letras, números y guiones).")
        return value.upper()

    @field_validator("server_string")
    @classmethod
    def _validate_server_string(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,64}", value):
            raise ValueError("Descripción del servidor no válida.")
        return value


class DnsmasqRecord(BaseModel):
    """Registro local nombre -> IP de dnsmasq."""

    name: str = Field(..., min_length=1, max_length=63, description="Nombre del host.")
    address: str = Field(..., description="Dirección IPv4.")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,61}[A-Za-z0-9])?", value):
            raise ValueError("Nombre de registro no válido.")
        return value

    @field_validator("address")
    @classmethod
    def _validate_address(cls, value: str) -> str:
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise ValueError(f"Dirección IPv4 no válida: {value}") from exc
        return value


class DnsmasqDhcpRange(BaseModel):
    """Rango DHCP gestionado por dnsmasq."""

    enabled: bool = Field(default=False, description="Activar el servidor DHCP.")
    start: str = Field(default="192.168.1.100", description="Primera IP del rango.")
    end: str = Field(default="192.168.1.200", description="Última IP del rango.")
    lease_time: str = Field(default="12h", pattern=r"^\d{1,4}[smhd]$", description="Duración de la concesión.")

    @field_validator("start", "end")
    @classmethod
    def _validate_ip(cls, value: str) -> str:
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise ValueError(f"Dirección IPv4 no válida: {value}") from exc
        return value

    @model_validator(mode="after")
    def _check_range(self) -> "DnsmasqDhcpRange":
        """Comprueba que el rango es coherente (inicio <= fin)."""
        if int(ipaddress.IPv4Address(self.start)) > int(ipaddress.IPv4Address(self.end)):
            raise ValueError("El inicio del rango DHCP no puede ser mayor que el fin.")
        return self


class DnsmasqReservation(BaseModel):
    """Reserva DHCP por MAC."""

    mac: str = Field(..., description="Dirección MAC (aa:bb:cc:dd:ee:ff).")
    address: str = Field(..., description="Dirección IPv4 reservada.")
    hostname: str = Field(default="", max_length=63, description="Nombre opcional del host.")

    @field_validator("mac")
    @classmethod
    def _validate_mac(cls, value: str) -> str:
        if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", value):
            raise ValueError("Dirección MAC no válida (formato aa:bb:cc:dd:ee:ff).")
        return value.lower()

    @field_validator("address")
    @classmethod
    def _validate_address(cls, value: str) -> str:
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise ValueError(f"Dirección IPv4 no válida: {value}") from exc
        return value

    @field_validator("hostname")
    @classmethod
    def _validate_hostname(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9-]{0,62})?", value):
            raise ValueError("Nombre de host no válido.")
        return value


class DnsmasqConfig(BaseModel):
    """Configuración del drop-in de dnsmasq (DNS + DHCP)."""

    servers: list[str] = Field(default_factory=list, max_length=8, description="Servidores DNS upstream.")
    records: list[DnsmasqRecord] = Field(default_factory=list, max_length=100, description="Registros locales.")
    dhcp: DnsmasqDhcpRange = Field(default_factory=DnsmasqDhcpRange, description="Rango DHCP.")
    reservations: list[DnsmasqReservation] = Field(
        default_factory=list, max_length=100, description="Reservas por MAC."
    )

    @field_validator("servers")
    @classmethod
    def _validate_servers(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                ipaddress.IPv4Address(value)
            except ValueError as exc:
                raise ValueError(f"Servidor DNS no válido: {value}") from exc
        return values


class SambaConfigResponse(BaseModel):
    """Estado y modelo de la configuración de Samba."""

    managed: bool = Field(..., description="True si el fichero está gestionado por el panel.")
    exists: bool = Field(..., description="True si el fichero existe en el sistema.")
    path: str = Field(..., description="Ruta del fichero de configuración.")
    reason: str | None = Field(default=None, description="Aviso si el fichero no es interpretable.")
    config: SambaConfig = Field(..., description="Modelo de configuración efectivo.")


class DnsmasqConfigResponse(BaseModel):
    """Estado y modelo de la configuración de dnsmasq."""

    managed: bool = Field(..., description="True si el fichero está gestionado por el panel.")
    exists: bool = Field(..., description="True si el fichero existe en el sistema.")
    path: str = Field(..., description="Ruta del fichero de configuración.")
    reason: str | None = Field(default=None, description="Aviso si el fichero no es interpretable.")
    config: DnsmasqConfig = Field(..., description="Modelo de configuración efectivo.")


class SambaConfigUpdate(BaseModel):
    """Petición de guardado de la configuración de Samba."""

    config: SambaConfig = Field(..., description="Modelo a aplicar.")
    force: bool = Field(default=False, description="Forzar aunque haya avisos.")


class DnsmasqConfigUpdate(BaseModel):
    """Petición de guardado de la configuración de dnsmasq."""

    config: DnsmasqConfig = Field(..., description="Modelo a aplicar.")
    force: bool = Field(default=False, description="Forzar aunque haya otro DHCP.")


class SambaValidateRequest(BaseModel):
    """Petición de validación previa (dry-run) de Samba."""

    config: SambaConfig = Field(..., description="Modelo a validar.")


class DnsmasqValidateRequest(BaseModel):
    """Petición de validación previa (dry-run) de dnsmasq."""

    config: DnsmasqConfig = Field(..., description="Modelo a validar.")


class ConfigValidateResponse(BaseModel):
    """Resultado de la validación previa."""

    ok: bool = Field(..., description="True si el borrador es válido.")
    message: str = Field(..., description="Mensaje descriptivo.")
    preview: str = Field(..., description="Fichero exacto que se escribiría.")
    warnings: list[str] = Field(default_factory=list, description="Avisos.")


class ConfigApplyResponse(BaseModel):
    """Resultado de aplicar una configuración."""

    success: bool = Field(..., description="True si se aplicó y recargó.")
    message: str = Field(..., description="Mensaje descriptivo.")
    warnings: list[str] = Field(default_factory=list, description="Avisos del proceso.")
    preview: str = Field(default="", description="Contenido aplicado (o preparado).")


class BackupInfo(BaseModel):
    """Copia de seguridad disponible."""

    name: str = Field(..., description="Nombre del fichero de copia.")
    size: int = Field(..., description="Tamaño en bytes.")
    modified: str = Field(..., description="Fecha de modificación (ISO8601 UTC).")


class BackupListResponse(BaseModel):
    """Listado de copias de seguridad."""

    backups: list[BackupInfo] = Field(default_factory=list, description="Copias disponibles.")


class RestoreRequest(BaseModel):
    """Petición de restauración de una copia."""

    backup: str = Field(..., min_length=1, max_length=128, description="Nombre de la copia.")


class DhcpCheckResponse(BaseModel):
    """Resultado de la sonda de servidores DHCP."""

    available: bool = Field(..., description="False si nmap no está disponible.")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    servers: list[str] = Field(default_factory=list, description="Respondedores detectados.")
    ours: list[str] = Field(default_factory=list, description="Respondedores que son esta máquina.")
    foreign: list[str] = Field(default_factory=list, description="Respondedores ajenos (conflicto).")
    verdict: str = Field(..., description="ok | conflict | unknown")


# ================================ Red ================================
class NetworkPlanRequest(BaseModel):
    """Petición de plan/aplicación de cambio de IP."""

    interface: str = Field(..., min_length=1, max_length=32, description="Interfaz de red (p. ej. eth0).")
    address: str = Field(..., description="Nueva dirección IPv4.")
    prefix: int = Field(default=24, ge=8, le=30, description="Prefijo de red (8-30).")
    gateway: str = Field(..., description="Puerta de enlace IPv4.")
    dns: list[str] = Field(default_factory=list, max_length=4, description="Servidores DNS (opcional).")


class NetworkInterfaceInfo(BaseModel):
    """Interfaz de red detectada."""

    name: str
    mac: str | None = None
    addresses: list[str] = Field(default_factory=list)
    netmasks: list[str] = Field(
        default_factory=list, description="Máscaras de red en notación decimal (una por IP)."
    )
    cidrs: list[str] = Field(
        default_factory=list, description="Direcciones en notación CIDR (ip/prefijo)."
    )
    mtu: int | None = Field(default=None, description="MTU de la interfaz.")
    up: bool = False


class NetworkStateResponse(BaseModel):
    """Estado de red del sistema."""

    available: bool = Field(..., description="False si no se pudo leer el estado (sin `ip`).")
    renderer: str = Field(..., description="netplan | networkmanager | ifupdown | desconocido")
    supported: bool = Field(..., description="True si el renderer está soportado (netplan).")
    cloud_init: bool = Field(..., description="True si se detectó cloud-init.")
    interfaces: list[NetworkInterfaceInfo] = Field(default_factory=list)
    gateway: str | None = None
    default_interface: str | None = None
    dropin: str = Field(..., description="Ruta del fichero que gestiona el panel.")
    dropin_exists: bool = Field(..., description="True si el drop-in ya existe.")


class NetworkPlanResponse(BaseModel):
    """Previsualización de un cambio de IP."""

    ok: bool
    message: str
    warnings: list[str] = Field(default_factory=list)
    preview: str = Field(..., description="Fichero netplan que se escribiría.")
    ttl: int = Field(..., description="Segundos para confirmar antes de la reversión.")


class NetworkApplyResponse(BaseModel):
    """Resultado de aplicar un cambio de IP."""

    success: bool
    message: str
    warnings: list[str] = Field(default_factory=list)
    preview: str = ""
    token: str | None = Field(default=None, description="Token para confirmar/revertir.")
    ttl: int = Field(..., description="Segundos para confirmar antes de la reversión.")


class NetworkTokenRequest(BaseModel):
    """Token de un cambio de red pendiente."""

    token: str = Field(..., min_length=12, max_length=12, description="Token hex de 12 caracteres.")


class NetworkTokenResponse(BaseModel):
    """Resultado de confirmar o revertir un cambio de red."""

    success: bool
    message: str


# ============================ Acciones de la IA ============================
class PendingActionInfo(BaseModel):
    """Acción propuesta por el agente de IA pendiente de confirmación humana."""

    id: str = Field(..., description="Identificador de la acción pendiente.")
    tool: str = Field(..., description="Herramienta que la propuso.")
    args: dict[str, Any] = Field(
        default_factory=dict, description="Argumentos exactos que se ejecutarán al confirmar."
    )
    summary: str = Field(..., description="Descripción legible de lo que hará.")
    created_at: str = Field(..., description="Momento de creación (ISO8601 UTC).")
    expires_at: str = Field(..., description="Caducidad (ISO8601 UTC).")
    status: str = Field(default="pending", description="pending | confirmed | cancelled | expired")


# =========================== Reglas de alerta ===========================
MetricName = Literal["cpu", "ram", "swap", "disk", "load1", "temp"]

#: Acciones de autorremediación que una regla puede proponer al abrirse un evento.
#: Ninguna se ejecuta automáticamente: todas pasan por el aviso de confirmación
#: humana del panel (mismo mecanismo que las propuestas del agente de IA).
RemediationAction = Literal["docker_prune_images", "container_restart", "service_restart"]


class AlertRuleCreate(BaseModel):
    """Petición de creación de una regla de alerta."""

    name: str = Field(
        ..., min_length=1, max_length=80, description="Nombre de la regla (visible en el panel)."
    )
    metric: MetricName = Field(..., description="Métrica evaluada por la regla.")
    op: Literal[">", "<"] = Field(
        ..., description="Operador de comparación: '>' supera el umbral, '<' queda por debajo."
    )
    threshold: float = Field(..., description="Valor a partir del cual hay alerta.")
    clear_threshold: float | None = Field(
        default=None,
        description=(
            "Umbral de recuperación (histéresis). Si se omite, la alerta se cierra "
            "al volver por debajo de 'threshold' y se considera crítica."
        ),
    )
    duration_s: int = Field(
        default=0, ge=0, le=86400, description="Segundos de incumplimiento continuo antes de alertar."
    )
    enabled: bool = Field(default=True, description="Regla activa en el motor.")
    host_id: int | None = Field(
        default=None,
        description=(
            "Host al que se aplica la regla. Si se omite (null) es una regla "
            "global: se evalúa en todos los hosts de la flota."
        ),
        examples=[1],
    )
    remediation_action: RemediationAction | None = Field(
        default=None,
        description=(
            "Acción que se propone (nunca se ejecuta sola) al abrirse un evento: "
            "docker_prune_images | container_restart | service_restart."
        ),
    )

    @model_validator(mode="after")
    def _check_hysteresis(self) -> "AlertRuleCreate":
        """La banda de histéresis debe quedar del lado seguro del umbral.

        Con ``op = '>'`` la recuperación ocurre al bajar de ``clear_threshold``:
        si este fuese mayor o igual que ``threshold`` el evento cerraría nada
        más abrirse (flapping). Con ``op = '<'`` el caso simétrico.
        """
        if self.clear_threshold is None:
            return self
        if self.op == ">" and self.clear_threshold >= self.threshold:
            raise ValueError(
                "Con op '>' el clear_threshold debe ser menor que el threshold."
            )
        if self.op == "<" and self.clear_threshold <= self.threshold:
            raise ValueError(
                "Con op '<' el clear_threshold debe ser mayor que el threshold."
            )
        return self


class AlertRuleUpdate(BaseModel):
    """Cambios parciales de una regla de alerta (PATCH)."""

    name: str | None = Field(default=None, min_length=1, max_length=80, description="Nuevo nombre.")
    metric: MetricName | None = Field(default=None, description="Nueva métrica.")
    op: Literal[">", "<"] | None = Field(default=None, description="Nuevo operador.")
    threshold: float | None = Field(default=None, description="Nuevo umbral.")
    clear_threshold: float | None = Field(
        default=None, description="Nuevo umbral de recuperación; null lo elimina."
    )
    duration_s: int | None = Field(
        default=None, ge=0, le=86400, description="Nueva duración mínima en segundos."
    )
    enabled: bool | None = Field(default=None, description="Activar o desactivar la regla.")
    host_id: int | None = Field(
        default=None,
        description="Nuevo host de ámbito; null devuelve la regla al ámbito global.",
    )
    remediation_action: RemediationAction | None = Field(
        default=None,
        description="Acción de autorremediación propuesta; null la desactiva.",
    )


class AlertRule(BaseModel):
    """Regla de alerta tal y como se guarda en la base de datos."""

    id: int = Field(..., description="Identificador de la regla.")
    name: str = Field(..., description="Nombre de la regla.")
    metric: str = Field(..., description="Métrica evaluada.")
    op: str = Field(..., description="Operador ('>' o '<').")
    threshold: float = Field(..., description="Umbral de alarma.")
    clear_threshold: float | None = Field(
        default=None, description="Umbral de recuperación (histéresis) si lo hay."
    )
    duration_s: int = Field(default=0, description="Incumplimiento continuo exigido (s).")
    enabled: int = Field(default=1, description="1 = activa, 0 = desactivada.")
    host_id: int | None = Field(
        default=None,
        description="Host de ámbito de la regla; null = global (todos los hosts).",
    )
    remediation_action: str | None = Field(
        default=None,
        description="Acción propuesta al abrirse un evento (null = sin remediación).",
    )
    created_at: str | None = Field(default=None, description="Creación (ISO8601 UTC).")
    updated_at: str | None = Field(default=None, description="Última modificación (ISO8601 UTC).")


class AlertRuleListResponse(BaseModel):
    """Listado de reglas de alerta."""

    count: int = Field(..., description="Número de reglas devueltas.")
    rules: list[AlertRule] = Field(default_factory=list, description="Reglas de alerta.")


# =========================== Eventos de alerta ==========================
class AlertEvent(BaseModel):
    """Evento de alerta (una incidencia abierta, reconocida o ya cerrada)."""

    id: int = Field(..., description="Identificador del evento.")
    rule_id: int | None = Field(default=None, description="Regla que lo originó.")
    host_id: int | None = Field(
        default=None,
        description=(
            "Host donde se produjo la incidencia. null = anterior a la flota "
            "(se considera el host local)."
        ),
    )
    metric: str | None = Field(default=None, description="Métrica afectada.")
    status: str = Field(..., description="Estado: open | acked | closed.")
    severity: str = Field(..., description="Severidad: warn | crit.")
    value: float | None = Field(default=None, description="Valor medido al abrirse.")
    threshold: float | None = Field(default=None, description="Umbral de la regla.")
    opened_at: str | None = Field(default=None, description="Apertura (ISO8601 UTC).")
    closed_at: str | None = Field(default=None, description="Recuperación (ISO8601 UTC).")
    acked_at: str | None = Field(default=None, description="Reconocimiento (ISO8601 UTC).")
    acked_by: str | None = Field(default=None, description="Usuario que lo reconoció.")
    snooze_until: str | None = Field(default=None, description="Notificaciones aplazadas hasta (ISO8601 UTC).")
    suppressed: int = Field(default=0, description="1 = registrado en ventana de mantenimiento.")
    message: str | None = Field(default=None, description="Descripción legible del evento.")


class AlertEventListResponse(BaseModel):
    """Listado de eventos de alerta."""

    count: int = Field(..., description="Número de eventos devueltos.")
    events: list[AlertEvent] = Field(default_factory=list, description="Eventos, más recientes primero.")


class AlertSnoozeRequest(BaseModel):
    """Petición de aplazamiento de notificaciones de un evento."""

    minutes: int = Field(
        ..., ge=1, le=10080, description="Minutos de silencio (1 minuto a 7 días)."
    )


class ActionResponse(BaseModel):
    """Respuesta genérica de una acción (éxito o no + mensaje)."""

    success: bool = Field(..., description="True si la acción se completó.")
    message: str = Field(..., description="Mensaje descriptivo del resultado.")


# ========================= Canales de notificación =======================
class NotificationChannelCreate(BaseModel):
    """Petición de alta de un canal de notificación."""

    name: str = Field(..., min_length=1, max_length=80, description="Nombre del canal.")
    kind: Literal["telegram", "webhook", "smtp"] = Field(..., description="Tipo de canal.")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Configuración según el tipo: telegram {token, chat_id}; "
            "webhook {url, authorization?, headers?}; smtp {host, port, user?, "
            "password?, from_addr, to}. Los secretos nunca se devuelven."
        ),
    )
    enabled: bool = Field(default=True, description="Canal activo para el envío.")


class NotificationChannelUpdate(BaseModel):
    """Cambios parciales de un canal de notificación (PATCH)."""

    name: str | None = Field(default=None, min_length=1, max_length=80, description="Nuevo nombre.")
    kind: Literal["telegram", "webhook", "smtp"] | None = Field(default=None, description="Nuevo tipo.")
    config: dict[str, Any] | None = Field(
        default=None, description="Configuración completa (sustituye a la anterior)."
    )
    enabled: bool | None = Field(default=None, description="Activar o desactivar el canal.")


class NotificationChannel(BaseModel):
    """Canal de notificación (configuración con secretos enmascarados)."""

    id: int = Field(..., description="Identificador del canal.")
    name: str = Field(..., description="Nombre del canal.")
    kind: str = Field(..., description="Tipo: telegram | webhook | smtp.")
    config: dict[str, Any] = Field(
        default_factory=dict, description="Configuración con token/contraseña enmascarados."
    )
    enabled: int = Field(default=1, description="1 = habilitado, 0 = deshabilitado.")
    last_ok_at: str | None = Field(default=None, description="Último envío correcto (ISO8601 UTC).")
    last_error: str | None = Field(default=None, description="Último error de envío.")
    created_at: str | None = Field(default=None, description="Creación (ISO8601 UTC).")
    updated_at: str | None = Field(default=None, description="Última modificación (ISO8601 UTC).")


class NotificationChannelListResponse(BaseModel):
    """Listado de canales de notificación."""

    count: int = Field(..., description="Número de canales devueltos.")
    channels: list[NotificationChannel] = Field(
        default_factory=list, description="Canales configurados."
    )


class ChannelTestResponse(BaseModel):
    """Resultado de la prueba de envío de un canal."""

    ok: bool = Field(..., description="True si el canal aceptó el mensaje.")
    detail: str = Field(..., description="Detalle del intento (o del error).")
    kind: str = Field(default="", description="Tipo de canal probado.")
    name: str = Field(default="", description="Nombre del canal probado.")


# ========================= Ventanas de mantenimiento =====================
class MaintenanceWindowCreate(BaseModel):
    """Petición de alta de una ventana de mantenimiento."""

    name: str = Field(..., min_length=1, max_length=80, description="Motivo (p. ej. «Actualización del SO»).")
    starts_at: str = Field(..., description="Inicio de la ventana (ISO8601 UTC).")
    ends_at: str = Field(..., description="Fin de la ventana (ISO8601 UTC).")
    enabled: bool = Field(default=True, description="Ventana activa.")

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _validate_iso(cls, value: str) -> str:
        """Exige una marca ISO-8601 (el panel la genera con toISOString())."""
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Fecha no válida (ISO-8601): {value}") from exc
        return value

    @model_validator(mode="after")
    def _check_order(self) -> "MaintenanceWindowCreate":
        """La ventana debe terminar después de empezar."""
        if datetime.fromisoformat(self.ends_at) <= datetime.fromisoformat(self.starts_at):
            raise ValueError("ends_at debe ser posterior a starts_at.")
        return self


class MaintenanceWindowUpdate(BaseModel):
    """Cambios parciales de una ventana de mantenimiento (PATCH)."""

    name: str | None = Field(default=None, min_length=1, max_length=80, description="Nuevo motivo.")
    starts_at: str | None = Field(default=None, description="Nuevo inicio (ISO8601 UTC).")
    ends_at: str | None = Field(default=None, description="Nuevo fin (ISO8601 UTC).")
    enabled: bool | None = Field(default=None, description="Activar o desactivar la ventana.")


class MaintenanceWindow(BaseModel):
    """Ventana de mantenimiento (durante ella no se notifica)."""

    id: int = Field(..., description="Identificador de la ventana.")
    name: str = Field(..., description="Motivo de la ventana.")
    starts_at: str = Field(..., description="Inicio (ISO8601 UTC).")
    ends_at: str = Field(..., description="Fin (ISO8601 UTC).")
    enabled: int = Field(default=1, description="1 = activa, 0 = desactivada.")
    created_at: str | None = Field(default=None, description="Creación (ISO8601 UTC).")


class MaintenanceWindowListResponse(BaseModel):
    """Listado de ventanas de mantenimiento."""

    count: int = Field(..., description="Número de ventanas devueltas.")
    windows: list[MaintenanceWindow] = Field(
        default_factory=list, description="Ventanas configuradas."
    )


# ============================ Histórico de métricas ======================
class MetricPoint(BaseModel):
    """Punto de una serie de métricas."""

    ts: int = Field(..., description="Marca temporal Unix (segundos UTC).")
    value: float | None = Field(default=None, description="Valor de la métrica; None si no hay sensor.")


class MetricsHistoryResponse(BaseModel):
    """Histórico downsampled de las métricas pedidas."""

    range: str = Field(..., description="Rango consultado: 1h | 24h | 7d | 30d.")
    period: str = Field(..., description="Nivel de agregación usado: 1m | 5m | 1h.")
    metrics: list[str] = Field(..., description="Métricas devueltas.")
    points: int = Field(..., description="Número de puntos por serie (máx. 1000).")
    from_ts: int = Field(..., description="Inicio de la ventana (Unix).")
    to_ts: int = Field(..., description="Fin de la ventana (Unix).")
    host_id: int = Field(
        default=1, description="Host al que pertenece el histórico (1 = este servidor)."
    )
    series: dict[str, list[MetricPoint]] = Field(
        default_factory=dict, description="Serie {métrica: [{ts, value}]}."
    )


# ========================= Monitores de disponibilidad ====================
MonitorKind = Literal["http", "tcp", "ssl"]
MonitorState = Literal["up", "down", "unknown"]
OverallState = Literal["up", "degraded", "down"]


class MonitorCreate(BaseModel):
    """Petición de alta de un monitor de disponibilidad."""

    name: str = Field(
        ..., min_length=1, max_length=80, description="Nombre del monitor (visible en el panel)."
    )
    kind: MonitorKind = Field(
        ..., description="Tipo de comprobación: http | tcp | ssl."
    )
    target: str = Field(
        ...,
        min_length=1,
        max_length=300,
        description=(
            "Destino: URL http(s) completa para 'http'; 'host' o 'host:puerto' "
            "para 'tcp' y 'ssl'. Apunta libremente a la red interna (LAN o "
            "localhost): el panel vigila sus propios servicios."
        ),
    )
    port: int | None = Field(
        default=None,
        ge=1,
        le=65535,
        description=(
            "Puerto explícito. Obligatorio en 'tcp' si el destino no lo incluye; "
            "en 'ssl' vale 443 por defecto."
        ),
    )
    interval_s: int = Field(
        default=60,
        ge=MIN_INTERVAL_S,
        le=MAX_INTERVAL_S,
        description="Segundos entre comprobaciones (mínimo 10).",
    )
    timeout_s: int = Field(
        default=5,
        ge=MIN_TIMEOUT_S,
        le=MAX_TIMEOUT_S,
        description="Timeout de la comprobación en segundos (1-30).",
    )
    expected_status: int | None = Field(
        default=None,
        ge=100,
        le=599,
        description=(
            "Código HTTP esperado (solo 'http'). Si se omite, se considera "
            "arriba cualquier 2xx o 3xx."
        ),
    )
    keyword: str | None = Field(
        default=None,
        max_length=200,
        description="Palabra clave que debe aparecer en el cuerpo (solo 'http').",
    )
    enabled: bool = Field(default=True, description="Monitor activo en el comprobador.")

    @model_validator(mode="after")
    def _check_target(self) -> "MonitorCreate":
        """Valida el destino contra el tipo.

        Se convierte a ``ValueError`` para que Pydantic lo devuelva como un 422
        con el motivo en español, igual que el resto de restricciones del modelo
        (el mensaje sale de :func:`app.modules.monitors.parse_target`).
        """
        try:
            parse_target(self.kind, self.target, self.port)
        except TargetError as exc:
            raise ValueError(str(exc)) from exc
        return self


class MonitorUpdate(BaseModel):
    """Cambios parciales de un monitor de disponibilidad (PATCH)."""

    name: str | None = Field(default=None, min_length=1, max_length=80, description="Nuevo nombre.")
    kind: MonitorKind | None = Field(default=None, description="Nuevo tipo de comprobación.")
    target: str | None = Field(default=None, min_length=1, max_length=300, description="Nuevo destino.")
    port: int | None = Field(default=None, ge=1, le=65535, description="Nuevo puerto.")
    interval_s: int | None = Field(
        default=None, ge=MIN_INTERVAL_S, le=MAX_INTERVAL_S, description="Nuevo intervalo."
    )
    timeout_s: int | None = Field(
        default=None, ge=MIN_TIMEOUT_S, le=MAX_TIMEOUT_S, description="Nuevo timeout."
    )
    expected_status: int | None = Field(default=None, ge=100, le=599, description="Nuevo código HTTP esperado.")
    keyword: str | None = Field(default=None, max_length=200, description="Nueva palabra clave.")
    enabled: bool | None = Field(default=None, description="Activar o desactivar el monitor.")


class Monitor(BaseModel):
    """Monitor de disponibilidad con su estado actual y sus porcentajes."""

    id: int = Field(..., description="Identificador del monitor.")
    name: str = Field(..., description="Nombre del monitor.")
    kind: str = Field(..., description="Tipo de comprobación: http | tcp | ssl.")
    target: str = Field(..., description="Destino vigilado (visible solo en la API privada).")
    port: int | None = Field(default=None, description="Puerto vigilado, si lo hay.")
    interval_s: int = Field(default=60, description="Segundos entre comprobaciones.")
    timeout_s: int = Field(default=5, description="Timeout de la comprobación (s).")
    expected_status: int | None = Field(default=None, description="Código HTTP esperado, si se fijó.")
    keyword: str | None = Field(default=None, description="Palabra clave exigida en el cuerpo.")
    enabled: int = Field(default=1, description="1 = habilitado, 0 = desactivado.")
    created_at: str | None = Field(default=None, description="Creación (ISO8601 UTC).")
    updated_at: str | None = Field(default=None, description="Última modificación (ISO8601 UTC).")
    status: str = Field(
        default="unknown", description="Estado del último heartbeat: up | down | unknown."
    )
    latency_ms: float | None = Field(default=None, description="Latencia del último heartbeat (ms).")
    last_check: str | None = Field(default=None, description="Última comprobación (ISO8601 UTC).")
    last_error: str | None = Field(default=None, description="Error de la última comprobación.")
    uptime_24h: float | None = Field(
        default=None, description="Disponibilidad en 24 h (%); None si no hay datos."
    )
    uptime_7d: float | None = Field(default=None, description="Disponibilidad en 7 días (%).")
    uptime_30d: float | None = Field(default=None, description="Disponibilidad en 30 días (%).")
    ssl_days_left: int | None = Field(
        default=None, description="Días para caducar el certificado (solo tipo ssl)."
    )


class MonitorListResponse(BaseModel):
    """Listado de monitores con su estado."""

    count: int = Field(..., description="Número de monitores devueltos.")
    monitors: list[Monitor] = Field(default_factory=list, description="Monitores definidos.")


class Heartbeat(BaseModel):
    """Resultado de una comprobación (una fila de ``heartbeats``)."""

    id: int = Field(..., description="Identificador del heartbeat.")
    monitor_id: int = Field(..., description="Monitor al que pertenece.")
    ts: int = Field(..., description="Marca temporal Unix (segundos UTC).")
    iso: str = Field(default="", description="Marca temporal legible (ISO8601 UTC).")
    up: int = Field(..., description="1 = la comprobación pasó, 0 = falló.")
    latency_ms: float | None = Field(default=None, description="Latencia de la comprobación (ms).")
    status_code: int | None = Field(default=None, description="Código HTTP obtenido (solo http).")
    detail: str | None = Field(default=None, description="Detalle legible del resultado.")
    error: str | None = Field(default=None, description="Error si la comprobación falló.")


class HeartbeatListResponse(BaseModel):
    """Histórico reciente de un monitor (para la gráfica de barras de estado)."""

    monitor_id: int = Field(..., description="Monitor consultado.")
    hours: int = Field(..., description="Ventana solicitada, en horas.")
    count: int = Field(..., description="Número de heartbeats devueltos (máx. 500).")
    points: list[Heartbeat] = Field(
        default_factory=list, description="Heartbeats, del más antiguo al más reciente."
    )


class PublicMonitorStatus(BaseModel):
    """Monitor en el estado público (sin datos del destino)."""

    name: str = Field(..., description="Nombre elegido por el administrador.")
    kind: str = Field(..., description="Tipo de comprobación: http | tcp | ssl.")
    status: MonitorState = Field(..., description="Estado: up | down | unknown.")
    latency_ms: float | None = Field(default=None, description="Latencia del último heartbeat (ms).")
    last_check: str | None = Field(default=None, description="Última comprobación (ISO8601 UTC).")
    uptime_24h: float | None = Field(default=None, description="Disponibilidad en 24 h (%).")
    uptime_7d: float | None = Field(default=None, description="Disponibilidad en 7 días (%).")


class PublicStatusResponse(BaseModel):
    """Estado público del servicio (sin autenticación).

    Nunca incluye destinos: ni URL, ni host, ni puerto, ni mensajes de error.
    """

    overall: OverallState = Field(
        ..., description="Estado global: up | degraded | down."
    )
    monitors: list[PublicMonitorStatus] = Field(
        default_factory=list, description="Estado de cada monitor."
    )
    updated_at: str = Field(..., description="Momento de generación del estado (ISO8601 UTC).")


# ============================== Auditoría ===============================
class AuditEntry(BaseModel):
    """Entrada del registro de auditoría encadenado."""

    ts: str = Field(..., description="Momento del evento (ISO8601 UTC).")
    seq: int = Field(default=0, description="Número de secuencia en la cadena.")
    actor: str = Field(default="system", description="Usuario que provocó el evento.")
    action: str = Field(..., description="Acción registrada (p. ej. 'auth.login').")
    target: str = Field(default="", description="Recurso afectado.")
    result: str = Field(default="ok", description="ok | denied | fail | throttled.")
    details: dict[str, Any] = Field(default_factory=dict, description="Contexto (sin secretos).")
    prev_hash: str = Field(default="", description="Hash de la entrada anterior.")
    hash: str = Field(default="", description="Hash de esta entrada (cadena de integridad).")


class AuditChainState(BaseModel):
    """Resultado de verificar la cadena de hashes del registro."""

    valid: bool = Field(..., description="True si la cadena está intacta.")
    events: int = Field(default=0, description="Entradas verificadas.")
    error: str | None = Field(default=None, description="Motivo si la cadena no es válida.")


class AuditListResponse(BaseModel):
    """Página del registro de auditoría."""

    count: int = Field(..., description="Entradas devueltas en esta página.")
    total: int = Field(..., description="Entradas que cumplen el filtro.")
    limit: int = Field(..., description="Tamaño de página solicitado.")
    offset: int = Field(..., description="Desplazamiento aplicado.")
    events: list[AuditEntry] = Field(default_factory=list, description="Entradas, más recientes primero.")
    chain: AuditChainState = Field(..., description="Estado de la cadena de hashes.")


# ============================== Tokens de API ============================
class ApiTokenCreate(BaseModel):
    """Petición de alta de un token de API."""

    name: str = Field(
        ..., min_length=1, max_length=80, description="Nombre del token para reconocerlo en el panel."
    )
    scope: Literal["read", "write"] = Field(
        default="read",
        description="'read' solo consulta; 'write' además permite métodos mutantes.",
    )
    ttl_days: int | None = Field(
        default=90, ge=1, le=3650, description="Días de validez; null o 0 = sin caducidad."
    )


class ApiToken(BaseModel):
    """Metadatos de un token (nunca el secreto)."""

    id: int = Field(..., description="Identificador del token.")
    name: str = Field(..., description="Nombre del token.")
    prefix: str = Field(..., description="Primeros caracteres del token, para reconocerlo.")
    scope: str = Field(..., description="Ámbito: read | write.")
    created_at: str | None = Field(default=None, description="Creación (ISO8601 UTC).")
    last_used_at: str | None = Field(default=None, description="Último uso (ISO8601 UTC).")
    expires_at: str | None = Field(default=None, description="Caducidad (ISO8601 UTC).")
    revoked_at: str | None = Field(default=None, description="Revocación (ISO8601 UTC).")
    active: bool = Field(..., description="True si el token todavía autentica.")


class ApiTokenCreated(ApiToken):
    """Respuesta del alta: incluye el secreto, mostrado una única vez."""

    token: str = Field(
        ...,
        description="Token en claro (nx_...). Solo se devuelve aquí: no se puede recuperar.",
    )


class ApiTokenListResponse(BaseModel):
    """Listado de tokens de API."""

    count: int = Field(..., description="Número de tokens devueltos.")
    tokens: list[ApiToken] = Field(default_factory=list, description="Tokens existentes.")


# ============================== Sesiones ================================
class SecuritySession(BaseModel):
    """Sesión abierta del panel."""

    id: str = Field(
        ..., description="Identificador de la sesión (hash del token); no es una credencial."
    )
    username: str = Field(..., description="Usuario de la sesión.")
    created_at: float = Field(..., description="Creación (Unix, segundos).")
    last_seen: float = Field(..., description="Último uso (Unix, segundos).")
    expires_at: float = Field(..., description="Caducidad absoluta (Unix, segundos).")
    idle_seconds: float = Field(..., description="Segundos desde el último uso.")
    idle_limit_seconds: float = Field(..., description="Límite de inactividad configurado (s).")
    expired: bool = Field(..., description="True si ya no sería aceptada.")
    is_current: bool = Field(..., description="True si es la sesión que hace la consulta.")


class SecuritySessionListResponse(BaseModel):
    """Listado de sesiones abiertas."""

    count: int = Field(..., description="Número de sesiones devueltas.")
    sessions: list[SecuritySession] = Field(default_factory=list, description="Sesiones.")


# ================================ 2FA ===================================
class TwoFactorStatusResponse(BaseModel):
    """Estado del segundo factor de la cuenta."""

    enabled: bool = Field(..., description="True si el segundo factor está activo.")
    configured: bool = Field(..., description="True si hay un secreto guardado (setup pendiente o activo).")
    recovery_left: int = Field(..., description="Códigos de recuperación disponibles.")


class TwoFactorSetupResponse(BaseModel):
    """Resultado de iniciar el alta del segundo factor."""

    secret: str = Field(..., description="Secreto TOTP en base32 para introducir a mano.")
    otpauth_url: str = Field(..., description="URL otpauth:// para escanear como QR.")
    recovery_codes: list[str] = Field(
        ..., description="Códigos de recuperación de un solo uso (se muestran una única vez)."
    )


class TwoFactorEnableRequest(BaseModel):
    """Confirmación del segundo factor con un código TOTP válido."""

    code: str = Field(..., min_length=6, max_length=6, description="Código de 6 dígitos de la app.")


class TwoFactorDisableRequest(BaseModel):
    """Desactivación del segundo factor (exige la contraseña)."""

    password: str = Field(..., min_length=1, max_length=256, description="Contraseña actual.")


# ========================== Herramientas del sistema ====================
class SmartDisk(BaseModel):
    """Salud de un disco (SMART)."""

    device: str = Field(..., description="Dispositivo (p. ej. /dev/sda).")
    model: str = Field(default="", description="Modelo del disco.")
    health: str | None = Field(default=None, description="Resultado del test de salud (PASSED/FAILED).")
    temperature: float | None = Field(default=None, description="Temperatura en °C.")
    wear: float | None = Field(
        default=None, description="Desgaste de la memoria en % (0 = nueva, 100 = agotada)."
    )
    reallocated_sectors: int | None = Field(
        default=None, description="Sectores reasignados (0 = sano)."
    )
    source: str = Field(default="json", description="Origen del dato: json | text.")


class SmartResponse(BaseModel):
    """Informe SMART de los discos del sistema."""

    available: bool = Field(..., description="False si smartctl no está instalado o no es usable.")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    count: int = Field(..., description="Número de discos con datos.")
    disks: list[SmartDisk] = Field(default_factory=list, description="Discos inspeccionados.")


class ListeningPort(BaseModel):
    """Socket en escucha."""

    family: str = Field(..., description="Familia: inet (IPv4) | inet6 (IPv6).")
    ip: str = Field(..., description="Dirección local en escucha.")
    port: int = Field(..., description="Puerto local.")
    status: str = Field(..., description="Estado del socket (LISTEN, ESTABLISHED...).")
    pid: int | None = Field(default=None, description="PID del proceso propietario.")
    process: str | None = Field(default=None, description="Nombre del proceso (requiere permisos).")


class PortsResponse(BaseModel):
    """Puertos en escucha del sistema."""

    available: bool = Field(..., description="False si no se pudo leer la tabla de sockets.")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    partial: bool = Field(default=False, description="True si faltaron permisos para algún PID.")
    count: int = Field(..., description="Número de sockets devueltos.")
    ports: list[ListeningPort] = Field(default_factory=list, description="Sockets en escucha.")


class PendingUpdate(BaseModel):
    """Paquete que se puede actualizar."""

    package: str = Field(..., description="Nombre del paquete.")
    version: str = Field(default="", description="Versión destino.")


class UpdatesResponse(BaseModel):
    """Actualizaciones pendientes del sistema."""

    available: bool = Field(..., description="False si apt-get no está disponible (no es Debian).")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    count: int = Field(..., description="Número de paquetes actualizables.")
    packages: list[PendingUpdate] = Field(default_factory=list, description="Paquetes pendientes.")


# ============================== Docker extra ============================
class DockerVolume(BaseModel):
    """Volumen Docker."""

    name: str = Field(..., description="Nombre del volumen.")
    driver: str = Field(default="", description="Driver del volumen.")
    mountpoint: str = Field(default="", description="Punto de montaje en el host.")
    scope: str = Field(default="", description="Ámbito del volumen.")


class DockerNetwork(BaseModel):
    """Red Docker."""

    name: str = Field(..., description="Nombre de la red.")
    driver: str = Field(default="", description="Driver de la red.")
    scope: str = Field(default="", description="Ámbito: local | swarm.")
    internal: bool = Field(default=False, description="True si la red no tiene salida a internet.")


class DockerImage(BaseModel):
    """Imagen Docker local."""

    id: str = Field(..., description="Identificador de la imagen.")
    short_id: str = Field(..., description="Identificador corto (12 caracteres).")
    tags: list[str] = Field(default_factory=list, description="Tags de la imagen.")
    size_bytes: int = Field(default=0, description="Tamaño en bytes.")
    created: str | None = Field(default=None, description="Creación (ISO8601).")


class DockerVolumeListResponse(BaseModel):
    """Listado de volúmenes Docker."""

    available: bool = Field(..., description="False si el daemon de Docker no responde.")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    count: int = Field(default=0, description="Número de volúmenes devueltos.")
    volumes: list[DockerVolume] = Field(default_factory=list, description="Volúmenes.")


class DockerNetworkListResponse(BaseModel):
    """Listado de redes Docker."""

    available: bool = Field(..., description="False si el daemon de Docker no responde.")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    count: int = Field(default=0, description="Número de redes devueltas.")
    networks: list[DockerNetwork] = Field(default_factory=list, description="Redes.")


class DockerImageListResponse(BaseModel):
    """Listado de imágenes Docker."""

    available: bool = Field(..., description="False si el daemon de Docker no responde.")
    reason: str | None = Field(default=None, description="Motivo si no está disponible.")
    count: int = Field(default=0, description="Número de imágenes devueltas.")
    images: list[DockerImage] = Field(default_factory=list, description="Imágenes locales.")


class ContainerLogsResponse(BaseModel):
    """Últimas líneas del log de un contenedor."""

    success: bool = Field(..., description="True si se pudo leer el log.")
    container_id: str = Field(..., description="Contenedor consultado.")
    tail: int = Field(default=200, description="Líneas solicitadas.")
    count: int = Field(..., description="Líneas devueltas.")
    lines: list[str] = Field(default_factory=list, description="Líneas del log.")


class ContainerStatsResponse(BaseModel):
    """Muestra puntual del consumo de un contenedor."""

    success: bool = Field(..., description="True si se pudieron obtener las estadísticas.")
    container_id: str = Field(..., description="Contenedor consultado.")
    stats: dict[str, Any] = Field(
        default_factory=dict, description="Porcentajes de CPU y memoria (líneas de /proc)."
    )


class PruneRequest(BaseModel):
    """Petición de limpieza de Docker (destructiva: exige confirmación)."""

    images: bool = Field(default=False, description="Eliminar imágenes sin usar.")
    volumes: bool = Field(default=False, description="Eliminar volúmenes sin usar.")
    containers: bool = Field(default=False, description="Eliminar contenedores parados.")
    confirm: bool = Field(
        default=False,
        description=(
            "Confirmación explícita. Sin ella la API responde 400: la limpieza "
            "borra datos de forma irreversible."
        ),
    )


# ========================= Integridad de ficheros =======================
FimState = Literal["sin_baseline", "ok", "modificado", "ausente"]


class FimFileStatus(BaseModel):
    """Estado de un fichero vigilado por el FIM."""

    path: str = Field(..., description="Ruta vigilada (siempre de la lista permitida).")
    label: str = Field(..., description="Nombre lógico del fichero (samba, dnsmasq, netplan, ai_env).")
    state: FimState = Field(..., description="Estado: sin_baseline | ok | modificado | ausente.")
    exists: bool = Field(..., description="True si el fichero existe ahora mismo.")
    baseline_sha256: str | None = Field(default=None, description="SHA-256 de la línea base.")
    current_sha256: str | None = Field(default=None, description="SHA-256 del contenido actual.")
    baseline_updated_at: str | None = Field(default=None, description="Fecha de la línea base (ISO8601).")
    diff: str = Field(default="", description="Diff unificado (solo si el fichero cambió).")
    missing_lines: int = Field(default=0, description="Líneas borradas respecto a la línea base.")
    extra_lines: int = Field(default=0, description="Líneas añadidas respecto a la línea base.")
    diff_truncated: bool = Field(default=False, description="True si el diff se recortó por tamaño.")


class FimStatusResponse(BaseModel):
    """Estado del FIM para todos los ficheros vigilados."""

    count: int = Field(..., description="Número de ficheros vigilados.")
    changed: int = Field(..., description="Número de ficheros modificados o ausentes.")
    files: list[FimFileStatus] = Field(default_factory=list, description="Estado por fichero.")


class FimBaselineRequest(BaseModel):
    """Petición para (re)generar la línea base del FIM."""

    paths: list[str] | None = Field(
        default=None,
        description=(
            "Ficheros a incluir; si se omite, se actualizan todos. Solo se "
            "admiten rutas de la lista vigilada."
        ),
    )


class FimBaselineResponse(BaseModel):
    """Resultado de actualizar la línea base."""

    count: int = Field(..., description="Número de líneas base escritas.")
    paths: list[str] = Field(default_factory=list, description="Rutas actualizadas.")


# ============================ Autorremediación ==========================
class RemediationProposal(BaseModel):
    """Propuesta de autorremediación pendiente de confirmación humana."""

    id: str = Field(..., description="Identificador de la propuesta.")
    rule_id: int = Field(..., description="Regla de alerta que la propuso.")
    rule_name: str = Field(default="", description="Nombre de la regla.")
    action: str = Field(..., description="Acción propuesta (herramienta del agente).")
    summary: str = Field(..., description="Descripción legible de lo que haría.")
    event_id: int | None = Field(default=None, description="Evento de alerta que la disparó.")
    args: dict[str, Any] = Field(default_factory=dict, description="Argumentos de la acción.")
    status: str = Field(default="pending", description="pending | confirmed | cancelled | expired.")
    created_at: str = Field(..., description="Creación (ISO8601 UTC).")
    expires_at: str = Field(..., description="Caducidad (ISO8601 UTC).")


class RemediationListResponse(BaseModel):
    """Listado de propuestas de remediación."""

    count: int = Field(..., description="Número de propuestas devueltas.")
    proposals: list[RemediationProposal] = Field(
        default_factory=list, description="Propuestas pendientes de confirmación."
    )

# ======================= Flota de hosts (multi-host) =====================
# Contratos del protocolo del agente y de la API de flota. El hub es el
# servidor: los agentes remotos se conectan a él (salida, sin puertos abiertos
# en el agente) y reportan con su propio token.


class AgentEnrollRequest(BaseModel):
    """Petición de enrolamiento de un agente en el hub."""

    token: str = Field(
        ..., min_length=8, max_length=200, description="Token de enrolamiento del hub."
    )
    name: str = Field(
        default="", max_length=120, description="Nombre del host (si se omite, se deduce del SO)."
    )
    hostname: str = Field(default="", max_length=120, description="Hostname del agente.")
    os: str = Field(default="", max_length=120, description="Sistema operativo del agente.")
    agent_version: str = Field(default="", max_length=40, description="Versión del agente.")


class AgentEnrollResponse(BaseModel):
    """Credenciales devueltas al enrolar un agente.

    ``token`` es el secreto del agente y **solo existe en esta respuesta**: el
    hub guarda su SHA-256 y un prefijo. Si se pierde, hay que dar de alta el host
    de nuevo.
    """

    host_id: int = Field(..., description="Identificador asignado al host.")
    token: str = Field(..., description="Token del agente (secreto, se muestra una sola vez).")
    name: str = Field(default="", description="Nombre registrado del host.")
    hostname: str | None = Field(default=None, description="Hostname informado por el agente.")
    poll_seconds: int = Field(
        default=4, description="Espera recomendada en el sondeo de comandos (segundos)."
    )


class AgentMetrics(BaseModel):
    """Métricas que reporta el agente en cada heartbeat."""

    cpu: float | None = Field(default=None, description="Uso de CPU (%).")
    ram: float | None = Field(default=None, description="Uso de RAM (%).")
    swap: float | None = Field(default=None, description="Uso de swap (%).")
    disk: float | None = Field(default=None, description="Uso del disco raíz (%).")
    load1: float | None = Field(default=None, description="Carga media de 1 minuto.")
    temp: float | None = Field(default=None, description="Temperatura de la CPU (°C).")


class AgentHeartbeatRequest(BaseModel):
    """Latido del agente: identidad, métricas e inventario opcional."""

    metrics: AgentMetrics = Field(..., description="Métricas del instante.")
    hostname: str = Field(default="", max_length=120, description="Hostname del agente.")
    os: str = Field(default="", max_length=120, description="Sistema operativo del agente.")
    agent_version: str = Field(default="", max_length=40, description="Versión del agente.")
    state: dict[str, Any] = Field(
        default_factory=dict,
        description="Instantánea opcional del host (contenedores, servicios…).",
    )


class AgentHeartbeatResponse(BaseModel):
    """Confirmación del heartbeat."""

    ok: bool = Field(default=True, description="Siempre true si el latido se aceptó.")
    host_id: int = Field(..., description="Host al que se atribuyó la muestra.")
    ts: int = Field(..., description="Marca temporal Unix de la muestra.")


class AgentCommand(BaseModel):
    """Comando que el hub entrega al agente para su ejecución local."""

    id: int = Field(..., description="Identificador del comando (para devolver el resultado).")
    tool: str = Field(..., description="Herramienta allowlisted a ejecutar.")
    args: dict[str, Any] = Field(default_factory=dict, description="Argumentos de la herramienta.")
    created_at: str | None = Field(default=None, description="Creación (ISO8601 UTC).")


class AgentCommandResult(BaseModel):
    """Resultado (o error) que el agente devuelve tras ejecutar un comando."""

    status: Literal["done", "error"] = Field(..., description="done si la ejecución fue correcta.")
    result: Any = Field(default=None, description="Resultado serializable de la herramienta.")
    error: str | None = Field(default=None, description="Motivo del error (status='error').")


class Host(BaseModel):
    """Host del inventario de la flota."""

    id: int = Field(..., description="Identificador del host (1 = este servidor).")
    name: str = Field(..., description="Nombre elegido en el panel.")
    hostname: str | None = Field(default=None, description="Hostname que informa el agente.")
    os: str | None = Field(default=None, description="Sistema operativo del host.")
    agent_version: str | None = Field(default=None, description="Versión del agente instalada.")
    token_prefix: str | None = Field(
        default=None, description="Prefijo legible del token (el secreto no se almacena)."
    )
    status: str = Field(default="offline", description="Estado: online | offline | disabled.")
    online: bool = Field(default=False, description="True si el último latido es reciente.")
    last_seen_ts: int | None = Field(default=None, description="Último latido (Unix).")
    seconds_since_seen: float | None = Field(
        default=None, description="Segundos transcurridos desde el último latido."
    )
    enrolled_at: str | None = Field(default=None, description="Enrolamiento (ISO8601 UTC).")
    notes: str | None = Field(default=None, description="Notas del administrador.")
    enabled: int = Field(default=1, description="1 = acepta tráfico del agente, 0 = deshabilitado.")
    is_local: int = Field(default=0, description="1 si es el servidor donde corre el hub.")


class HostListResponse(BaseModel):
    """Listado de la flota con recuentos."""

    count: int = Field(..., description="Número de hosts devueltos.")
    online: int = Field(..., description="Hosts en línea ahora mismo.")
    offline: int = Field(..., description="Hosts sin latido reciente.")
    hosts: list[Host] = Field(default_factory=list, description="Hosts del inventario.")


class HostUpdate(BaseModel):
    """Cambios parciales de un host (PATCH)."""

    name: str | None = Field(
        default=None, min_length=1, max_length=120, description="Nuevo nombre."
    )
    notes: str | None = Field(default=None, max_length=200, description="Notas internas.")
    enabled: bool | None = Field(
        default=None,
        description=(
            "False deshabilita el host: su token deja de autenticar al instante y "
            "el agente solo ve un 401."
        ),
    )


class EnrollmentInfo(BaseModel):
    """Token de enrolamiento de los agentes (secreto del hub)."""

    token: str = Field(..., description="Token con el que los agentes se enrolan.")
    source: str = Field(
        default="generado",
        description="Origen del token: 'entorno' (AGENT_ENROLL_TOKEN) o 'generado'.",
    )
    command: str = Field(
        default="", description="Comando exacto de enrolamiento para pegar en el host."
    )
    warning: str = Field(
        default="",
        description="Aviso: es un secreto; el que lo posee puede dar de alta hosts.",
    )


class CommandRequest(BaseModel):
    """Petición de ejecución remota de una herramienta en un host."""

    tool: str = Field(
        ..., min_length=1, max_length=80, description="Herramienta allowlisted (p. ej. 'docker.list')."
    )
    args: dict[str, Any] = Field(default_factory=dict, description="Argumentos de la herramienta.")
    timeout_s: int = Field(
        default=30,
        ge=1,
        le=180,
        description="Segundos que se espera el resultado antes de marcarlo expirado.",
    )


class CommandDispatchResponse(BaseModel):
    """Resultado de un comando remoto."""

    command_id: int = Field(..., description="Comando encolado.")
    host_id: int = Field(..., description="Host que lo ejecutó.")
    tool: str = Field(..., description="Herramienta ejecutada.")
    status: str = Field(..., description="done | error | expired.")
    result: Any = Field(default=None, description="Resultado devuelto por el agente.")
    error: str | None = Field(default=None, description="Motivo del error o del caducado.")
    elapsed_ms: int = Field(default=0, description="Tiempo que tardó el agente en responder.")


class CommandListResponse(BaseModel):
    """Listado de comandos de un host."""

    count: int = Field(..., description="Número de comandos devueltos.")
    commands: list[dict[str, Any]] = Field(default_factory=list, description="Comandos recientes.")
