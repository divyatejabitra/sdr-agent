#!/usr/bin/env python
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field

from crewai.flow import Flow, listen, router, start

from sdr_agent.crews.outreach_crew.outreach_crew import OutreachCrew, research_tools_status_note
from sdr_agent.schemas import OutreachEmail, ProspectIntel
from sdr_agent.tools.send_email import send_plain_email

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"
SAMPLE_LEAD = PROJECT_ROOT / "data" / "sample_lead.json"
VALID_CAMPAIGNS = {"cold", "offer", "update"}


class SDRState(BaseModel):
    campaign_type: str = "cold"
    prospect_name: str = ""
    prospect_title: str = ""
    prospect_email: str = ""
    prospect_company: str = ""
    company_website: str = ""
    sender_name: str = ""
    sender_title: str = ""
    sender_company: str = ""
    sender_email: str = ""
    sender_phone: str = ""
    physical_address: str = ""
    product_name: str = ""
    value_proposition: str = ""
    offer_details: str = ""
    update_details: str = ""
    extra_notes: str = ""
    research_brief: str = ""
    subject: str = ""
    preview_text: str = ""
    body: str = ""
    send_status: str = ""
    draft_path: str = ""


def _load_lead() -> dict:
    lead_file = Path(os.getenv("LEADS_FILE", SAMPLE_LEAD))
    if not lead_file.exists():
        raise FileNotFoundError(f"Lead file not found: {lead_file}")
    return json.loads(lead_file.read_text())


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return cleaned or "prospect"


class SDRFlow(Flow[SDRState]):

    @start()
    def load_campaign(self, crewai_trigger_payload: dict | None = None):
        payload = crewai_trigger_payload or _load_lead()
        for field in SDRState.model_fields:
            if field in payload and payload[field] is not None:
                setattr(self.state, field, payload[field])

        campaign = (self.state.campaign_type or "cold").strip().lower()
        if campaign not in VALID_CAMPAIGNS:
            raise ValueError(
                f"campaign_type must be one of {sorted(VALID_CAMPAIGNS)}, got {campaign!r}"
            )
        self.state.campaign_type = campaign
        print(
            f"SDR campaign={campaign} prospect={self.state.prospect_name} "
            f"at {self.state.prospect_company}"
        )

    @listen(load_campaign)
    def run_outreach(self):
        inputs = self.state.model_dump()
        inputs["tools_status_note"] = research_tools_status_note()
        result = OutreachCrew().crew().kickoff(inputs=inputs)
        outputs = result.tasks_output

        research = outputs[0].pydantic if outputs else None
        if isinstance(research, ProspectIntel):
            self.state.research_brief = research.model_dump_json(indent=2)
        elif outputs:
            self.state.research_brief = outputs[0].raw

        email = result.pydantic
        if isinstance(email, OutreachEmail):
            self.state.subject = email.subject
            self.state.preview_text = email.preview_text
            self.state.body = email.body
        else:
            self.state.body = result.raw
            self.state.subject = self.state.subject or "(subject in body)"

    @listen(run_outreach)
    def save_draft(self):
        OUTPUT_DIR.mkdir(exist_ok=True)
        stem = f"{self.state.campaign_type}_{_slug(self.state.prospect_company)}_{_slug(self.state.prospect_name)}"
        draft_path = OUTPUT_DIR / f"{stem}.md"
        research_path = OUTPUT_DIR / f"{stem}_research.json"

        draft_path.write_text(
            f"# {self.state.campaign_type.title()} email to {self.state.prospect_name}\n\n"
            f"- To: {self.state.prospect_email}\n"
            f"- Company: {self.state.prospect_company}\n"
            f"- Subject: {self.state.subject}\n"
            f"- Preview: {self.state.preview_text}\n\n"
            f"---\n\n{self.state.body}\n"
        )
        if self.state.research_brief:
            research_path.write_text(self.state.research_brief)

        self.state.draft_path = str(draft_path)
        print(f"Draft saved to {draft_path}")

    @router(save_draft)
    def decide_delivery(self):
        if os.getenv("SEND_EMAILS", "").lower() in {"1", "true", "yes"}:
            return "send"
        return "draft_only"

    @listen("draft_only")
    def keep_as_draft(self):
        self.state.send_status = (
            "Draft only. Review the file, then set SEND_EMAILS=true and SMTP_* to deliver."
        )
        print(self.state.send_status)
        return self.state.send_status

    @listen("send")
    def deliver_email(self):
        self.state.send_status = send_plain_email(
            to_address=self.state.prospect_email,
            subject=self.state.subject,
            body=self.state.body,
            from_address=self.state.sender_email or None,
        )
        print(self.state.send_status)
        return self.state.send_status


def kickoff():
    SDRFlow().kickoff()


def plot():
    SDRFlow().plot("sdr_flow")


def run_with_trigger():
    import sys

    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided. Please provide JSON payload as argument.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        raise Exception("Invalid JSON payload provided as argument") from exc

    flow = SDRFlow()
    return flow.kickoff({"crewai_trigger_payload": trigger_payload})


def run_batch():
    """Run one flow per object in a JSON array file (LEADS_FILE)."""
    leads_path = Path(os.getenv("LEADS_FILE", PROJECT_ROOT / "data" / "sample_leads.json"))
    leads = json.loads(leads_path.read_text())
    if isinstance(leads, dict):
        leads = [leads]
    for lead in leads:
        SDRFlow().kickoff(inputs={"crewai_trigger_payload": lead})


if __name__ == "__main__":
    kickoff()
