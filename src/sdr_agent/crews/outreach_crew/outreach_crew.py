import os

from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.knowledge.source.text_file_knowledge_source import TextFileKnowledgeSource
from crewai.project import CrewBase, agent, crew, task

from sdr_agent.schemas import OutreachEmail, ProspectIntel


def _playbook_source() -> list[TextFileKnowledgeSource]:
    return [TextFileKnowledgeSource(file_paths=["sdr_playbook.md"])]


def _research_tools() -> list:
    tools = []
    if os.getenv("SERPER_API_KEY"):
        from crewai_tools import ScrapeWebsiteTool, SerperDevTool

        tools = [SerperDevTool(), ScrapeWebsiteTool()]
    return tools


def _email_guardrail(output) -> tuple[bool, object]:
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
