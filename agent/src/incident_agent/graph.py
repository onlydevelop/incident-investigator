"""The investigation graph.

    START -> agent -> tools -> agent -> ... -> END
                  \\-> out_of_budget -> agent -> END

`agent` is Claude with every tool bound: the observability MCP tools and the corpus tools. It loops
through `tools` until it replies without tool calls, which is its report. After `max_tool_rounds`
rounds of tool calls, `out_of_budget` answers the pending calls with "not run" instead, and the
agent gets one more turn to write its report.
"""

from typing import Annotated, Any, Literal, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from incident_agent import prompts

# Top-level automatic caching: the API puts the breakpoint on the last block of each request, so
# every call reads the previous call's prefix (tools, system prompt, all earlier turns) from cache.
CACHE_CONTROL = {"type": "ephemeral"}
# On a safety-classifier refusal, the API retries on a substitute model picked by the refusal's
# category, instead of ending the investigation.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int
    out_of_budget: bool


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
        response = await llm.ainvoke([system, *state["messages"]], cache_control=CACHE_CONTROL)
        return {"messages": [response], "tool_rounds": state.get("tool_rounds", 0) + bool(response.tool_calls)}

    async def out_of_budget(state: State) -> dict[str, Any]:
        # Every tool_use needs a tool_result before the model can be called again.
        pending = state["messages"][-1].tool_calls
        return {
            "messages": [ToolMessage(prompts.OUT_OF_BUDGET, tool_call_id=c["id"], name=c["name"], status="error")
                         for c in pending],
            "out_of_budget": True,
        }

    def route(state: State) -> Literal["tools", "out_of_budget", "__end__"]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls or state.get("out_of_budget"):
            return END
        if state["tool_rounds"] > max_tool_rounds:
            return "out_of_budget"
        return "tools"

    graph = StateGraph(State)
    graph.add_node("agent", agent)
    # Runs a round's tool calls concurrently, so parallel queries from one turn run in parallel.
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("out_of_budget", out_of_budget)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route)
    graph.add_edge("tools", "agent")
    graph.add_edge("out_of_budget", "agent")
    return graph.compile()
