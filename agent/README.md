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
| `remediation` | Proposed changes to the cluster: restart or scale one of the four app deployments. Empty when confidence is low. Nothing in it runs until a human approves it (see [Remediation](#remediation)) |

```sh
make obs-up                           # from the repo root: the telemetry it queries
make -C observability-mcp install     # the MCP server it starts
make -C rag ingest                    # the corpus in OpenSearch (or use --rag-backend memory)
export ANTHROPIC_API_KEY=...

make -C agent install
make -C agent run Q="Positions don't seem to be getting fresh prices"
make -C agent run Q="orders-api is returning 503s" ARGS="--verbose --dry-run"
make -C agent test                    # scripted model and a fake cluster, no API calls
```

Progress and the approval prompts go to stderr. The result goes to stdout as JSON: `run_id`, `report`, and `remediation`, the outcome of each proposed action. `--markdown` renders the same result for reading. The exit code is 1 if there is no valid report, or if an approved action failed.

```text
Run 00683fe3305f4d94875840c426802c4d
→ prometheus_alerts {"state": "all"}
→ search_incident_docs {"query": "delta-ticker symbols_read_failed"}
  ...
  ✗ loki_query: Not run: this investigation has used its tool budget. Write your report now ...

9 model calls: 199,114 input tokens (163,026 read from cache, 36,070 written to cache), 5,868 output tokens
```
```json
{
  "run_id": "00683fe3305f4d94875840c426802c4d",
  "report": {
    "root_cause": "The symbols_read_failed errors were a transient cold-start artefact, not an ongoing feed failure ...",
    "confidence": "high",
    "evidence": [...],
    "next_step": "No restart is warranted. Confirm freshness the way RB-001's Verify section does ...",
    "remediation": []
  },
  "remediation": []
}
```

## How it works

```text
START → agent ⇄ tools
          │
          ├─ no tool calls → validate ─ invalid → agent (once)
          │                     │
          │                     ├─ remediation proposed → approve ⏸ → remediate → END
          │                     └─ otherwise ───────────────────────────────→ END
          └─ out of steps → out_of_budget → agent
```

| Node | What it does |
|---|---|
| `agent` | Claude with every tool bound. The system prompt carries the MCP server's own `instructions` (services, label conventions), so the stack is described in one place. Every call sets the `InvestigationReport` JSON schema as the response format, so the reply without tool calls is decoded to that schema |
| `tools` | LangGraph's `ToolNode`. A turn's tool calls run concurrently |
| `out_of_budget` | Once only the reserved steps are left, answers the pending tool calls with "not run, write your report now" (see [Step limit](#step-limit)) |
| `validate` | Parses the reply with `InvestigationReport.model_validate_json`. The API already guarantees the schema. Pydantic also catches what JSON Schema can't express: a reply cut off at `max_tokens`, a blank field, a remediation proposed at low confidence, a scale with no replica count. On failure the errors go back to the agent for one corrected reply |
| `approve` | Pauses the run with `interrupt()` and shows each proposed action with its idempotency key. The run resumes with the human's decision (see [Remediation](#remediation)) |
| `remediate` | Applies the approved actions one at a time, in the proposed order, with retries. It records the rejected ones as `rejected` |

The response format goes on every call, not only the last one, because it's part of the request: adding it only at the end would break the cached prefix. It constrains only the reply that doesn't call a tool. The instructions for each field live in the schema's field descriptions, which the model sees. The system prompt doesn't repeat them.

The tools:

| Tool | From | What it does |
|---|---|---|
| `prometheus_*`, `loki_*`, `tempo_*` | observability-mcp, over stdio | The eight read-only tools in [its README](../observability-mcp/README.md#tools). The server is started once per run, from the `observability` entry in [`.mcp.json`](../.mcp.json) |
| `search_incident_docs` | incident-rag | Hybrid search (`opensearch-hybrid`, `heading-merged+ctx`, as recommended in [rag/README.md](../rag/README.md)). Returns sections with their document ID and title. Optional `doc_type` filter: `runbook` or `postmortem` |
| `get_incident_doc` | incident-corpus | One runbook or postmortem in full, with its frontmatter. Fetching the whole document is how the agent gets Triage and Mitigation together, since no single chunk holds both (the "cross-section" result in the RAG eval) |

The agent has no tool that changes anything. It can only propose remediation in its report.

The system prompt says to use runbooks and postmortems to decide *what to check*, and to adopt a documented cause only once the telemetry confirms it. That matches how the corpus is built: its postmortems share symptoms with new incidents but not causes (see [Keeping evals honest](../incident-corpus/README.md#keeping-evals-honest)).

## Step limit

A step is one model call, and `--max-steps` (default 30) caps them per run:
- **Reserved steps:** the last two are kept for the report and one repair of it. Once only those are left, `out_of_budget` answers any pending tool calls with "not run", and the agent writes its report from what it has. So a long investigation ends with a report, not an error.
- **What it guarantees:** a run makes at most `max-steps` model calls, whatever the model does. If it still calls tools after being told to stop, the run ends without a report. As a last guard, the agent node raises `StepLimitExceeded` rather than make a call past the limit.
- **Minimum:** `max-steps` must be at least 4, which leaves room for one round of tool calls.
- **LangGraph's own limit:** its `recursion_limit` counts supersteps, not model calls. It's set above what `max-steps` allows, so it never trips first.

## Remediation

1. **Proposal.** The report's `remediation` may list `restart_deployment` or `scale_deployment` on `delta-ticker`, `symbols-api`, `orders-api` or `position-updater`.
   - The schema restricts the targets to these four. The Postgres and Kafka StatefulSets and the exporters are out of reach.
   - Scaling allows 0 to 3 replicas, and at most 1 for `delta-ticker`.
   - With low confidence, validation rejects any proposal.
2. **Approval.** `approve` pauses the run, and the CLI prints the root cause and each proposal with its reason and key. It asks `Apply 1 (restart deployment/orders-api)? [y/N]` for each one. Only `y` approves. When stdin isn't a terminal, every action is rejected. The approver recorded is the local user.
3. **Remediate.** Each approved action becomes one `kubectl patch` on its deployment, retried with backoff on transient errors (see below). Actions run one at a time, in the proposed order.

From code, a paused run returns `state["__interrupt__"]` with the proposals, and you resume it with `graph.ainvoke(Command(resume={"approver": "...", "approved": [keys]}), config)`. Pausing needs a checkpointer and a `thread_id`. The graph is compiled with an in-memory checkpointer that allowlists the state's Pydantic types.

### Idempotency keys and retries

Each action's key is a hash of the run ID and the action itself (kind, deployment, replicas), not the reason's wording. The patch records the key, so applying the same action again changes nothing:
- **Restart:** the key goes into the pod template's annotations (`incident-agent/restart-key`), the way `kubectl rollout restart` writes a timestamp. A new key changes the template and starts a rollout. The same key again leaves the template unchanged, so Kubernetes starts nothing.
- **Scale:** it sets an absolute replica count, which is the same the second time.
- **Record:** both write the key, the approver and the time to the deployment's annotations (`incident-agent/remediation-key`, `approved-by`, `applied-at`), which is also an audit trail on the object itself.

Failures are handled as follows:
- **Transient** (API server unreachable, timeouts, "the object has been modified" conflicts): up to 4 attempts with the same key, 1 s, 2 s and 4 s apart.
- **Permanent** (`NotFound`, `forbidden`, an invalid patch): fails at once.
- **A lost response:** before each attempt, the deployment's recorded key is checked. If the patch landed but its response didn't come back, the retry reports `already_applied` instead of patching again. The same holds if the remediation step is replayed.
- **A new run** gets new keys, so a later incident can restart the same deployment again.

`--dry-run` sends the patches with `kubectl --dry-run=server`: the API server validates and admits them, but persists nothing. `--no-remediation` never pauses, and the proposals stay in the report. Model calls are retried by the Anthropic SDK (`max_retries=4`). They have no side effects, so they need no idempotency key.

## Model settings

Set in [`graph.py`](src/incident_agent/graph.py):
- **Model:** `claude-opus-5`, through `langchain-anthropic`.
- **Thinking:** adaptive, with `effort` defaulting to `high`. Summaries of the thinking are returned, and `--verbose` prints them.
- **Prompt caching:** automatic caching (top-level `cache_control`) on every call. The tool definitions and the system prompt never change between runs. The current time goes in the first user message, not the system prompt. So each call reads everything before its newest turn from cache. The run above read 82% of its input from cache.
- **Refusal fallback:** `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`). If a safety classifier declines a turn, the API retries it on a substitute model instead of ending the investigation.

## Configuration

| Variable | Flag | Default |
|---|---|---|
| `INCIDENT_AGENT_MODEL` | `--model` | `claude-opus-5` |
| `INCIDENT_AGENT_EFFORT` | `--effort` | `high` |
| `INCIDENT_AGENT_MAX_STEPS` | `--max-steps` | `30` |
| | `--dry-run` / `--no-remediation` | apply approved actions for real |
| `INCIDENT_AGENT_NAMESPACE` | | `incident-investigator` |
| `INCIDENT_AGENT_KUBE_CONTEXT` | | kubectl's current context |
| `INCIDENT_AGENT_RAG_BACKEND` | `--rag-backend` | `opensearch-hybrid`. `memory` chunks and embeds the corpus at startup, with no OpenSearch needed |
| `INCIDENT_AGENT_RAG_STRATEGY` | | `heading-merged+ctx` |
| `INCIDENT_AGENT_MCP_CONFIG` | | the repo's `.mcp.json` |
| `INCIDENT_AGENT_CORPUS` | | `../incident-corpus` |

The backend URLs follow the MCP server's rules: `PROMETHEUS_URL`, `LOKI_URL` and `TEMPO_URL` from the environment override `.mcp.json`. `RAG_OPENSEARCH_URL` and `RAG_DATABASE_URL` work as in `incident-rag`.

## Layout

| File | What |
|---|---|
| [`graph.py`](src/incident_agent/graph.py) | The model, the LangGraph state graph with the step limit, approval and remediation nodes, and `outcome()` |
| [`report.py`](src/incident_agent/report.py) | `InvestigationReport`, `Evidence` and `RemediationAction`: the output schema, its validation, and the Markdown rendering |
| [`remediation.py`](src/incident_agent/remediation.py) | Idempotency keys, the patches, retries with backoff, and `KubectlCluster` |
| [`mcp_tools.py`](src/incident_agent/mcp_tools.py) | Reads `.mcp.json`, starts the server, and keeps one MCP session open for the whole run. Per-call sessions would start a new server process for every tool call |
| [`knowledge.py`](src/incident_agent/knowledge.py) | The two corpus tools on top of an `incident_rag` retriever |
| [`prompts.py`](src/incident_agent/prompts.py) | System prompt, task message, out-of-budget and repair messages |
| [`cli.py`](src/incident_agent/cli.py) | `incident-agent`: streams progress, asks for approval, prints the result as JSON or Markdown, and token usage |
| [`tests/`](tests) | See below |

The tests:
- **Graph:** runs with a scripted chat model and a fake cluster:
  - the tool loop, and validation and repair of the report;
  - the step limit, including that no run makes more than `max_steps` calls;
  - pausing for approval, applying only what was approved, and a new key per run;
  - the same system prompt and response format on every call, and the exact request payload.
- **Remediation:** the keys and patches; retries with the same key; a lost response not applied twice; giving up; permanent errors; kubectl error classification.
- **Against real servers:**
  - the MCP test starts the real server, if it's installed;
  - the `k8s` test sends a patch to the real API server, and runs only if the cluster is reachable. It only ever uses `--dry-run=server`, so it changes nothing.
- **Corpus tools:** use a hashing embedder, so there's no model download.

The agent's venv uses `mcp` 1.x (through `langchain-mcp-adapters`), while the server runs `mcp` 2.x in its own venv. They talk only over stdio, so the two versions don't conflict.
