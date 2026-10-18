"""Configuración central de Nexus-Core.

Toda la configuración se inyecta mediante variables de entorno (o un fichero
``.env`` en desarrollo) y se valida con ``pydantic-settings``. El objeto
resultante se cachea mediante ``lru_cache`` para no re-parsear el entorno en
cada petición.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración tipada de la aplicación.

    Los nombres de los campos coinciden con las variables de entorno (en
    mayúsculas). Los valores por defecto permiten arrancar el proyecto sin
    configuración adicional en un entorno de laboratorio.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------- Aplicación -------------------------------
    APP_NAME: str = "Nexus-Core"
    APP_VERSION: str = "1.0.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_PREFIX: str = "/api"
    LOG_LEVEL: str = "INFO"
    # Orígenes permitidos por CORS, separados por comas. Vacío = sin CORS
    # (same-origin), que es el modo recomendado: panel y API se sirven desde
    # el mismo host. "*" queda desaconsejado (se ignora con aviso).
    ALLOWED_ORIGINS: str = ""
    # Directorio del frontend estático (relativo al WORKDIR /app).
    FRONTEND_DIR: str = "frontend"

    # ------------------------------- Seguridad --------------------------------
    # Hosts aceptados por TrustedHostMiddleware (csv). "*" desactiva el filtro
    # (cómodo en LAN con IP cambiante); en producción fija el host real.
    ALLOWED_HOSTS: str = "*"
    # Sesiones del panel: caducidad por inactividad y absoluta.
    SESSION_IDLE_MINUTES: int = 30
    SESSION_ABSOLUTE_HOURS: int = 8
    # Marcar la cookie como "Secure" cuando el panel se sirva por HTTPS.
    SESSION_COOKIE_SECURE: bool = False
    # Límite de intentos de login por usuario+IP dentro de la ventana.
    LOGIN_RATE_LIMIT: int = 5
    LOGIN_RATE_WINDOW_SECONDS: int = 900

    # ---------------------------------- Docker --------------------------------
    DOCKER_SOCKET: str = "unix:///var/run/docker.sock"
    CONTAINER_NAME_PREFIX: str = "nexus-"
    MAX_RUNNING_CONTAINERS: int = 15
    # Whitelist de imágenes desplegables (csv). Evita ejecutar imágenes arbitrarias.
    ALLOWED_IMAGES: str = (
        "alpine,nginx,httpd,busybox,redis,postgres,mysql,traefik,portainer"
    )

    # ------------------------------------ Red ---------------------------------
    SCAN_DEFAULT_SUBNET: str = "192.168.1.0/24"
    SCAN_TIMEOUT_SECONDS: int = 120

    # ------------------------- Exportación a Prometheus ------------------------
    # Si está vacío, /api/metrics/prometheus es público (por defecto). Al
    # configurarlo, el endpoint exige «Authorization: Bearer <token>».
    METRICS_TOKEN: str = ""
    # Comprobador de monitores en segundo plano. Se desactiva (MONITORS_ENABLED=
    # false) en los tests —para que no hagan peticiones de red reales— y durante
    # un mantenimiento en el que no deba salir tráfico.
    MONITORS_ENABLED: bool = True

    # ------------------------------ Flota (multi-host) ------------------------
    # Token de enrolamiento de los agentes remotos: quien lo presenta puede dar
    # de alta un host en la flota (POST /api/agent/enroll). Si se deja vacío, el
    # hub genera uno y lo guarda en $NEXUS_DATA_DIR/config/agent_enroll_token
    # (0600); se consulta desde el panel con GET /api/hosts/enrollment.
    AGENT_ENROLL_TOKEN: str = ""
    # Segundos sin heartbeat a partir de los cuales un host se marca «offline».
    AGENT_ONLINE_WINDOW: int = 30

    # --------------------------------- Agente IA ------------------------------
    # Proveedor: "openai" | "anthropic" | "gemini" | "ollama".
    # Vacío = sin proveedor: el asistente queda desactivado (es opcional y el
    # resto del panel funciona igual).
    AI_PROVIDER: str = ""
    AI_MODEL: str = "gpt-4o-mini"
    AI_TEMPERATURE: float = 0.2
    AI_MAX_TOOL_ITERATIONS: int = 4
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    ANTHROPIC_API_KEY: str = ""
    # Endpoint compatible OpenAI que expone Ollama en el host.
    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434/v1"
    # --- Google Gemini (API Generative Language) ---
    # La clave se envía en la cabecera `x-goog-api-key`.
    GEMINI_API_KEY: str = ""
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
    GEMINI_MODEL: str = "gemini-2.5-flash"
    AI_REQUEST_TIMEOUT: int = 60

    # --------------------------- Propiedades derivadas ------------------------
    @property
    def allowed_origins_list(self) -> list[str]:
        """Lista de orígenes CORS. Vacío = sin middleware CORS (same-origin)."""
        raw = self.ALLOWED_ORIGINS.strip()
        if not raw or raw == "*":
            return []
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def allowed_hosts_list(self) -> list[str]:
        """Lista de hosts aceptados por TrustedHostMiddleware ('*' = todos)."""
        raw = self.ALLOWED_HOSTS.strip()
        if not raw or raw == "*":
            return ["*"]
        return [host.strip() for host in raw.split(",") if host.strip()]

    @property
    def allowed_images_list(self) -> list[str]:
        """Devuelve la whitelist de imágenes Docker normalizada."""
        return [image.strip().lower() for image in self.ALLOWED_IMAGES.split(",") if image.strip()]

    @property
    def ai_base_url(self) -> str:
        """URL base del proveedor LLM activo."""
        provider = self.AI_PROVIDER.lower()
        if provider == "ollama":
            return self.OLLAMA_BASE_URL.rstrip("/")
        if provider == "anthropic":
            return "https://api.anthropic.com/v1"
        if provider == "gemini":
            return self.GEMINI_BASE_URL.rstrip("/")
        return self.OPENAI_BASE_URL.rstrip("/")

    @property
    def ai_api_key(self) -> str:
        """Clave de API del proveedor LLM activo (en Ollama no es necesaria)."""
        provider = self.AI_PROVIDER.lower()
        if provider == "ollama":
            return "ollama"
        if provider == "anthropic":
            return self.ANTHROPIC_API_KEY
        if provider == "gemini":
            return self.GEMINI_API_KEY
        return self.OPENAI_API_KEY

    @property
    def ai_model(self) -> str:
        """Modelo efectivo del proveedor activo.

        Si el proveedor es Gemini pero ``AI_MODEL`` conserva el valor de otro
        proveedor (por ejemplo tras cambiar de OpenAI), se usa automáticamente
        ``GEMINI_MODEL`` para evitar peticiones inválidas. Sin proveedor,
        devuelve cadena vacía.
        """
        provider = self.AI_PROVIDER.lower()
        if not provider:
            return ""
        if provider == "gemini" and not self.AI_MODEL.startswith("gemini"):
            return self.GEMINI_MODEL
        return self.AI_MODEL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve una instancia única (cacheada) de la configuración."""
    return Settings()
