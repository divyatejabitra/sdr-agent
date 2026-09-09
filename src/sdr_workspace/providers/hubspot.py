from __future__ import annotations

from dataclasses import dataclass

from sdr_workspace.config import Settings


@dataclass
class CrmStatus:
    connected: bool
    provider: str = "hubspot"
    detail: str = ""


class HubSpotAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> CrmStatus:
        token = self.settings.hubspot_access_token
        if token is None or not token.get_secret_value():
            return CrmStatus(connected=False, detail="disconnected")
        return CrmStatus(connected=True, detail="configured_not_live_tested")
