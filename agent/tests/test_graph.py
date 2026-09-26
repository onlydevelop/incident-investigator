import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.types import Command
from pydantic import Field

from incident_agent import prompts
from incident_agent.graph import CACHE_CONTROL, FALLBACK_BETA, build_graph, create_model, outcome
from incident_agent.remediation import KEY_ANNOTATION, Backoff, idempotency_key
from incident_agent.report import InvestigationReport, RemediationAction

from fakes import FakeCluster


class ScriptedModel(BaseChatModel):
    """Replies with `responses` in order and records every request. Each response must be its own
    message: add_messages would merge a repeated one into the earlier copy."""

    responses: list[AIMessage]
    requests: list[tuple[list, dict]] = Field(default_factory=list)
    bound_tools: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = list(tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        self.requests.append((messages, kwargs))
        index = min(len(self.requests), len(self.responses)) - 1
        return ChatResult(generations=[ChatGeneration(message=self.responses[index])])


@tool
def prometheus_query(query: str) -> str:
    """Evaluates PromQL."""
    return f"result of {query}"


RESTART = {"kind": "restart_deployment", "deployment": "orders-api", "replicas": None,
           "reason": "RB-005: restart to release the leaked pools."}
SCALE = {"kind": "scale_deployment", "deployment": "position-updater", "replicas": 2, "reason": "RB-002."}
REPORT = {
    "root_cause": "Postgres is down since 01:22 UTC, so orders-api returns 503.",
    "confidence": "high",
    "evidence": [{"finding": "pg_up is 0 since 01:22.", "source": "prometheus", "reference": "pg_up"}],
    "next_step": "kubectl -n incident-investigator get pod postgres-0 (RB-005).",
    "remediation": [],
}
NO_DELAY = Backoff(initial_delay=0)


def call(name: str, call_id: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def reply(report: dict | str = REPORT, stop_reason: str = "end_turn") -> AIMessage:
    text = report if isinstance(report, str) else json.dumps(report)
    return AIMessage(content=text, response_metadata={"stop_reason": stop_reason})


def task() -> dict:
    return {"messages": [HumanMessage("orders-api is returning 503s")]}


def run_config(run_id: str = "run-1") -> dict:
    return {"configurable": {"thread_id": run_id}}


def graph_for(model, max_steps: int = 10, **kwargs):
    return build_graph(model, [prometheus_query], kwargs.pop("instructions", None), max_steps, backoff=NO_DELAY,
                       **kwargs)


# --- The investigation loop ---


async def test_loops_through_tools_until_the_model_replies_with_its_report():
    model = ScriptedModel(responses=[call("prometheus_query", "c1", query="up"), reply()])
    graph = graph_for(model, instructions="Apps: orders-api.")

    state = await graph.ainvoke(task(), run_config())

    assert state["report"] == InvestigationReport.model_validate(REPORT)
    assert outcome(state).confidence == "high"
    assert state["steps"] == 2
    result = state["messages"][2]
    assert isinstance(result, ToolMessage) and result.content == "result of up"
    assert model.bound_tools == [prometheus_query]
    # The second request carries the tool call and its result, under the same system prompt.
    messages, kwargs = model.requests[1]
    assert isinstance(messages[0], SystemMessage) and "Apps: orders-api." in messages[0].content
    assert [type(m) for m in messages[1:]] == [HumanMessage, AIMessage, ToolMessage]
    assert kwargs["cache_control"] == CACHE_CONTROL


async def test_every_call_has_the_same_system_prompt_and_response_format_so_it_caches():
    model = ScriptedModel(responses=[call("prometheus_query", "c1", query="up"), reply()])
    await graph_for(model).ainvoke(task(), run_config())
    (first, first_kwargs), (second, second_kwargs) = model.requests
    assert first[0].content == second[0].content
    assert "no instructions" in first[0].content
    assert first_kwargs["response_format"] is second_kwargs["response_format"] is InvestigationReport


async def test_an_invalid_report_gets_one_repair_turn():
    truncated = json.dumps(REPORT)[:60]
    model = ScriptedModel(responses=[reply(truncated, stop_reason="max_tokens"), reply()])

    state = await graph_for(model).ainvoke(task(), run_config())

    assert outcome(state) == InvestigationReport.model_validate(REPORT)
    assert state["repairs"] == 1 and state["report_error"] is None
    repair = model.requests[1][0][-1]
    assert isinstance(repair, HumanMessage)
    assert "stop_reason=max_tokens" in repair.content and "Invalid JSON" in repair.content


async def test_a_second_invalid_report_ends_the_run():
    model = ScriptedModel(responses=[reply({**REPORT, "next_step": " "}), reply({**REPORT, "root_cause": ""})])

    state = await graph_for(model).ainvoke(task(), run_config())

    assert len(model.requests) == 2 and state.get("report") is None
    with pytest.raises(RuntimeError, match="root_cause"):
        outcome(state)


# --- The step limit ---


async def test_the_last_two_steps_are_kept_for_the_report():
    model = ScriptedModel(responses=[
        call("prometheus_query", "c1", query="a"),
        call("prometheus_query", "c2", query="b"),
        call("prometheus_query", "c3", query="c"),
        reply({**REPORT, "confidence": "low"}),
    ])
    state = await graph_for(model, max_steps=5).ainvoke(task(), run_config())

    assert state["out_of_budget"] is True and state["steps"] == 4
    assert outcome(state).confidence == "low"
    skipped = state["messages"][-2]
    assert isinstance(skipped, ToolMessage)
    assert skipped.tool_call_id == "c3" and skipped.status == "error" and skipped.content == prompts.OUT_OF_BUDGET
    # Only the two calls from steps 1 and 2 ran.
    assert [m.content for m in state["messages"] if isinstance(m, ToolMessage)][:2] == ["result of a", "result of b"]


async def test_a_run_never_takes_more_than_max_steps_model_calls():
    # Keeps calling tools, then writes an invalid report and an invalid repair.
    responses = [call("prometheus_query", f"c{i}", query="a") for i in range(3)]
    for max_steps in (4, 5, 8):
        model = ScriptedModel(responses=[*responses, reply("{"), reply("{")])
        state = await graph_for(model, max_steps=max_steps).ainvoke(task(), run_config(f"run-{max_steps}"))
        assert len(model.requests) <= max_steps
        with pytest.raises(RuntimeError):
            outcome(state)


async def test_no_repair_when_the_report_used_the_last_step():
    model = ScriptedModel(responses=[
        call("prometheus_query", "c1", query="a"), call("prometheus_query", "c2", query="b"),
        reply("{"), reply(),
    ])
    state = await graph_for(model, max_steps=4).ainvoke(task(), run_config())
    # Step 1 ran a tool, step 2's calls were answered "not run", step 3 was the report, step 4 the repair.
    assert len(model.requests) == 4 and outcome(state).confidence == "high"


async def test_ends_without_a_report_if_the_model_keeps_calling_tools_after_the_budget():
    model = ScriptedModel(responses=[call("prometheus_query", f"c{i}", query="a") for i in range(4)])
    state = await graph_for(model, max_steps=4).ainvoke(task(), run_config())
    assert state["out_of_budget"] is True and state["messages"][-1].tool_calls
    assert len(model.requests) == 3
    with pytest.raises(RuntimeError, match="wrote no report"):
        outcome(state)


def test_rejects_a_step_limit_too_small_for_the_report():
    with pytest.raises(ValueError, match="at least 4"):
        graph_for(ScriptedModel(responses=[reply()]), max_steps=3)


# --- Approval and remediation ---


async def test_the_state_survives_the_checkpoint_without_serializer_warnings(caplog):
    """Resuming restores the state from the checkpoint; every type in it must be on the allowlist."""
    cluster = FakeCluster()
    model = ScriptedModel(responses=[call("prometheus_query", "c1", query="up"),
                                     reply({**REPORT, "remediation": [RESTART]})])
    graph = graph_for(model, cluster=cluster)
    paused = await graph.ainvoke(task(), run_config())
    key = paused["__interrupt__"][0].value["proposals"][0]["key"]

    with caplog.at_level("WARNING"):
        state = await graph.ainvoke(Command(resume={"approver": "d", "approved": [key]}), run_config())

    assert not [r for r in caplog.records if "unregistered" in r.message or "block" in r.message.lower()]
    assert isinstance(state["report"], InvestigationReport)
    assert state["messages"][1].tool_calls[0]["name"] == "prometheus_query"
    assert [r.status for r in state["remediation"]] == ["applied"]


async def test_pauses_for_approval_and_applies_only_what_was_approved():
    cluster = FakeCluster()
    model = ScriptedModel(responses=[reply({**REPORT, "remediation": [RESTART, SCALE]})])
    graph = graph_for(model, cluster=cluster)

    paused = await graph.ainvoke(task(), run_config())

    # Nothing has touched the cluster yet; the proposals carry their idempotency keys.
    assert cluster.patches == []
    [pause] = paused["__interrupt__"]
    restart, scale = pause.value["proposals"]
    assert restart["key"] == idempotency_key("run-1", RemediationAction.model_validate(RESTART))
    assert scale["action"]["replicas"] == 2

    decision = {"approver": "dipanjan", "approved": [restart["key"]]}
    state = await graph.ainvoke(Command(resume=decision), run_config())

    applied, rejected = state["remediation"]
    assert (applied.status, applied.attempts, applied.key) == ("applied", 1, restart["key"])
    assert rejected.status == "rejected" and rejected.attempts == 0
    assert cluster.rollouts == ["orders-api"] and "position-updater" not in cluster.deployments
    annotations = cluster.deployments["orders-api"]["annotations"]
    assert annotations[KEY_ANNOTATION] == restart["key"] and annotations["incident-agent/approved-by"] == "dipanjan"


async def test_rejecting_everything_changes_nothing():
    cluster = FakeCluster()
    graph = graph_for(ScriptedModel(responses=[reply({**REPORT, "remediation": [RESTART]})]), cluster=cluster)
    await graph.ainvoke(task(), run_config())
    state = await graph.ainvoke(Command(resume={"approver": "dipanjan", "approved": []}), run_config())
    assert [r.status for r in state["remediation"]] == ["rejected"] and cluster.patches == []


async def test_approving_a_key_that_was_not_proposed_is_an_error():
    graph = graph_for(ScriptedModel(responses=[reply({**REPORT, "remediation": [RESTART]})]), cluster=FakeCluster())
    await graph.ainvoke(task(), run_config())
    with pytest.raises(ValueError, match="not proposed"):
        await graph.ainvoke(Command(resume={"approver": "dipanjan", "approved": ["0123456789abcdef"]}), run_config())


async def test_no_pause_without_proposals_or_without_a_cluster():
    for report, cluster in ((REPORT, FakeCluster()), ({**REPORT, "remediation": [RESTART]}, None)):
        state = await graph_for(ScriptedModel(responses=[reply(report)]), cluster=cluster).ainvoke(task(), run_config())
        assert "__interrupt__" not in state and "remediation" not in state
        assert outcome(state).remediation == InvestigationReport.model_validate(report).remediation


async def test_the_same_action_in_a_new_run_gets_a_new_key():
    cluster = FakeCluster()
    for run_id in ("run-1", "run-2"):
        graph = graph_for(ScriptedModel(responses=[reply({**REPORT, "remediation": [RESTART]})]), cluster=cluster)
        paused = await graph.ainvoke(task(), run_config(run_id))
        key = paused["__interrupt__"][0].value["proposals"][0]["key"]
        await graph.ainvoke(Command(resume={"approver": "d", "approved": [key]}), run_config(run_id))
    # A later incident may need the same restart again.
    assert cluster.rollouts == ["orders-api", "orders-api"]


def test_model_request_payload():
    """What ChatAnthropic sends, without sending it."""
    model = create_model("claude-opus-5", "high").bind_tools([prometheus_query])
    payload = model.bound._get_request_payload(
        [SystemMessage("system"), HumanMessage("question")], **model.kwargs,
        cache_control=CACHE_CONTROL, response_format=InvestigationReport,
    )
    assert payload["model"] == "claude-opus-5"
    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert payload["output_config"]["effort"] == "high"
    output_format = payload["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    schema = output_format["schema"]
    assert schema["required"] == ["root_cause", "confidence", "evidence", "next_step", "remediation"]
    action = schema["$defs"]["RemediationAction"]
    assert action["properties"]["deployment"]["enum"] == ["delta-ticker", "symbols-api", "orders-api", "position-updater"]
    assert set(action["required"]) == {"kind", "deployment", "replicas", "reason"}
    assert payload["betas"] == [FALLBACK_BETA]
    assert payload["fallbacks"] == "default"
    assert payload["cache_control"] == CACHE_CONTROL
    assert [t["name"] for t in payload["tools"]] == ["prometheus_query"]
    for sampling in ("temperature", "top_k", "top_p"):
        assert payload.get(sampling) is None
