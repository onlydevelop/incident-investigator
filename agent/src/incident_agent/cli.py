"""Investigate an incident with the observability tools and the incident corpus.

    incident-agent "orders-api is returning 503s"
    incident-agent "positions stopped updating about 20 minutes ago" --verbose
    incident-agent "ticks look stale" --rag-backend memory       # no OpenSearch needed
    incident-agent "ticks look stale" --dry-run                  # approved actions are only validated
    incident-agent "ticks look stale" --no-remediation           # never pause for approval

If the report proposes remediation, the run pauses and asks, for each action, whether to apply it.
Nothing is applied without a "y". When stdin is not a terminal, every action is rejected.

Progress, and the approval prompts, go to stderr. The result goes to stdout as JSON: the run ID, the
report (an `InvestigationReport`) and what happened to each proposed action. `--markdown` renders it
for reading instead.
"""

import argparse
import asyncio
import getpass
import json
import logging
import sys
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from incident_agent import config, prompts
from incident_agent.graph import StepLimitExceeded, build_graph, create_model, outcome, recursion_limit
from incident_agent.knowledge import open_knowledge_base
from incident_agent.mcp_tools import load_connection, open_mcp_tools
from incident_agent.remediation import ApprovalDecision, KubectlCluster, Proposal, RemediationResult
from incident_agent.report import InvestigationReport
from incident_rag.backends import BACKENDS

ARG_CHARS = 160


async def investigate(args: argparse.Namespace) -> int:
    run_id = uuid.uuid4().hex
    cluster = None if args.no_remediation else KubectlCluster(config.NAMESPACE, config.KUBE_CONTEXT, args.dry_run)
    with ExitStack() as stack:
        knowledge = open_knowledge_base(stack, args.rag_backend, config.RAG_STRATEGY, config.CORPUS)
        connection = load_connection(config.MCP_CONFIG, config.MCP_SERVER)
        async with open_mcp_tools(connection, config.MCP_SERVER) as mcp:
            graph = build_graph(
                create_model(args.model, args.effort),
                [*mcp.tools, *knowledge.tools()],
                mcp.instructions,
                args.max_steps,
                cluster,
            )
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            run_config = {"configurable": {"thread_id": run_id}, "recursion_limit": recursion_limit(args.max_steps)}
            _log(f"Run {run_id}")
            state: dict[str, Any] = {}
            usage: list[dict] = []
            inputs: Any = {"messages": [HumanMessage(prompts.TASK.format(question=args.question, now=now))]}
            try:
                # Runs until the graph ends; when it pauses for approval, asks, then resumes it.
                while inputs is not None:
                    paused = None
                    async for update in graph.astream(inputs, run_config, stream_mode="updates"):
                        if "__interrupt__" in update:
                            paused = update["__interrupt__"][0].value
                            continue
                        _show_update(update, state, usage, args.verbose)
                    inputs = None
                    if paused is not None:
                        decision = await _ask_approval(state["report"], paused["proposals"], cluster.dry_run)
                        inputs = Command(resume=decision.model_dump())
            except StepLimitExceeded as e:
                _log(str(e))
                return 1

    _show_usage(usage)
    try:
        report = outcome(state)
    except RuntimeError as e:
        _log(str(e))
        return 1
    if state.get("out_of_budget"):
        _log("(Step budget used up: the report is based on partial evidence.)")
    results = state.get("remediation", [])
    if args.markdown:
        print(report.markdown() + _results_markdown(results))
    else:
        print(json.dumps({"run_id": run_id, "report": report.model_dump(mode="json"),
                          "remediation": [r.model_dump(mode="json") for r in results]}, indent=2))
    return 1 if any(r.status == "failed" for r in results) else 0


def _log(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _show_update(update: dict, state: dict, usage: list[dict], verbose: bool) -> None:
    for node, delta in update.items():
        state.update({k: v for k, v in delta.items() if k != "messages"})
        for message in delta.get("messages", []):
            if isinstance(message, AIMessage):
                usage.append(message.usage_metadata or {})
                _show_ai(message, verbose)
            elif isinstance(message, ToolMessage):
                _show_tool_result(message, verbose)
            elif node == "validate":
                _log(f"  ✗ report failed validation, asking for a corrected one: {state['report_error']}")
        for result in delta.get("remediation", []) if node == "remediate" else []:
            _log(f"  {_result_line(result)}")


async def _ask_approval(report: InvestigationReport, proposals: list[dict], dry_run: bool) -> ApprovalDecision:
    approver = getpass.getuser()
    proposals = [Proposal.model_validate(p) for p in proposals]
    _log(f"\nRoot cause ({report.confidence} confidence): {report.root_cause}\n")
    _log("Proposed remediation" + (" (dry run: validated by the API server, not applied)" if dry_run else "") + ":")
    for i, p in enumerate(proposals, 1):
        _log(f"  {i}. {p.action.describe()}  [key {p.key}]\n     {p.action.reason}")
    if not sys.stdin.isatty():
        _log("stdin is not a terminal, so every action is rejected.")
        return ApprovalDecision(approver=approver, approved=[])
    approved = []
    for i, p in enumerate(proposals, 1):
        # input() blocks; in a thread, the MCP session's reader keeps running meanwhile.
        try:
            answer = await asyncio.to_thread(input, f"Apply {i} ({p.action.describe()})? [y/N] ")
        except EOFError:
            # Ctrl-D, or input ran out: a "no" to this action and every one after it.
            _log("\nEnd of input: rejecting the remaining actions.")
            break
        if answer.strip().lower() in ("y", "yes"):
            approved.append(p.key)
    return ApprovalDecision(approver=approver, approved=approved)


def _result_line(result: RemediationResult) -> str:
    mark = {"applied": "✓", "already_applied": "✓", "rejected": "-", "failed": "✗"}[result.status]
    status = result.status.replace("_", " ") + (" (dry run)" if result.dry_run and result.status == "applied" else "")
    attempts = f", {result.attempts} attempts" if result.attempts > 1 else ""
    detail = f": {result.detail}" if result.detail else ""
    return f"{mark} {result.action.describe()}: {status}{attempts}{detail}"


def _results_markdown(results: list[RemediationResult]) -> str:
    if not results:
        return ""
    return "\n## Remediation\n\n" + "\n".join(f"- {_result_line(r)}" for r in results) + "\n"


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
    parser.add_argument("--max-steps", type=int, default=config.MAX_STEPS,
                        help="model calls per run, the last two kept for the report (default %(default)s)")
    parser.add_argument("--rag-backend", choices=BACKENDS, default=config.RAG_BACKEND)
    remediation = parser.add_mutually_exclusive_group()
    remediation.add_argument("--dry-run", action="store_true",
                             help="apply approved actions with kubectl --dry-run=server: validated, not persisted")
    remediation.add_argument("--no-remediation", action="store_true",
                             help="don't pause for approval; proposals stay in the report")
    parser.add_argument("--markdown", action="store_true", help="print the result as Markdown instead of JSON")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="show the agent's thinking summaries, full tool arguments and results")
    args = parser.parse_args()
    # httpx logs every request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(investigate(args)))


if __name__ == "__main__":
    main()
