# incident-agent

A LangGraph agent that investigates an incident in the stack. It uses two sources:
- **Live telemetry:** the [`observability-mcp`](../observability-mcp/README.md) tools, for Prometheus, Loki and Tempo.
- **Past experience:** the [`incident-corpus`](../incident-corpus/README.md) runbooks and postmortems, through the [`incident-rag`](../rag/README.md) retriever.

It replies with a Markdown report: summary, timeline, evidence, root cause with a confidence level, related documents, and recommended actions.

```sh
make obs-up                           # from the repo root: the telemetry it queries
make -C observability-mcp install     # the MCP server it starts
make -C rag ingest                    # the corpus in OpenSearch (or use --rag-backend memory)
export ANTHROPIC_API_KEY=...

make -C agent install
make -C agent run Q="Positions don't seem to be getting fresh prices"
make -C agent run Q="orders-api is returning 503s" ARGS="--verbose"
make -C agent test                    # scripted model, no API calls
```

Progress goes to stderr and the report to stdout, so `agent/.venv/bin/incident-agent "..." > report.md` keeps only the report:

```text
→ prometheus_alerts {"state": "all"}
→ prometheus_label_values {"label": "__name__", "match": "{__name__=~\"md_.*\"}"}
  ← prometheus_alerts: 2,619 chars
  ...
→ search_incident_docs {"query": "positions not getting fresh prices stale market data pipeline"}
  ...
14 model calls: 596,819 input tokens (525,211 read from cache, ...), 14,204 output tokens
```

## How it works

```text
START → agent ⇄ tools
          │
          ├─ no tool calls ───────────────────────────→ END (the report)
          └─ over the tool budget → out_of_budget → agent → END
```

| Node | What it does |
|---|---|
| `agent` | Claude with every tool bound. The system prompt carries the MCP server's own `instructions` (services, label conventions), so the stack is described in one place |
| `tools` | LangGraph's `ToolNode`. A turn's tool calls run concurrently |
| `out_of_budget` | After `--max-tool-rounds` rounds (default 25), answers the pending calls with "not run, write your report now". The agent then gets one last turn, so a long investigation still ends with a report |

The tools:

| Tool | From | What it does |
|---|---|---|
| `prometheus_*`, `loki_*`, `tempo_*` | observability-mcp, over stdio | The eight read-only tools in [its README](../observability-mcp/README.md#tools). The server is started once per run, from the `observability` entry in [`.mcp.json`](../.mcp.json) |
| `search_incident_docs` | incident-rag | Hybrid search (`opensearch-hybrid`, `heading-merged+ctx`, as recommended in [rag/README.md](../rag/README.md)). Returns sections with their document ID and title. Optional `doc_type` filter: `runbook` or `postmortem` |
| `get_incident_doc` | incident-corpus | One runbook or postmortem in full, with its frontmatter. Fetching the whole document is how the agent gets Triage and Mitigation together, since no single chunk holds both (the "cross-section" result in the RAG eval) |

The system prompt says to use runbooks and postmortems to decide *what to check*, and to adopt a documented cause only once the telemetry confirms it. That matches how the corpus is built: its postmortems share symptoms with new incidents but not causes (see [Keeping evals honest](../incident-corpus/README.md#keeping-evals-honest)).

## Model settings

Set in [`graph.py`](src/incident_agent/graph.py):
- **Model:** `claude-opus-5`, through `langchain-anthropic`.
- **Thinking:** adaptive, with `effort` defaulting to `high`. Summaries of the thinking are returned, and `--verbose` prints them.
- **Prompt caching:** automatic caching (top-level `cache_control`) on every call. The tool definitions and the system prompt never change between runs. The current time goes in the first user message, not the system prompt. So each call reads everything before its newest turn from cache. The run above read 88% of its input from cache.
- **Refusal fallback:** `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`). If a safety classifier declines a turn, the API retries it on a substitute model instead of ending the investigation.

## Configuration

| Variable | Flag | Default |
|---|---|---|
| `INCIDENT_AGENT_MODEL` | `--model` | `claude-opus-5` |
| `INCIDENT_AGENT_EFFORT` | `--effort` | `high` |
| `INCIDENT_AGENT_MAX_TOOL_ROUNDS` | `--max-tool-rounds` | `25` |
| `INCIDENT_AGENT_RAG_BACKEND` | `--rag-backend` | `opensearch-hybrid`. `memory` chunks and embeds the corpus at startup, with no OpenSearch needed |
| `INCIDENT_AGENT_RAG_STRATEGY` | | `heading-merged+ctx` |
| `INCIDENT_AGENT_MCP_CONFIG` | | the repo's `.mcp.json` |
| `INCIDENT_AGENT_CORPUS` | | `../incident-corpus` |

The backend URLs follow the MCP server's rules: `PROMETHEUS_URL`, `LOKI_URL` and `TEMPO_URL` from the environment override `.mcp.json`. `RAG_OPENSEARCH_URL` and `RAG_DATABASE_URL` work as in `incident-rag`.

## Layout

| File | What |
|---|---|
| [`graph.py`](src/incident_agent/graph.py) | The model and the LangGraph state graph |
| [`mcp_tools.py`](src/incident_agent/mcp_tools.py) | Reads `.mcp.json`, starts the server, and keeps one MCP session open for the whole run. Per-call sessions would start a new server process for every tool call |
| [`knowledge.py`](src/incident_agent/knowledge.py) | The two corpus tools on top of an `incident_rag` retriever |
| [`prompts.py`](src/incident_agent/prompts.py) | System prompt, task message, out-of-budget message |
| [`cli.py`](src/incident_agent/cli.py) | `incident-agent`: streams progress, prints the report and token usage |
| [`tests/`](tests) | The graph with a scripted chat model: tool loop, tool budget, cache-stable system prompt, and the exact request payload sent to the API. The corpus tools use a hashing embedder, so no model download. The MCP test starts the real server and runs if it's installed |

The agent's venv uses `mcp` 1.x (through `langchain-mcp-adapters`), while the server runs `mcp` 2.x in its own venv. They talk only over stdio, so the two versions don't conflict.
