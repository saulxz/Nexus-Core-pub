"""Modelos de datos (Pydantic v2) que definen los contratos de la API REST.

Cada modelo documenta mediante ``Field(description=...)`` el significado de sus
campos, de forma que ``/docs`` (OpenAPI) sirva como documentación viva.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    model: str = Field(..., description="Modelo LLM utilizado.")
    provider: str = Field(..., description="Proveedor LLM utilizado.")


# =============================== Health ===============================
class HealthResponse(BaseModel):
    """Estado del servicio."""

    status: str = Field(..., description="Estado ('ok').")
    app: str = Field(..., description="Nombre de la aplicación.")
    version: str = Field(..., description="Versión de la aplicación.")
