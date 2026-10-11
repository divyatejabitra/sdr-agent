"""A tool that runs several web searches in parallel, from one tool call.

Ports the useful part of 2_openai/4_lab4.ipynb's Deep Research pattern
(plan N distinct queries, run them concurrently) without that notebook's
separate planner/searcher/synthesizer agents: those would cost 4-5 extra
LLM calls per lead, including when SERPER_API_KEY isn't set at all, for a
brief no better than the single research_task already produces (see
outreach_crew.py's _research_guardrail, which exists for exactly the
problem a separate planner step would otherwise be solving).

Instead, prospect_researcher gets this as a second tool alongside
SerperDevTool/ScrapeWebsiteTool. The planning happens inside the agent's
existing research_task - it already decides what to look up - it just
issues 2-4 targeted queries in one call instead of one broad query, and
this tool fans them out with a thread pool and returns labeled results.
Zero extra LLM calls; zero cost when SERPER_API_KEY isn't set, because
_research_tools() (outreach_crew.py) simply doesn't hand out this tool then.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class MultiSearchInput(BaseModel):
    queries: list[str] = Field(
        description=(
            "2 to 4 short, distinct search queries that together cover different angles on "
            "the same research goal (e.g. company news, the person's role, a recent signal) "
            "- not restatements of one broad query."
        )
    )


class MultiSearchTool(BaseTool):
    name: str = "multi_search"
    description: str = (
        "Run 2-4 distinct web searches in parallel and get back labeled results for each. "
        "Use this instead of calling the single search tool repeatedly one at a time when you "
        "already know several different things worth checking."
    )
    args_schema: type[BaseModel] = MultiSearchInput

    def _run(self, queries: list[str]) -> str:
        from crewai_tools import SerperDevTool

        serper = SerperDevTool()
        queries = [q.strip() for q in queries if q.strip()][:4]
        if not queries:
            return "No queries given."

        def run_one(query: str) -> tuple[str, str]:
            try:
                return query, str(serper.run(search_query=query))
            except Exception as exc:  # noqa: BLE001 - one query's failure must not sink the others
                return query, f"(this search failed: {exc})"

        with ThreadPoolExecutor(max_workers=len(queries)) as pool:
            results = list(pool.map(run_one, queries))

        return "\n\n".join(f"Query: {query}\nResults:\n{result}" for query, result in results)
