from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ADMIN_KEY = "change-me-admin-key"


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- Général
    app_name: str = "IntelliFlood API"
    environment: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./intelliflood.db"
    auto_create_tables: bool = True
    cors_origins: str = "*"
    trust_proxy: bool = False
    default_phone_region: str = "CI"

    # --- Sécurité (F3.5)
    admin_api_key: str = DEFAULT_ADMIN_KEY  # protège admin + vue autorités
    sensor_api_key: str | None = None  # clé globale optionnelle (en plus des clés par point)

    # --- Logique d'alerte
    rise_window_minutes: int = 5
    sensor_offline_after_seconds: int = 300
    alert_cooldown_seconds: int = 300
    sms_on_orange: bool = False
    onpc_notify_on_orange: bool = False

    # --- IA (Module 4) : none = messages prédéfinis uniquement
    ai_provider: Literal["none", "anthropic", "openai_compatible"] = "none"
    ai_api_key: str | None = None
    ai_model: str = "claude-haiku-5-5"
    ai_base_url: str = "http://localhost:11434/v1"  # Ollama / LM Studio / tout serveur compatible OpenAI
    ai_timeout_seconds: float = 8.0

    # --- SMS (Module 5)
    sms_provider: Literal["console", "twilio", "africastalking"] = "console"
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from: str | None = None
    at_username: str | None = None
    at_api_key: str | None = None
    at_sender_id: str | None = None
    at_sandbox: bool = True

    # --- ONPC (démonstration)
    onpc_phone_numbers: str = ""
    onpc_emails: str = ""
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "alertes@intelliflood.local"

    # --- Notifications push (FCM)
    push_provider: Literal["none", "fcm"] = "none"
    fcm_project_id: str | None = None
    fcm_service_account_file: str | None = None

    # --- MQTT (optionnel, alternative au HTTP)
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_topic: str = "intelliflood/points/+/measurements"

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+psycopg://", 1)
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return _split(self.cors_origins) or ["*"]

    @property
    def onpc_phones(self) -> list[str]:
        return _split(self.onpc_phone_numbers)

    @property
    def onpc_email_list(self) -> list[str]:
        return _split(self.onpc_emails)

    def assert_production_ready(self) -> None:
        if self.environment == "production":
            if self.admin_api_key == DEFAULT_ADMIN_KEY or len(self.admin_api_key) < 16:
                raise RuntimeError("ADMIN_API_KEY doit être défini (16 caractères minimum) en production.")
            if self.cors_origins.strip() == "*":
                raise RuntimeError("Définissez CORS_ORIGINS explicitement en production.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
