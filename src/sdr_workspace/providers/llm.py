from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from sdr_workspace.config import Settings
from sdr_workspace.enums import ReplyIntent
from sdr_workspace.services.drafting import DRAFT_INSTRUCTIONS, DraftOutput


class LlmProvider(Protocol):
    name: str

    def draft(self, prompt: str) -> DraftOutput: ...

    def classify(self, newest: str, quoted: str) -> dict: ...


class MockLlm:
    name = "mock"

    def draft(self, prompt: str) -> DraftOutput:
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

    def draft(self, prompt: str) -> DraftOutput:
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


def get_llm(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "ollama":
        return OllamaLlm(settings)
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
