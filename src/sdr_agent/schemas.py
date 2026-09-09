from pydantic import BaseModel, Field


class ProspectIntel(BaseModel):
    used_live_tools: bool = Field(
        description="True only if a web search or scrape tool call actually ran this task. "
        "False if research came only from the lead file and extra_notes."
    )
    company_summary: str = ""
    likely_priorities: list[str] = Field(default_factory=list)
    personalization_hooks: list[str] = Field(default_factory=list)
    recent_signals: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class OutreachEmail(BaseModel):
    subject: str
    preview_text: str = ""
    body: str
    personalization_hooks_used: list[str] = Field(default_factory=list)
    call_to_action: str = ""
    compliance_notes: str = ""
