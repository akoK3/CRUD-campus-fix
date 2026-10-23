from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration, loaded from backend/.env.

    Nothing here is ever sent to a browser.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_publishable_key: str

    # Bypasses Row Level Security completely. Treat as root.
    supabase_service_role_key: str = ""

    ai_classification_enabled: bool = True
    ollama_url: str = "http://localhost:11434/api/chat"
    ollama_model: str = "deepseek-r1:8b"
    ollama_timeout_seconds: float = Field(default=180, gt=0)

    ai_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    cors_origins: str = "https://campusfizz.netlify.app,http://localhost:5173"

    # Email delivery of notifications. Leave SMTP_HOST blank to disable; the
    # in-app notifications work regardless.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "CampusFix <no-reply@caldwell.edu>"
    notify_interval_seconds: int = 60
    app_url: str = "https://campusfizz.netlify.app"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host) and self.admin_enabled

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ai_enabled(self) -> bool:
        # Reports configuration, not a live Ollama availability probe.
        return (
            self.ai_classification_enabled
            and self.admin_enabled
            and bool(self.ollama_url.strip() and self.ollama_model.strip())
        )

    @property
    def admin_enabled(self) -> bool:
        return bool(self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
