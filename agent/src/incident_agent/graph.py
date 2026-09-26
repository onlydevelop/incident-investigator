"""The investigation graph.

    START -> agent -> tools -> agent -> ... -> validate -> approve -> remediate -> END
                  \\-> out_of_budget -> agent      |         (interrupt: a human decides)
                                                   \\-> agent (one repair attempt)

`agent` is Claude with every tool bound: the observability MCP tools and the corpus tools. It loops
through `tools` until it replies without tool calls. Every call carries the `InvestigationReport`
JSON schema as its response format, so that reply is the report as JSON. `validate` parses it with
Pydantic into `state["report"]`; if it fails, the errors go back to the agent once, and a second
failure ends the run with `state["report_error"]`.

Steps: each model call is a step, and a run takes at most `max_steps`. The last two are reserved:
once only those are left, `out_of_budget` answers any pending tool calls with "not run" and the agent
writes its report, with one step left over for a repair.

Remediation: if the report proposes actions and a cluster is configured, `approve` pauses the run
with `interrupt()` and shows the proposals, each with its idempotency key. The caller resumes it with
`Command(resume={"approver": ..., "approved": [keys]})`. `remediate` then applies the approved
actions one by one (see remediation.py for the retries and idempotency) and records the rejected
ones. Without a cluster the proposals stay in the report and the run ends after `validate`.

Pausing needs a checkpointer; the graph is compiled with an in-memory one, and every run needs a
`thread_id` in its config. The thread ID is also the run ID the idempotency keys are derived from.
"""

from typing import Annotated, Any, Literal, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt
from pydantic import ValidationError

from incident_agent import prompts
from incident_agent.remediation import (
    DEFAULT_BACKOFF,
    ApprovalDecision,
    Backoff,
    Cluster,
    Proposal,
    RemediationResult,
    apply,
    proposals_for,
)
from incident_agent.report import Evidence, InvestigationReport, RemediationAction

# Top-level automatic caching: the API puts the breakpoint on the last block of each request, so
# every call reads the previous call's prefix (tools, system prompt, all earlier turns) from cache.
CACHE_CONTROL = {"type": "ephemeral"}
# On a safety-classifier refusal, the API retries on a substitute model picked by the refusal's
# category, instead of ending the investigation.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Constrained decoding already guarantees the schema, so a failure means a truncated reply
# (max_tokens) or a rule the schema can't express (blank fields). One retry is enough to fix either.
MAX_REPAIRS = 1
# Steps kept back from tool use: the report, and one repair of it.
RESERVED_STEPS = 1 + MAX_REPAIRS
# Tool calls run only from steps before the last RESERVED_STEPS + 1, so this allows one round of them.
MIN_STEPS = RESERVED_STEPS + 2
# The Pydantic models the state holds. The checkpointer only restores types it is told about.
STATE_TYPES = (InvestigationReport, Evidence, RemediationAction, Proposal, ApprovalDecision, RemediationResult)


class StepLimitExceeded(RuntimeError):
    pass


class State(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    out_of_budget: bool
    repairs: int
    report: InvestigationReport | None
    report_error: str | None
    proposals: list[Proposal]
    decision: ApprovalDecision | None
    remediation: list[RemediationResult]


def create_model(model: str, effort: str) -> ChatAnthropic:
    return ChatAnthropic(
        model=model,
        # Room for a long report after adaptive thinking; one call stays well inside the SDK's timeout.
        max_tokens=16000,
        # Summarized so the CLI can show what the agent is weighing between tool calls.
        thinking={"type": "adaptive", "display": "summarized"},
        effort=effort,
        # The SDK retries rate limits, 5xx and connection errors with backoff. A model call has no side
        # effects, so a retry needs no idempotency key; only remediation does.
        max_retries=4,
        betas=[FALLBACK_BETA],
        model_kwargs={"fallbacks": "default"},
    )


def create_checkpointer() -> InMemorySaver:
    return InMemorySaver(serde=JsonPlusSerializer().with_msgpack_allowlist(STATE_TYPES))


def build_graph(
    model: BaseChatModel,
    tools: list[BaseTool],
    mcp_instructions: str | None,
    max_steps: int,
    cluster: Cluster | None = None,
    backoff: Backoff = DEFAULT_BACKOFF,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    if max_steps < MIN_STEPS:
        raise ValueError(f"max_steps must be at least {MIN_STEPS}: {RESERVED_STEPS} are kept for the report")
    system = SystemMessage(prompts.SYSTEM.format(
        mcp_instructions=(mcp_instructions or "(The observability server sent no instructions.)").strip()
    ))
    llm = model.bind_tools(tools)

    async def agent(state: State) -> dict[str, Any]:
        steps = state.get("steps", 0)
        # The routing below never gets here past the limit; this makes the limit hard regardless.
        if steps >= max_steps:
            raise StepLimitExceeded(f"step limit of {max_steps} model calls reached")
        # The response format goes on every call, not just the last: it is part of the request, so
        # changing it would break the cached prefix. It only constrains replies without tool calls.
        response = await llm.ainvoke([system, *state["messages"]], cache_control=CACHE_CONTROL,
                                     response_format=InvestigationReport)
        return {"messages": [response], "steps": steps + 1}

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
            if repairs >= MAX_REPAIRS or state["steps"] >= max_steps:
                return {"report_error": error}
            return {"messages": [HumanMessage(prompts.REPAIR.format(error=error))],
                    "repairs": repairs + 1, "report_error": error}

    def approve(state: State, config: RunnableConfig) -> dict[str, Any]:
        # Runs again from the top when the run resumes, so nothing before interrupt() may have effects.
        # The keys come out the same both times, since they depend only on the run and the actions.
        proposals = proposals_for(config["configurable"]["thread_id"], state["report"].remediation)
        answer = interrupt({"proposals": [p.model_dump() for p in proposals]})
        decision = ApprovalDecision.model_validate(answer)
        unknown = set(decision.approved) - {p.key for p in proposals}
        if unknown:
            raise ValueError(f"approved keys that were not proposed: {sorted(unknown)}")
        return {"proposals": proposals, "decision": decision}

    async def remediate(state: State) -> dict[str, Any]:
        decision = state["decision"]
        results = []
        # One at a time, in the proposed order: a restart should land before the next action.
        for proposal in state["proposals"]:
            if proposal.key in decision.approved:
                results.append(await apply(cluster, proposal, decision.approver, backoff))
            else:
                results.append(RemediationResult(key=proposal.key, action=proposal.action, status="rejected",
                                                 dry_run=cluster.dry_run))
        return {"remediation": results}

    def route_agent(state: State) -> Literal["tools", "out_of_budget", "validate", "__end__"]:
        last = state["messages"][-1]
        if not last.tool_calls:
            return "validate"
        if state.get("out_of_budget"):
            return END
        if state["steps"] >= max_steps - RESERVED_STEPS:
            return "out_of_budget"
        return "tools"

    def route_validate(state: State) -> Literal["agent", "approve", "__end__"]:
        if isinstance(state["messages"][-1], HumanMessage):
            return "agent"
        if state.get("report") and state["report"].remediation and cluster is not None:
            return "approve"
        return END

    graph = StateGraph(State)
    graph.add_node("agent", agent)
    # Runs a round's tool calls concurrently, so parallel queries from one turn run in parallel.
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("out_of_budget", out_of_budget)
    graph.add_node("validate", validate)
    graph.add_node("approve", approve)
    graph.add_node("remediate", remediate)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_agent)
    graph.add_edge("tools", "agent")
    graph.add_edge("out_of_budget", "agent")
    graph.add_conditional_edges("validate", route_validate)
    graph.add_edge("approve", "remediate")
    graph.add_edge("remediate", END)
    return graph.compile(checkpointer=checkpointer or create_checkpointer())


def recursion_limit(max_steps: int) -> int:
    """LangGraph's own superstep limit, set above what `max_steps` allows so that ours is the one
    that applies: two supersteps per step (agent, then tools or validate), plus approve and remediate."""
    return 2 * max_steps + 5


def outcome(state: dict) -> InvestigationReport:
    """The validated report from a finished run, or a RuntimeError saying why there is none."""
    if state.get("report") is not None:
        return state["report"]
    if state.get("report_error"):
        raise RuntimeError(f"The report failed validation: {state['report_error']}")
    raise RuntimeError("The agent kept calling tools after its step budget and wrote no report.")
