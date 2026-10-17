import json
import os

from crewai import LLM, Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.knowledge.source.text_file_knowledge_source import TextFileKnowledgeSource
from crewai.project import CrewBase, agent, crew, task
from crewai.tasks.task_output import TaskOutput

from sdr_agent.schemas import OutreachEmail, ProspectIntel, SelectedEmail


def _playbook_source() -> list[TextFileKnowledgeSource]:
    return [TextFileKnowledgeSource(file_paths=["sdr_playbook.md"])]


def _research_tools_available() -> bool:
    return bool(os.getenv("SERPER_API_KEY"))


def _research_tools() -> list:
    tools = []
    if _research_tools_available():
        from crewai_tools import ScrapeWebsiteTool, SerperDevTool

        from sdr_agent.tools.multi_search import MultiSearchTool

        tools = [MultiSearchTool(), SerperDevTool(), ScrapeWebsiteTool()]
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


# Each voice gets a genuinely different model, not just a different prompt on
# the same model (real diversity, zero extra LLM calls) - with a required env
# var per non-OpenAI voice. _llm_for() falls back to OpenAI for a voice whose
# key isn't set, so the crew still runs with only OPENAI_API_KEY configured.
#
# "curious" goes through Gemini's OpenAI-compatible endpoint rather than
# crewai's native Gemini provider: this project's GOOGLE_API_KEY is an
# AI Studio "Auth key" (starts "AQ."), which the native google-genai SDK
# rejects outright ("Invalid authentication credentials"), but the
# OpenAI-compatible endpoint accepts fine - the same approach
# 2_openai/3_lab3.ipynb uses for Gemini.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = "gemini-3.8-flash"

_FALLBACK_LLM = "openai/gpt-4o-mini"
_VOICE_REQUIRED_KEYS = {
    "direct": None,
    "consultative": "ANTHROPIC_API_KEY",
    "curious": "GOOGLE_API_KEY",
}


def _with_runtime_fallback(primary: LLM, fallback: LLM, voice: str) -> LLM:
    """Wrap `primary` so a failure inside .call() - a quota, a retired model
    id, an outage - falls back to `fallback` for that call instead of
    crashing the whole crew. _llm_for()'s key-presence check below only
    catches a voice whose key is simply absent; it can't catch a key that
    exists but whose provider is unavailable right now, which is exactly
    what happened the day Gemini's free-tier daily cap was hit mid-run.

    Subclassing crewai.LLM itself doesn't work: LLM.__new__ is a factory
    that always constructs and returns a concrete provider class (here,
    OpenAICompletion) rather than an instance of cls, so a subclass's own
    __init__/call() would never actually run. This instead subclasses
    OpenAICompletion directly, constructing it with the same kwargs
    LLM.__new__ would have derived for the "openai/<model> + custom
    base_url" shape _llm_for() uses for Gemini - bypassing LLM's factory
    __new__ entirely rather than fighting it.
    """
    try:
        from crewai.llms.providers.openai.completion import OpenAICompletion
        from pydantic import PrivateAttr

        class _FallbackOpenAICompletion(OpenAICompletion):
            _fallback_llm: LLM = PrivateAttr()
            _voice: str = PrivateAttr(default="")

            def call(self, *args, **kwargs):
                try:
                    return super().call(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - must not crash the crew
                    print(
                        f"{self._voice} voice: primary LLM failed ({exc}); "
                        "falling back to OpenAI for this call."
                    )
                    return self._fallback_llm.call(*args, **kwargs)

        wrapped = _FallbackOpenAICompletion(
            model=primary.model,
            provider="openai",
            custom_openai=True,
            base_url=primary.base_url,
            api_key=primary.api_key,
        )
        wrapped._fallback_llm = fallback
        wrapped._voice = voice
        return wrapped
    except Exception:  # noqa: BLE001 - a future crewai internals change must degrade, not crash
        return primary


def _llm_for(voice: str) -> str | LLM:
    required_key = _VOICE_REQUIRED_KEYS[voice]
    if required_key and not os.getenv(required_key):
        return _FALLBACK_LLM
    if voice == "consultative":
        return "anthropic/claude-sonnet-5"
    if voice == "curious":
        gemini = LLM(
            model=f"openai/{GEMINI_MODEL}",
            base_url=GEMINI_BASE_URL,
            api_key=os.environ["GOOGLE_API_KEY"],
        )
        return _with_runtime_fallback(gemini, LLM(model=_FALLBACK_LLM), voice)
    return _FALLBACK_LLM


# One directive per draft variant. Each is appended to the shared
# write_email_task description, so only the voice differs between drafts.
_DRAFT_VOICES = {
    "direct": (
        "Direct and brisk. State the problem in one sentence, give one proof "
        "point, and ask one yes/no question. Make this the shortest of the drafts."
    ),
    "consultative": (
        "Warm and consultative. Open with something specific to their role or "
        "world, share the value as a peer's observation, and offer a short conversation."
    ),
    "curious": (
        "Curious and question-led. Open with a genuine question about how they "
        "handle this problem today, and keep the pitch to a single sentence."
    ),
}


@CrewBase
class OutreachCrew:
    """Research a prospect, draft three voice variants in parallel, and pick the best."""

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

    def _new_copywriter(self, voice: str) -> Agent:
        # The three drafts run concurrently, and a CrewAI agent cannot run two
        # tasks at once, so each draft gets its own instance of the same persona.
        # The role must also differ per instance: @crew de-duplicates agents by
        # role, and a de-duplicated agent never gets wired into the crew.
        config = dict(self.agents_config["sdr_copywriter"])  # type: ignore[index]
        config["role"] = f"{config['role'].strip()} ({voice} voice)"
        return Agent(config=config, llm=_llm_for(voice), knowledge_sources=_playbook_source())

    @agent
    def copywriter_direct(self) -> Agent:
        return self._new_copywriter("direct")

    @agent
    def copywriter_consultative(self) -> Agent:
        return self._new_copywriter("consultative")

    @agent
    def copywriter_curious(self) -> Agent:
        return self._new_copywriter("curious")

    @task
    def research_task(self) -> Task:
        return Task(
            config=self.tasks_config["research_task"],  # type: ignore[index]
            output_pydantic=ProspectIntel,
            guardrail=_research_guardrail,
            guardrail_max_retries=2,
        )

    @agent
    def email_judge(self) -> Agent:
        return Agent(
            config=self.agents_config["email_judge"],  # type: ignore[index]
        )

    def _draft_variant(self, voice: str, writer: Agent) -> Task:
        config = dict(self.tasks_config["write_email_task"])  # type: ignore[index]
        config["description"] = (
            f"{config['description']}\n\n"
            f"Voice for this draft: {voice}. {_DRAFT_VOICES[voice]}\n"
            f'Set the variant field to "{voice}".'
        )
        return Task(
            config=config,
            agent=writer,
            context=[self.research_task()],
            async_execution=True,
            output_pydantic=OutreachEmail,
            guardrail=_email_guardrail,
            guardrail_max_retries=2,
        )

    @task
    def write_email_direct(self) -> Task:
        return self._draft_variant("direct", self.copywriter_direct())

    @task
    def write_email_consultative(self) -> Task:
        return self._draft_variant("consultative", self.copywriter_consultative())

    @task
    def write_email_curious(self) -> Task:
        return self._draft_variant("curious", self.copywriter_curious())

    @task
    def select_email_task(self) -> Task:
        return Task(
            config=self.tasks_config["select_email_task"],  # type: ignore[index]
            context=[
                self.write_email_direct(),
                self.write_email_consultative(),
                self.write_email_curious(),
            ],
            output_pydantic=SelectedEmail,
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
