"""The investigation graph.

    START -> agent -> tools -> agent -> ... -> validate -> END
                  \\-> out_of_budget -> agent      \\-> agent (one repair attempt)

`agent` is Claude with every tool bound: the observability MCP tools and the corpus tools. It loops
through `tools` until it replies without tool calls. Every call carries the `InvestigationReport`
JSON schema as its response format, so that reply is the report as JSON. `validate` parses it with
Pydantic into `state["report"]`; if it fails, the errors go back to the agent once, and a second
failure ends the run with `state["report_error"]`.

After `max_tool_rounds` rounds of tool calls, `out_of_budget` answers the pending calls with "not
run" instead, and the agent gets one more turn to write its report.
"""

from typing import Annotated, Any, Literal, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from incident_agent import prompts
from incident_agent.report import InvestigationReport

# Top-level automatic caching: the API puts the breakpoint on the last block of each request, so
# every call reads the previous call's prefix (tools, system prompt, all earlier turns) from cache.
CACHE_CONTROL = {"type": "ephemeral"}
# On a safety-classifier refusal, the API retries on a substitute model picked by the refusal's
# category, instead of ending the investigation.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Constrained decoding already guarantees the schema, so a failure means a truncated reply
# (max_tokens) or a rule the schema can't express (blank fields). One retry is enough to fix either.
MAX_REPAIRS = 1


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int
    out_of_budget: bool
    repairs: int
    report: InvestigationReport | None
    report_error: str | None


def create_model(model: str, effort: str) -> ChatAnthropic:
    return ChatAnthropic(
        model=model,
        # Room for a long report after adaptive thinking; one call stays well inside the SDK's timeout.
        max_tokens=16000,
        # Summarized so the CLI can show what the agent is weighing between tool calls.
        thinking={"type": "adaptive", "display": "summarized"},
        effort=effort,
        betas=[FALLBACK_BETA],
        model_kwargs={"fallbacks": "default"},
    )


def build_graph(
    model: BaseChatModel, tools: list[BaseTool], mcp_instructions: str | None, max_tool_rounds: int
) -> CompiledStateGraph:
    system = SystemMessage(prompts.SYSTEM.format(
        mcp_instructions=(mcp_instructions or "(The observability server sent no instructions.)").strip()
    ))
    llm = model.bind_tools(tools)

    async def agent(state: State) -> dict[str, Any]:
        # The response format goes on every call, not just the last: it is part of the request, so
        # changing it would break the cached prefix. It only constrains replies without tool calls.
        response = await llm.ainvoke([system, *state["messages"]], cache_control=CACHE_CONTROL,
                                     response_format=InvestigationReport)
        return {"messages": [response], "tool_rounds": state.get("tool_rounds", 0) + bool(response.tool_calls)}

    async def out_of_budget(state: State) -> dict[str, Any]:
        # Every tool_use needs a tool_result before the model can be called again.
        pending = state["messages"][-1].tool_calls
        return {
            "messages": [ToolMessage(prompts.OUT_OF_BUDGET, tool_call_id=c["id"], name=c["name"], status="error")
                         for c in pending],
            "out_of_budget": True,
        }

    def validate(state: State) -> dict[str, Any]:
        reply = state["messages"][-1]
        try:
            return {"report": InvestigationReport.model_validate_json(reply.text), "report_error": None}
        except ValidationError as e:
            stop_reason = reply.response_metadata.get("stop_reason")
            error = f"stop_reason={stop_reason}: {e}" if stop_reason not in (None, "end_turn") else str(e)
            repairs = state.get("repairs", 0)
            if repairs >= MAX_REPAIRS:
                return {"report_error": error}
            return {"messages": [HumanMessage(prompts.REPAIR.format(error=error))],
                    "repairs": repairs + 1, "report_error": error}

    def route_agent(state: State) -> Literal["tools", "out_of_budget", "validate", "__end__"]:
        last = state["messages"][-1]
        if not last.tool_calls:
            return "validate"
        if state.get("out_of_budget"):
            return END
        if state["tool_rounds"] > max_tool_rounds:
            return "out_of_budget"
        return "tools"

    def route_validate(state: State) -> Literal["agent", "__end__"]:
        return "agent" if isinstance(state["messages"][-1], HumanMessage) else END

    graph = StateGraph(State)
    graph.add_node("agent", agent)
    # Runs a round's tool calls concurrently, so parallel queries from one turn run in parallel.
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("out_of_budget", out_of_budget)
    graph.add_node("validate", validate)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_agent)
    graph.add_edge("tools", "agent")
    graph.add_edge("out_of_budget", "agent")
    graph.add_conditional_edges("validate", route_validate)
    return graph.compile()


def outcome(state: dict) -> InvestigationReport:
    """The validated report from a finished run, or a RuntimeError saying why there is none."""
    if state.get("report") is not None:
        return state["report"]
    if state.get("report_error"):
        raise RuntimeError(f"The report failed validation: {state['report_error']}")
    raise RuntimeError("The agent kept calling tools after its tool budget and wrote no report.")
