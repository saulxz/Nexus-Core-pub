"""Modelos de datos (Pydantic v2) que definen los contratos de la API REST.

Cada modelo documenta mediante ``Field(description=...)`` el significado de sus
campos, de forma que ``/docs`` (OpenAPI) sirva como documentación viva.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    summary: str = Field(..., description="Descripción legible de lo que hará.")
    created_at: str = Field(..., description="Momento de creación (ISO8601 UTC).")
    expires_at: str = Field(..., description="Caducidad (ISO8601 UTC).")
    status: str = Field(default="pending", description="pending | confirmed | cancelled | expired")
