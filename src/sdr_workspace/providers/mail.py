from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

import httpx

from sdr_workspace.config import Settings


@dataclass
class SendResult:
    status: str
    provider_id: str = ""
    error: str = ""
    retryable: bool = False


class MailAdapter(Protocol):
    name: str

    def send(self, *, to_email: str, subject: str, text: str, html: str, correlation_id: str) -> SendResult: ...


class MockMail:
    name = "mock_mail"

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, *, to_email: str, subject: str, text: str, html: str, correlation_id: str) -> SendResult:
        provider_id = f"mock-{uuid4().hex[:12]}"
        self.sent.append(
            {
                "to": to_email,
                "subject": subject,
                "text": text,
                "html": html,
                "correlation_id": correlation_id,
                "id": provider_id,
            }
        )
        if to_email.startswith("fail@"):
            return SendResult(status="failed", error="simulated_failure", retryable=False)
        if to_email.startswith("timeout@"):
            return SendResult(status="unknown", error="timeout_after_possible_accept", retryable=False)
        return SendResult(status="accepted", provider_id=provider_id)


class SendGridMail:
    name = "sendgrid"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, *, to_email: str, subject: str, text: str, html: str, correlation_id: str) -> SendResult:
        key = self.settings.sendgrid_api_key
        if key is None:
            return SendResult(status="failed", error="sendgrid_unconfigured", retryable=False)
        payload = {
            "personalizations": [{"to": [{"email": to_email}], "custom_args": {"cid": correlation_id}}],
            "from": {"email": self.settings.sender_email, "name": self.settings.sender_name},
            "subject": subject,
            "content": [
                {"type": "text/plain", "value": text},
                {"type": "text/html", "value": html},
            ],
            "mail_settings": {"bypass_list_management": {"enable": False}},
            "headers": {
                "List-Unsubscribe": f"<{self.settings.public_base_url}/unsubscribe/one-click>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        }
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(
                    "https://api.sendgrid.com/v3/mail/send",
                    headers={"Authorization": f"Bearer {key.get_secret_value()}", "Content-Type": "application/json"},
                    json=payload,
                )
        except httpx.TimeoutException:
            return SendResult(status="unknown", error="timeout_after_possible_accept", retryable=False)
        if response.status_code in {200, 202}:
            return SendResult(status="accepted", provider_id=response.headers.get("X-Message-Id", correlation_id))
        if response.status_code in {429, 500, 502, 503}:
            return SendResult(status="failed", error=f"http_{response.status_code}", retryable=True)
        return SendResult(status="failed", error=f"http_{response.status_code}", retryable=False)


def get_mail_adapter(provider: str, settings: Settings, mock: MockMail | None = None) -> MailAdapter:
    if provider == "sendgrid":
        return SendGridMail(settings)
    return mock or MockMail()
