from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "SDR Workspace"
    delivery_mode: Literal["mock", "test", "live"] = "mock"
    public_base_url: str = "http://127.0.0.1:8000"
    database_url: str = "postgresql+psycopg://sdr:sdr@127.0.0.1:5432/sdr_workspace"

    owner_email: str = "owner@example.com"
    owner_password: str = "change-me-local"
    session_secret: str = "dev-session-secret-change-me"
    internal_api_key: str = "dev-internal-key-change-me"
    unsubscribe_secret: str = "dev-unsubscribe-secret-change-me"

    business_name: str = "Harborline"
    business_legal_name: str = "Harborline Demo LLC"
    sender_name: str = "Alex Rivera"
    sender_email: str = "alex@harborline.example"
    business_postal_address: str = "100 Market Street, Suite 400, Austin, TX 78701"
    sending_window_start: str = "09:00"
    sending_window_end: str = "17:00"
    default_timezone: str = "America/Chicago"

    promotional_min_gap_days: int = 7
    contact_active_enrollment_cap: int = 1
    max_body_words: int = 120
    test_recipient_allowlist: str = "owner@example.com,qa@example.com"

    llm_provider: Literal["mock", "ollama"] = "mock"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1"
    llm_timeout_seconds: float = 20.0

    sendgrid_api_key: SecretStr | None = None
    sendgrid_webhook_public_key: str = ""
    smartlead_api_key: SecretStr | None = None
    smartlead_base_url: str = "https://server.smartlead.ai/api/v1"
    hubspot_access_token: SecretStr | None = None

    worker_id: str = Field(default_factory=lambda: f"worker-{uuid4().hex[:8]}")
    job_lease_seconds: int = 60
    webhook_max_skew_seconds: int = 600
    event_payload_retention_days: int = 90

    @property
    def test_recipients(self) -> set[str]:
        return {item.strip().lower() for item in self.test_recipient_allowlist.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> UUID:
    return uuid4()
