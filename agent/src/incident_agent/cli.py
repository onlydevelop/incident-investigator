"""Investigate an incident with the observability tools and the incident corpus.

    incident-agent "orders-api is returning 503s"
    incident-agent "positions stopped updating about 20 minutes ago" --verbose
    incident-agent "ticks look stale" --rag-backend memory       # no OpenSearch needed

Progress (tool calls and their results) goes to stderr, and the report to stdout as JSON
(an `InvestigationReport`), so `incident-agent "..." > report.json` keeps just the report.
`--markdown` renders it for reading instead.
"""

import argparse
import asyncio
import json
import logging
import sys
from contextlib import ExitStack
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from incident_agent import config, prompts
from incident_agent.graph import build_graph, create_model, outcome
from incident_agent.knowledge import open_knowledge_base
from incident_agent.mcp_tools import load_connection, open_mcp_tools
from incident_rag.backends import BACKENDS

ARG_CHARS = 160


async def investigate(args: argparse.Namespace) -> int:
    with ExitStack() as stack:
        knowledge = open_knowledge_base(stack, args.rag_backend, config.RAG_STRATEGY, config.CORPUS)
        connection = load_connection(config.MCP_CONFIG, config.MCP_SERVER)
        async with open_mcp_tools(connection, config.MCP_SERVER) as mcp:
            graph = build_graph(
                create_model(args.model, args.effort),
                [*mcp.tools, *knowledge.tools()],
                mcp.instructions,
                args.max_tool_rounds,
            )
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            task = HumanMessage(prompts.TASK.format(question=args.question, now=now))
            # Two supersteps per round (agent, tools), plus the wrap-up and a repair.
            limit = 2 * args.max_tool_rounds + 10
            state, usage = {}, []
            async for update in graph.astream({"messages": [task]}, {"recursion_limit": limit},
                                              stream_mode="updates"):
                for node, delta in update.items():
                    state.update({k: v for k, v in delta.items() if k != "messages"})
                    for message in delta.get("messages", []):
                        if isinstance(message, AIMessage):
                            usage.append(message.usage_metadata or {})
                            _show_ai(message, args.verbose)
                        elif isinstance(message, ToolMessage):
                            _show_tool_result(message, args.verbose)
                        elif node == "validate":
                            _log(f"  ✗ report failed validation, asking for a corrected one: {state['report_error']}")

    _show_usage(usage)
    try:
        report = outcome(state)
    except RuntimeError as e:
        _log(str(e))
        return 1
    if state.get("out_of_budget"):
        _log("(Tool budget used up: the report is based on partial evidence.)")
    print(report.markdown() if args.markdown else report.model_dump_json(indent=2))
    return 0


def _log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _show_ai(message: AIMessage, verbose: bool) -> None:
    if verbose:
        for block in message.content if isinstance(message.content, list) else []:
            if isinstance(block, dict) and block.get("type") == "thinking" and block.get("thinking"):
                _log(f"  … {block['thinking'].strip()}")
    for call in message.tool_calls:
        arguments = json.dumps(call["args"], ensure_ascii=False)
        if not verbose and len(arguments) > ARG_CHARS:
            arguments = arguments[:ARG_CHARS] + "…"
        _log(f"→ {call['name']} {arguments}")


def _show_tool_result(message: ToolMessage, verbose: bool) -> None:
    text = message.text
    if message.status == "error":
        _log(f"  ✗ {message.name}: {text[:300]}")
    elif verbose:
        _log(f"  ← {message.name}: {text[:1000]}")
    else:
        _log(f"  ← {message.name}: {len(text):,} chars")


def _show_usage(usage: list[dict]) -> None:
    def total(key: str, records) -> int:
        return sum(r.get(key) or 0 for r in records)

    details = [u.get("input_token_details") or {} for u in usage]
    # langchain-anthropic reports cache writes per TTL when the API breaks them down, and then zeroes
    # `cache_creation`, so the three never overlap.
    written = sum(total(k, details) for k in ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens"))
    _log(
        f"\n{len(usage)} model calls: {total('input_tokens', usage):,} input tokens "
        f"({total('cache_read', details):,} read from cache, {written:,} written to cache), "
        f"{total('output_tokens', usage):,} output tokens\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question", help="what's wrong, as you'd describe it in the incident channel")
    parser.add_argument("--model", default=config.MODEL)
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=config.EFFORT)
    parser.add_argument("--max-tool-rounds", type=int, default=config.MAX_TOOL_ROUNDS)
    parser.add_argument("--rag-backend", choices=BACKENDS, default=config.RAG_BACKEND)
    parser.add_argument("--markdown", action="store_true", help="print the report as Markdown instead of JSON")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show the agent's thinking summaries, full tool arguments and results")
    args = parser.parse_args()
    # httpx logs every request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(investigate(args)))


if __name__ == "__main__":
    main()
