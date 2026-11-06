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
    # Database-enforced job control (migration 20261105120000_ai_orchestration_safety).
    # A ticket whose classification keeps crashing or going stale ends in review
    # after this many claims.
    ai_max_attempts: int = Field(default=3, ge=1)
    # User-triggered runs: per-ticket cooldown for everyone, lifetime cap for the
    # requester. Staff reclassification is limited by the cooldown only.
    ai_manual_cooldown_seconds: int = Field(default=60, ge=0)
    ai_requester_manual_limit: int = Field(default=3, ge=0)
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
    def ai_enabled(self) -> bool:
        # Reports configuration, not a live Ollama availability probe.
        return (
            self.ai_classification_enabled
            and self.admin_enabled
            and bool(self.ollama_url.strip() and self.ollama_model.strip())
        )

    @property
    def ai_lease_seconds(self) -> int:
        # Longer than one model call can legitimately take; an expired lease means
        # the result is discarded and the ticket is retried, never half-written.
        return int(self.ollama_timeout_seconds) + 60

    @property
    def admin_enabled(self) -> bool:
        return bool(self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
