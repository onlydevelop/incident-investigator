from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from incident_agent import prompts
from incident_agent.graph import CACHE_CONTROL, FALLBACK_BETA, build_graph, create_model


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


def call(name: str, call_id: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def task() -> dict:
    return {"messages": [HumanMessage("orders-api is returning 503s")]}


async def test_loops_through_tools_until_the_model_replies_with_its_report():
    model = ScriptedModel(responses=[
        call("prometheus_query", "c1", query="up"),
        AIMessage(content="## Summary\nPostgres is down."),
    ])
    graph = build_graph(model, [prometheus_query], "Apps: orders-api.", max_tool_rounds=5)

    state = await graph.ainvoke(task())

    assert state["messages"][-1].content == "## Summary\nPostgres is down."
    assert state["tool_rounds"] == 1
    result = state["messages"][2]
    assert isinstance(result, ToolMessage) and result.content == "result of up"
    assert model.bound_tools == [prometheus_query]
    # The second request carries the tool call and its result, under the same system prompt.
    messages, kwargs = model.requests[1]
    assert isinstance(messages[0], SystemMessage) and "Apps: orders-api." in messages[0].content
    assert [type(m) for m in messages[1:]] == [HumanMessage, AIMessage, ToolMessage]
    assert kwargs["cache_control"] == CACHE_CONTROL


async def test_system_prompt_is_the_same_for_every_call_so_it_caches():
    model = ScriptedModel(responses=[call("prometheus_query", "c1", query="up"), AIMessage(content="done")])
    await build_graph(model, [prometheus_query], None, max_tool_rounds=5).ainvoke(task())
    first, second = (messages[0].content for messages, _ in model.requests)
    assert first == second
    assert "no instructions" in first


async def test_out_of_budget_answers_pending_calls_and_asks_for_the_report():
    model = ScriptedModel(responses=[
        call("prometheus_query", "c1", query="a"),
        call("prometheus_query", "c2", query="b"),
        call("prometheus_query", "c3", query="c"),
        AIMessage(content="## Summary\nPartial."),
    ])
    graph = build_graph(model, [prometheus_query], None, max_tool_rounds=2)

    state = await graph.ainvoke(task())

    assert state["out_of_budget"] is True
    assert state["messages"][-1].content == "## Summary\nPartial."
    skipped = state["messages"][-2]
    assert isinstance(skipped, ToolMessage)
    assert skipped.tool_call_id == "c3" and skipped.status == "error" and skipped.content == prompts.OUT_OF_BUDGET
    # Only the two calls within budget ran.
    assert [m.content for m in state["messages"] if isinstance(m, ToolMessage)][:2] == ["result of a", "result of b"]


async def test_ends_if_the_model_keeps_calling_tools_after_the_budget():
    model = ScriptedModel(responses=[call("prometheus_query", f"c{i}", query="a") for i in range(3)])
    state = await build_graph(model, [prometheus_query], None, max_tool_rounds=1).ainvoke(task())
    assert state["out_of_budget"] is True
    assert state["messages"][-1].tool_calls
    assert len(model.requests) == 3


def test_model_request_payload():
    """What ChatAnthropic sends, without sending it."""
    model = create_model("claude-opus-5", "high").bind_tools([prometheus_query])
    payload = model.bound._get_request_payload(
        [SystemMessage("system"), HumanMessage("question")], **model.kwargs, cache_control=CACHE_CONTROL
    )
    assert payload["model"] == "claude-opus-5"
    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert payload["output_config"] == {"effort": "high"}
    assert payload["betas"] == [FALLBACK_BETA]
    assert payload["fallbacks"] == "default"
    assert payload["cache_control"] == CACHE_CONTROL
    assert [t["name"] for t in payload["tools"]] == ["prometheus_query"]
    for sampling in ("temperature", "top_k", "top_p"):
        assert payload.get(sampling) is None
