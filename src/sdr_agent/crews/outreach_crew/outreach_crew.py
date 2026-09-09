import json
import os

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.knowledge.source.text_file_knowledge_source import TextFileKnowledgeSource
from crewai.project import CrewBase, agent, crew, task
from crewai.tasks.task_output import TaskOutput

from sdr_agent.schemas import OutreachEmail, ProspectIntel


def _playbook_source() -> list[TextFileKnowledgeSource]:
    return [TextFileKnowledgeSource(file_paths=["sdr_playbook.md"])]


def _research_tools_available() -> bool:
    return bool(os.getenv("SERPER_API_KEY"))


def _research_tools() -> list:
    tools = []
    if _research_tools_available():
        from crewai_tools import ScrapeWebsiteTool, SerperDevTool

        tools = [SerperDevTool(), ScrapeWebsiteTool()]
    return tools


def research_tools_status_note() -> str:
    if _research_tools_available():
        return (
            "Live web search and scrape tools ARE available this run. Use them and cite the "
            "URLs you actually retrieved as sources."
        )
    return (
        "Live web search and scrape tools are NOT available this run. Do not call a search or "
        "scrape tool, and do not claim you searched, browsed, or found no results online - no "
        "search happened. Work only from the lead file and extra_notes. sources must be an "
        "empty list, and used_live_tools must be false."
    )


# Words that only mean something if a source was actually checked. If sources
# is empty, none of these should appear as an assertion about the company -
# the model has a tendency to state a plausible-sounding specific detail
# (e.g. "the website is expired") to explain away an empty search result,
# instead of just saying the search found nothing.
_UNVERIFIED_CLAIM_MARKERS = [
    "expired",
    "inactive website",
    "domain is inactive",
    "shut down",
    "shutdown",
    "ceased operations",
    "no longer in business",
    "acquired by",
    "bankrupt",
    "went public",
    "funding round",
    "raised $",
    "revenue of",
    "headquartered in",
    "founded in",
    "employees",
    "recently launched",
    "recently hired",
]


def _parse_prospect_intel(output: TaskOutput) -> ProspectIntel | None:
    """
    TaskOutput.pydantic is not reliably populated at guardrail-check time
    (CrewAI defers pydantic parsing when a guardrail is attached), so parse
    output.raw directly instead of trusting output.pydantic here.
    """
    if isinstance(output.pydantic, ProspectIntel):
        return output.pydantic
    try:
        return ProspectIntel.model_validate(json.loads(output.raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _research_guardrail(output: TaskOutput) -> tuple[bool, str | TaskOutput]:
    intel = _parse_prospect_intel(output)
    if intel is None or intel.sources:
        # Either couldn't parse structured output yet, or claims are backed
        # by cited sources - nothing to check here.
        return (True, output)

    text = f"{intel.company_summary} {' '.join(intel.recent_signals)}".lower()
    hits = sorted({marker for marker in _UNVERIFIED_CLAIM_MARKERS if marker in text})
    if hits:
        return (
            False,
            "sources is empty, so none of this brief's claims are verified, but "
            f"company_summary/recent_signals still assert specific details ({', '.join(hits)}). "
            "If a search ran and found nothing relevant, say exactly that in unknowns "
            "(e.g. 'search for the company name returned no verifiable results') - do not "
            "state a specific fact you did not actually confirm from a retrieved source.",
        )
    return (True, output)


def _email_guardrail(output: TaskOutput) -> tuple[bool, str | TaskOutput]:
    text = (output.raw or "").lower()
    missing = []
    if "stop" not in text and "unsubscribe" not in text:
        missing.append("an opt-out line (reply STOP)")
    if len((output.raw or "").split()) < 40:
        missing.append("a complete email body")
    if missing:
        return (False, f"Add {', '.join(missing)} and try again.")
    return (True, output)


@CrewBase
class OutreachCrew:
    """Research a prospect and write one SDR email."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def prospect_researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["prospect_researcher"],  # type: ignore[index]
            tools=_research_tools(),
        )

    @agent
    def sdr_copywriter(self) -> Agent:
        return Agent(
            config=self.agents_config["sdr_copywriter"],  # type: ignore[index]
            knowledge_sources=_playbook_source(),
        )

    @task
    def research_task(self) -> Task:
        return Task(
            config=self.tasks_config["research_task"],  # type: ignore[index]
            output_pydantic=ProspectIntel,
            guardrail=_research_guardrail,
            guardrail_max_retries=2,
        )

    @task
    def write_email_task(self) -> Task:
        return Task(
            config=self.tasks_config["write_email_task"],  # type: ignore[index]
            context=[self.research_task()],
            output_pydantic=OutreachEmail,
            guardrail=_email_guardrail,
            guardrail_max_retries=2,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
