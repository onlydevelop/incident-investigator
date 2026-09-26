# incident-agent

A LangGraph agent that investigates an incident in the stack. It uses two sources:
- **Live telemetry:** the [`observability-mcp`](../observability-mcp/README.md) tools, for Prometheus, Loki and Tempo.
- **Past experience:** the [`incident-corpus`](../incident-corpus/README.md) runbooks and postmortems, through the [`incident-rag`](../rag/README.md) retriever.

It returns an `InvestigationReport`, a Pydantic model defined in [`report.py`](src/incident_agent/report.py):

| Field | What |
|---|---|
| `root_cause` | The most likely cause, since when, the impact, and the alternatives ruled out |
| `confidence` | `high`, `medium` or `low` |
| `evidence` | At least one `{finding, source, reference}`. `source` is `prometheus`, `loki`, `tempo`, `runbook` or `postmortem`. `reference` is the query, trace ID or document section, so every finding can be checked |
| `next_step` | The single most useful thing to do now, with its command or query |

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

Progress goes to stderr. The report goes to stdout as JSON, or rendered as Markdown with `--markdown`, so `agent/.venv/bin/incident-agent "..." > report.json` keeps only the report. If there is no valid report, it exits 1 and says why.

```text
→ prometheus_alerts {"state": "all"}
→ search_incident_docs {"query": "orders-api slow creating positions high latency"}
  ← prometheus_alerts: 2,619 chars
  ← search_incident_docs: 4,647 chars
  ...
8 model calls: 187,529 input tokens (134,413 read from cache, 53,100 written to cache), 4,253 output tokens
```
```json
{
  "root_cause": "The slowness maps to a window around 01:12–01:41Z in which orders-api's Postgres connection pool was saturated ...",
  "confidence": "medium",
  "evidence": [
    {
      "finding": "orders-api pool was full with a waiter at 01:24–01:27Z: db_pool_in_use=2, db_pool_requests_waiting=1 ...",
      "source": "prometheus",
      "reference": "{__name__=~\"db_pool_.*\", job=\"incident-investigator/orders-api\"} over 00:32–02:32Z"
    },
    ...
  ],
  "next_step": "Read the actual orders-api and Postgres error text for the window to confirm pool exhaustion vs 'too many clients' ..."
}
```

To use it from code, run the graph and call `outcome(state)`. It returns the `InvestigationReport` or raises `RuntimeError`.

## How it works

```text
START → agent ⇄ tools
          │
          ├─ no tool calls → validate ─ valid ──────────→ END (state["report"])
          │                     └─ invalid → agent (once), then END (state["report_error"])
          └─ over the tool budget → out_of_budget → agent
```

| Node | What it does |
|---|---|
| `agent` | Claude with every tool bound. The system prompt carries the MCP server's own `instructions` (services, label conventions), so the stack is described in one place. Every call sets the `InvestigationReport` JSON schema as the response format, so the reply without tool calls is decoded to that schema |
| `tools` | LangGraph's `ToolNode`. A turn's tool calls run concurrently |
| `validate` | Parses the reply with `InvestigationReport.model_validate_json`. The API already guarantees the schema; Pydantic also catches a reply cut off at `max_tokens`, and blank `root_cause` or `next_step`, which JSON schema can't express. On failure the errors go back to the agent for one corrected reply |
| `out_of_budget` | After `--max-tool-rounds` rounds (default 25), answers the pending calls with "not run, write your report now". The agent then gets one last turn, so a long investigation still ends with a report |

The response format goes on every call, not only the last one, because it's part of the request: adding it only at the end would break the cached prefix. It constrains only the reply that doesn't call a tool. The instructions for each field live in the schema's field descriptions, which the model sees. The system prompt doesn't repeat them.

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
- **Prompt caching:** automatic caching (top-level `cache_control`) on every call. The tool definitions and the system prompt never change between runs. The current time goes in the first user message, not the system prompt. So each call reads everything before its newest turn from cache. The run above read 72% of its input from cache; a longer investigation reads more, since each turn adds to a prefix that is already cached.
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
| [`graph.py`](src/incident_agent/graph.py) | The model, the LangGraph state graph, and `outcome()` |
| [`report.py`](src/incident_agent/report.py) | `InvestigationReport` and `Evidence`: the output schema, its validation, and the Markdown rendering |
| [`mcp_tools.py`](src/incident_agent/mcp_tools.py) | Reads `.mcp.json`, starts the server, and keeps one MCP session open for the whole run. Per-call sessions would start a new server process for every tool call |
| [`knowledge.py`](src/incident_agent/knowledge.py) | The two corpus tools on top of an `incident_rag` retriever |
| [`prompts.py`](src/incident_agent/prompts.py) | System prompt, task message, out-of-budget and repair messages |
| [`cli.py`](src/incident_agent/cli.py) | `incident-agent`: streams progress, prints the report as JSON or Markdown, and token usage |
| [`tests/`](tests) | The graph with a scripted chat model: tool loop, tool budget, validation and repair of the report, the same system prompt and response format on every call, and the exact request payload sent to the API, including `output_config.format`. The report model's validation rules and rendering. The corpus tools use a hashing embedder, so no model download. The MCP test starts the real server and runs if it's installed |

The agent's venv uses `mcp` 1.x (through `langchain-mcp-adapters`), while the server runs `mcp` 2.x in its own venv. They talk only over stdio, so the two versions don't conflict.
