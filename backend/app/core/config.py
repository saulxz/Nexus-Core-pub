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
    # Orígenes permitidos por CORS, separados por comas. "*" en laboratorio.
    ALLOWED_ORIGINS: str = "*"
    # Directorio del frontend estático (relativo al WORKDIR /app).
    FRONTEND_DIR: str = "frontend"

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

    # --------------------------------- Agente IA ------------------------------
    # Proveedor: "openai" | "anthropic" | "gemini" | "ollama"
    AI_PROVIDER: str = "openai"
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
        """Devuelve la lista de orígenes CORS a partir del CSV de configuración."""
        if self.ALLOWED_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

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
        ``GEMINI_MODEL`` para evitar peticiones inválidas.
        """
        provider = self.AI_PROVIDER.lower()
        if provider == "gemini" and not self.AI_MODEL.startswith("gemini"):
            return self.GEMINI_MODEL
        return self.AI_MODEL


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve una instancia única (cacheada) de la configuración."""
    return Settings()
