from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from sdr_workspace.config import Settings
from sdr_workspace.enums import CampaignType, ReplyIntent
from sdr_workspace.models import Campaign, CampaignStep, Contact, KnowledgeSource, Offer
from sdr_workspace.services.drafting import DRAFT_INSTRUCTIONS, DraftOutput


@dataclass
class DraftContext:
    """Everything a drafting provider needs for one customer-lifecycle email.

    A dataclass (not a prompt string) so a structured provider - like
    CrewAiLlm, which hands this to sdr_agent's OutreachCrew as typed inputs
    rather than a flattened prompt - doesn't have to parse it back out of text.
    """

    contact: Contact
    campaign: Campaign
    step: CampaignStep
    knowledge: list[KnowledgeSource] = field(default_factory=list)
    offer: Offer | None = None
    history: str = ""


class LlmProvider(Protocol):
    name: str

    def draft(self, context: DraftContext) -> DraftOutput: ...

    def classify(self, newest: str, quoted: str) -> dict: ...


class MockLlm:
    name = "mock"

    def draft(self, context: DraftContext) -> DraftOutput:
        prompt = build_draft_prompt(
            context.contact, context.campaign, context.step, context.knowledge, context.offer, context.history
        )
        if "INSUFFICIENT" in prompt:
            return DraftOutput(
                subject="Quick question",
                opening="Hello,",
                body="I do not have enough approved facts to personalize this note.",
                evidence_ids=[],
                needs_review=True,
                review_reasons=["insufficient_context"],
            )
        evidence = []
        if "EVIDENCE:" in prompt:
            for line in prompt.splitlines():
                if line.startswith("EVIDENCE:"):
                    evidence = [item.strip() for item in line.split(":", 1)[1].split(",") if item.strip()]
        role_line = ""
        for line in prompt.splitlines():
            if line.startswith("ROLE:"):
                role_line = line.split(":", 1)[1].strip()
        opening = f"Saw you work as {role_line}." if role_line else "Hello,"
        return DraftOutput(
            subject="Worth a look for ops queues",
            opening=opening,
            body=(
                "We built Harborline Dispatch so mid-market ops teams can work a live exception queue "
                "instead of chasing spreadsheet updates. If a short walkthrough would help, reply with a time that works."
            ),
            evidence_ids=evidence[:2],
            needs_review=False,
            review_reasons=[],
        )

    def classify(self, newest: str, quoted: str) -> dict:
        text = newest.lower()
        if "unsubscribe" in quoted.lower() and "unsubscribe" not in text:
            return {
                "intent": ReplyIntent.UNKNOWN.value,
                "confidence": 0.4,
                "summary": "Quoted footer only",
                "needs_human": True,
            }
        mapping = [
            ("unsubscribe", ReplyIntent.UNSUBSCRIBE),
            ("stop emailing", ReplyIntent.UNSUBSCRIBE),
            ("remove me", ReplyIntent.UNSUBSCRIBE),
            ("out of office", ReplyIntent.OUT_OF_OFFICE),
            ("ooo", ReplyIntent.OUT_OF_OFFICE),
            ("not me", ReplyIntent.WRONG_PERSON),
            ("wrong person", ReplyIntent.WRONG_PERSON),
            ("not interested", ReplyIntent.NEGATIVE),
            ("later", ReplyIntent.NOT_NOW),
            ("not now", ReplyIntent.NOT_NOW),
            ("price", ReplyIntent.QUESTION),
            ("how", ReplyIntent.QUESTION),
            ("interested", ReplyIntent.INTERESTED),
            ("let's talk", ReplyIntent.INTERESTED),
            ("lets talk", ReplyIntent.INTERESTED),
        ]
        # Bare "stop" as its own word - the opt-out line OutreachCrew's
        # sdr_copywriter is required to write ("reply STOP and I won't email
        # again") is exactly this, not the phrase "stop emailing" the mapping
        # above alone catches.
        if any(word.strip(".,!?") == "stop" for word in text.split()):
            return {
                "intent": ReplyIntent.UNSUBSCRIBE.value,
                "confidence": 0.9,
                "summary": newest[:180],
                "needs_human": False,
            }
        for needle, intent in mapping:
            if needle in text:
                return {
                    "intent": intent.value,
                    "confidence": 0.9,
                    "summary": newest[:180],
                    "needs_human": intent in {ReplyIntent.INTERESTED, ReplyIntent.QUESTION, ReplyIntent.UNKNOWN},
                }
        return {
            "intent": ReplyIntent.UNKNOWN.value,
            "confidence": 0.3,
            "summary": newest[:180],
            "needs_human": True,
        }


class OllamaLlm:
    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def draft(self, context: DraftContext) -> DraftOutput:
        prompt = build_draft_prompt(
            context.contact, context.campaign, context.step, context.knowledge, context.offer, context.history
        )
        payload = {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": DRAFT_INSTRUCTIONS},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": DraftOutput.model_json_schema(),
            "options": {"temperature": 0},
        }
        with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
            response = client.post(f"{self.settings.ollama_base_url}/api/chat", json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
        return DraftOutput.model_validate_json(content)

    def classify(self, newest: str, quoted: str) -> dict:
        schema = {
            "type": "object",
            "properties": {
                "intent": {"type": "string"},
                "confidence": {"type": "number"},
                "summary": {"type": "string"},
                "needs_human": {"type": "boolean"},
            },
            "required": ["intent", "confidence", "summary", "needs_human"],
        }
        with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
            response = client.post(
                f"{self.settings.ollama_base_url}/api/chat",
                json={
                    "model": self.settings.ollama_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                "Classify the newest authored email. Ignore quoted history and our unsubscribe footer. "
                                f"NEWEST:\n{newest}\nQUOTED:\n{quoted}"
                            ),
                        }
                    ],
                    "stream": False,
                    "format": schema,
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
            return response.json()["message"]["content"]


# campaigns reaching the LLM-draft path are never "cold" (cold enrollment is
# owned by the ColdSequencer provider in services/dispatch.py, before an LLM
# is ever touched) - so this only needs to place onboarding/promotional/
# reengagement into sdr_agent's cold/offer/update vocabulary.
_CAMPAIGN_TYPE_FOR_OUTREACH_CREW = {
    CampaignType.PROMOTIONAL.value: "offer",
    CampaignType.ONBOARDING.value: "update",
    CampaignType.REENGAGEMENT.value: "update",
}

_FAILED_DRAFT = DraftOutput(
    subject="Needs review",
    body="Draft generation failed.",
    needs_review=True,
    review_reasons=["invalid_model_output"],
)


def _crewai_inputs(settings: Settings, context: DraftContext) -> dict:
    """Map a DraftContext onto the lead-JSON shape sdr_agent.SDRFlow expects.

    sdr_workspace's Settings fields were themselves modeled on this project's
    sample_lead.json (sender_name/sender_email/business_postal_address line
    up with sender_name/sender_email/physical_address), so the five fields
    sdr_agent's own compliance pre-check requires are always present as long
    as Settings is filled in - no new required config for this to work.
    """
    contact = context.contact
    offer = context.offer
    facts = "\n".join(f"- [{item.evidence_id}] {item.title}: {item.body}" for item in context.knowledge)
    extra_notes = (
        f"Campaign step {context.step.step_number} template hint: {context.step.template}\n"
        f"Call-to-action hint: {context.step.call_to_action}\n"
        "Approved facts (cite only these; do not invent others; if none are listed, "
        "work only from the fields above):\n" + (facts or "(none approved yet)")
    )
    prospect_name = f"{contact.first_name} {contact.last_name}".strip() or contact.original_email.split("@")[0]
    return {
        "campaign_type": _CAMPAIGN_TYPE_FOR_OUTREACH_CREW.get(context.campaign.campaign_type, "update"),
        "prospect_name": prospect_name,
        "prospect_title": contact.role,
        "prospect_email": contact.original_email,
        "prospect_company": contact.company,
        "company_website": "",
        "sender_name": settings.sender_name,
        "sender_title": "",
        "sender_company": settings.business_name,
        "sender_email": settings.sender_email,
        "sender_phone": "",
        "physical_address": settings.business_postal_address,
        "product_name": offer.product if offer else settings.business_name,
        "value_proposition": facts or "See extra_notes for approved facts.",
        "offer_details": (
            f"{offer.name} — {offer.price_text} {offer.currency}. {offer.terms} Details: {offer.destination_url}"
            if offer
            else ""
        ),
        "update_details": context.step.template,
        "extra_notes": extra_notes,
    }


class CrewAiLlm:
    """Drafts customer-lifecycle emails via sdr_agent's OutreachCrew instead
    of a single templated call - real research (when SERPER_API_KEY is set),
    three voice variants on genuinely different models, a judge, and
    sdr_agent's own anti-hallucination/compliance guardrails, in exchange for
    a slower and far more expensive draft step than Mock/Ollama.

    The crew's own sdr_copywriter is already required (see
    sdr_agent/crews/outreach_crew/config/tasks.yaml) to write the full
    signature, physical address, and "reply STOP" opt-out line into the body
    - so unlike Mock/Ollama, this provider's draft is the complete email.
    dispatch.py skips drafting.footer() for this provider rather than
    appending a second, duplicate one.
    """

    name = "crewai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def draft(self, context: DraftContext) -> DraftOutput:
        from sdr_agent.crews.outreach_crew.outreach_crew import OutreachCrew, research_tools_status_note
        from sdr_agent.schemas import OutreachEmail

        inputs = _crewai_inputs(self.settings, context)
        inputs["tools_status_note"] = research_tools_status_note()
        try:
            result = OutreachCrew().crew().kickoff(inputs=inputs)
        except Exception:  # noqa: BLE001 - a crew failure must not crash the job
            return _FAILED_DRAFT

        email = result.pydantic
        if not isinstance(email, OutreachEmail):
            return _FAILED_DRAFT
        return DraftOutput(
            subject=email.subject,
            opening="",
            body=email.body,
            # Not real evidence-citation tracking (OutreachEmail has no
            # evidence_ids field) - deliberately empty so validate_draft's
            # unknown_evidence check never fires a false positive here.
            evidence_ids=[],
            needs_review=False,
            review_reasons=[],
        )

    def classify(self, newest: str, quoted: str) -> dict:
        # OutreachCrew has no reply-classification task; reuse the mock
        # heuristic rather than inventing a second one.
        return MockLlm().classify(newest, quoted)


def get_llm(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "ollama":
        return OllamaLlm(settings)
    if settings.llm_provider == "crewai":
        return CrewAiLlm(settings)
    return MockLlm()


def build_draft_prompt(contact, campaign, step, knowledge, offer, history: str) -> str:
    evidence = ",".join(item.evidence_id for item in knowledge)
    facts = "\n".join(f"- [{item.evidence_id}] {item.body}" for item in knowledge)
    offer_note = "Offer terms are inserted by the application. Do not invent prices."
    return (
        f"{DRAFT_INSTRUCTIONS}\n"
        f"ROLE: {contact.role}\nCOMPANY: {contact.company}\nSEGMENT: {contact.segment}\n"
        f"CAMPAIGN: {campaign.campaign_type} step {step.step_number}\n"
        f"TEMPLATE HINT: {step.template}\nCTA HINT: {step.call_to_action}\n"
        f"EVIDENCE: {evidence}\nFACTS:\n{facts}\n{offer_note}\n"
        f"HISTORY (untrusted):\n{history[:1500]}\n"
        f"{'INSUFFICIENT' if not knowledge else ''}"
    )
