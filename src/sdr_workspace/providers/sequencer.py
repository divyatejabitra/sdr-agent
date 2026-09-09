from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from uuid import uuid4
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

import httpx

from sdr_workspace.config import Settings


def redact_query_key(url: str) -> str:
    parsed = urlparse(url)
    query = [(k, "***" if k == "api_key" else v) for k, v in parse_qsl(parsed.query)]
    return urlunparse(parsed._replace(query=urlencode(query)))


@dataclass
class SequencerResult:
    status: str
    external_id: str = ""
    error: str = ""
    retryable: bool = False


class ColdSequencer(Protocol):
    name: str

    def enroll(self, campaign_external_id: str, lead: dict) -> SequencerResult: ...

    def pause(self, campaign_external_id: str, lead_id: str) -> SequencerResult: ...

    def unsubscribe(self, lead_id: str) -> SequencerResult: ...

    def status(self, campaign_external_id: str, lead_id: str) -> SequencerResult: ...


class MockSequencer:
    name = "mock_sequencer"

    def __init__(self) -> None:
        self.leads: dict[str, dict] = {}

    def enroll(self, campaign_external_id: str, lead: dict) -> SequencerResult:
        if lead.get("email", "").startswith("timeout@"):
            return SequencerResult(status="unknown", error="timeout_after_possible_enroll")
        lead_id = f"sl-{uuid4().hex[:10]}"
        self.leads[lead_id] = {"campaign": campaign_external_id, **lead, "paused": False}
        return SequencerResult(status="accepted", external_id=lead_id)

    def pause(self, campaign_external_id: str, lead_id: str) -> SequencerResult:
        if lead_id in self.leads:
            self.leads[lead_id]["paused"] = True
        return SequencerResult(status="accepted", external_id=lead_id)

    def unsubscribe(self, lead_id: str) -> SequencerResult:
        if lead_id in self.leads:
            self.leads[lead_id]["unsubscribed"] = True
        return SequencerResult(status="accepted", external_id=lead_id)

    def status(self, campaign_external_id: str, lead_id: str) -> SequencerResult:
        if lead_id not in self.leads:
            return SequencerResult(status="missing", error="not_found")
        return SequencerResult(status="accepted", external_id=lead_id)


class SmartleadSequencer:
    name = "smartlead"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _params(self) -> dict:
        key = self.settings.smartlead_api_key
        if key is None:
            raise RuntimeError("smartlead_unconfigured")
        return {"api_key": key.get_secret_value()}

    def enroll(self, campaign_external_id: str, lead: dict) -> SequencerResult:
        url = f"{self.settings.smartlead_base_url}/campaigns/{campaign_external_id}/leads"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(
                    url,
                    params=self._params(),
                    json={
                        "lead_list": [lead],
                        "settings": {
                            "ignore_duplicate_leads_in_other_campaign": False,
                            "ignore_global_block_list": False,
                            "ignore_unsubscribe_list": False,
                        },
                    },
                )
        except httpx.TimeoutException:
            return SequencerResult(status="unknown", error="timeout_after_possible_enroll")
        except RuntimeError as exc:
            return SequencerResult(status="failed", error=str(exc))
        if response.status_code >= 400:
            return SequencerResult(status="failed", error=f"http_{response.status_code}")
        data = response.json()
        external = str(data.get("upload_id") or data.get("added_count") or "smartlead")
        return SequencerResult(status="accepted", external_id=external)

    def pause(self, campaign_external_id: str, lead_id: str) -> SequencerResult:
        url = f"{self.settings.smartlead_base_url}/campaigns/{campaign_external_id}/leads/{lead_id}/pause"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(url, params=self._params())
        except httpx.TimeoutException:
            return SequencerResult(status="unknown", error="timeout")
        if response.status_code >= 400:
            return SequencerResult(status="failed", error=f"http_{response.status_code}", retryable=True)
        return SequencerResult(status="accepted", external_id=lead_id)

    def unsubscribe(self, lead_id: str) -> SequencerResult:
        url = f"{self.settings.smartlead_base_url}/leads/{lead_id}/unsubscribe"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(url, params=self._params())
        except httpx.TimeoutException:
            return SequencerResult(status="unknown", error="timeout")
        if response.status_code >= 400:
            return SequencerResult(status="failed", error=f"http_{response.status_code}", retryable=True)
        return SequencerResult(status="accepted", external_id=lead_id)

    def status(self, campaign_external_id: str, lead_id: str) -> SequencerResult:
        url = f"{self.settings.smartlead_base_url}/campaigns/{campaign_external_id}/leads/{lead_id}/message-history"
        try:
            with httpx.Client(timeout=20) as client:
                response = client.get(url, params=self._params())
        except httpx.TimeoutException:
            return SequencerResult(status="unknown", error="timeout")
        if response.status_code == 404:
            return SequencerResult(status="missing", error="not_found")
        if response.status_code >= 400:
            return SequencerResult(status="failed", error=f"http_{response.status_code}")
        return SequencerResult(status="accepted", external_id=lead_id)


def get_sequencer(provider: str, settings: Settings, mock: MockSequencer | None = None) -> ColdSequencer:
    if provider == "smartlead":
        return SmartleadSequencer(settings)
    return mock or MockSequencer()
