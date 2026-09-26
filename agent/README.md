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

## Getting started

Every command runs from the repo root, in this order. The first run takes a while: it builds two images, installs four Helm charts, and downloads the embedding model (about 130 MB).

### 0. Prerequisites

- **Rancher Desktop** with Kubernetes enabled and the Docker engine (moby) selected. The kube context must be `rancher-desktop` (see [deploy/k8s/README.md](../deploy/k8s/README.md#0-prerequisites)).
- **Helm**, for the observability stack.
- **Python 3.11 or later.**
- **An Anthropic API key.**

```sh
kubectl config current-context        # rancher-desktop
helm version --short
python3 --version
```

### 1. Start the observability stack

Prometheus, Grafana, Loki, Tempo and the OTel Collector, in the `observability` namespace. Install it before the app stack, so that `k8s-start` also adds the exporters' PodMonitor.

```sh
make obs-up
curl -s http://prometheus.localhost/-/ready     # Prometheus Server is Ready.
```

### 2. Start the app stack

delta-ticker, symbols-api, orders-api and position-updater, with their Postgres, Redis and Kafka, in the `incident-investigator` namespace. Images are built on the first run.

```sh
make k8s-start
make k8s-status
```

An idle stack has little to investigate. To get ticks flowing, open a paper position as in [step 2 of the k3s walkthrough](../deploy/k8s/README.md#2-create-positions). Creating a position also subscribes its symbol, so delta-ticker starts streaming it.

### 3. Install the MCP server

The agent starts it itself, from the `observability` entry in [`.mcp.json`](../.mcp.json).

```sh
make -C observability-mcp install
make -C observability-mcp test        # its live tests run too, now that the stack is up
```

### 4. Load the incident corpus

This starts Postgres (with pgvector) and OpenSearch with Docker Compose from [`infra/`](../infra/README.md). It then chunks and embeds the runbooks and postmortems, and loads them into both stores. OpenSearch uses about 1 GB of memory. To skip it, leave this step out and run the agent with `ARGS="--rag-backend memory"`.

```sh
make -C rag ingest                    # heading-merged+ctx: 45 chunks from 20 documents -> pgvector, opensearch
make -C rag search Q="connected but no ticks received"
```

### 5. Install the agent

```sh
make -C agent install
make -C agent test                    # scripted model and a fake cluster; no API calls, no cluster changes
export ANTHROPIC_API_KEY=...
```

### 6. Investigate

Try a dry run first. Actions you approve are then sent with `kubectl --dry-run=server`: the API server checks them, but nothing changes.

```sh
make -C agent run Q="Positions stopped getting new prices about half an hour ago" ARGS="--dry-run --markdown"
```

Then run without `--dry-run`. An approved action is then applied for real:

```sh
make -C agent run Q="orders-api is returning 503s"
make -C agent run Q="orders-api is returning 503s" ARGS="--verbose"          # also show thinking and tool results
make -C agent run Q="orders-api is returning 503s" ARGS="--no-remediation"   # never pause for approval
```

### 7. Stop it all

```sh
make k8s-stop                         # the app stack scales to 0; data is kept
make -C infra search-stop             # OpenSearch; `make -C infra down` stops Postgres too
```

## Example session

This session is **scripted**, and no incident happened. It was produced by running the real CLI with a scripted model, canned tool results and a fake cluster, driven through a pseudo-terminal. Every line is exactly what the CLI prints, and the tool calls, the approval prompt and the result are in their real formats. The incident itself, delta-ticker's websocket going half-open (RB-001's "connected but no ticks received" case), and all its numbers are made up.

**The question:**

```sh
make -C agent run Q="Positions stopped getting new prices about half an hour ago" ARGS="--markdown"
```

**The investigation**, on stderr. The agent checks the alerts and searches the corpus, confirms RB-001's signals in Prometheus and Loki, and reads RB-001 in full. It then stops to ask before touching the cluster. The operator types `y`:

```text
Run 5f0c2a9e7b3d4e1f8a6b9c0d1e2f3a4b
→ prometheus_alerts {"state": "firing"}
→ search_incident_docs {"query": "positions current_price not updating stale market data"}
  ← prometheus_alerts: 363 chars
  ← search_incident_docs: 425 chars
→ prometheus_query {"query": "md_ws_connected"}
→ prometheus_query_range {"query": "sum(rate(md_ticks_received_total[1m]))", "start": "1h"}
→ prometheus_query {"query": "max by (symbol) (md_tick_age_seconds)"}
→ prometheus_query {"query": "increase(kube_pod_container_status_restarts_total{container=\"delta-ticker\"}[30m])"}
  ← prometheus_query: 161 chars
  ← prometheus_query_range: 335 chars
  ← prometheus_query: 270 chars
  ← prometheus_query: 116 chars
→ loki_query {"query": "{service_name=\"delta-ticker\"} | json | event=~\"ws_closed|ws_error|symbols_read_failed|no_symbols|ws_message\"", "start": "6h"}
→ get_incident_doc {"doc_id": "RB-001"}
  ← loki_query: 593 chars
  ← get_incident_doc: 3,258 chars

Root cause (high confidence): delta-ticker's websocket to Delta went half-open at about 09:13 UTC: md_ws_connected has stayed 1 and the pod has not restarted, but md_ticks_received_total has been flat since 09:13, so for 28 minutes no ticks have reached Redis or Kafka. Every open position's current_price is frozen and new positions stay pending. This is RB-001's 'connected but no ticks received' case: the client sends no pings, so the dead connection is never noticed (PM-003). Ruled out: an exited process (no restarts, metrics present), expired symbols (both expire 9 Oct 2026 and both went stale at once), and Kafka or consumer problems (nothing is being produced to fall behind on).

Proposed remediation:
  1. restart deployment/delta-ticker  [key d1dc94ded647a3fc]
     RB-001 > Mitigation: a half-open socket is cleared by restarting the ticker.
Apply 1 (restart deployment/delta-ticker)? [y/N] y
  ✓ restart deployment/delta-ticker: applied

4 model calls: 79,420 input tokens (53,440 read from cache, 25,820 written to cache), 2,760 output tokens
```

**The response**, on stdout. Without `--markdown`, this is the same result as JSON:

```markdown
## Root cause (high confidence)

delta-ticker's websocket to Delta went half-open at about 09:13 UTC: md_ws_connected has stayed 1 and the pod has not restarted, but md_ticks_received_total has been flat since 09:13, so for 28 minutes no ticks have reached Redis or Kafka. Every open position's current_price is frozen and new positions stay pending. This is RB-001's 'connected but no ticks received' case: the client sends no pings, so the dead connection is never noticed (PM-003). Ruled out: an exited process (no restarts, metrics present), expired symbols (both expire 9 Oct 2026 and both went stale at once), and Kafka or consumer problems (nothing is being produced to fall behind on).

## Evidence

- **prometheus**: md_ws_connected has been 1 for the whole window, including after ticks stopped.  
  `md_ws_connected`
- **prometheus**: The tick rate fell from 0.41/s to 0 at 09:13-09:14 UTC and has stayed at 0.  
  `sum(rate(md_ticks_received_total[1m]))`
- **prometheus**: Tick age is 1712 s and 1714 s for the two subscribed symbols, and still climbing.  
  `max by (symbol) (md_tick_age_seconds)`
- **loki**: delta-ticker has not restarted in 30 min, and its last connection event is ws_open at 06:31 UTC, with no ws_closed or ws_error since.  
  `{service_name="delta-ticker"} | json | event=~"ws_closed|ws_error|symbols_read_failed|no_symbols|ws_message"`
- **runbook**: RB-001 names 'connected but no ticks received' as a half-open socket and prescribes a restart; PM-003 is the same signature.  
  `RB-001 > Triage, Mitigation`

## Next step

Restart delta-ticker (RB-001 > Mitigation), then verify per RB-001 > Verify: md_ws_connected is 1, sum(rate(md_ticks_received_total[1m])) is above 0, and max(md_tick_age_seconds) is below 10 s.

## Proposed remediation

1. restart deployment/delta-ticker: RB-001 > Mitigation: a half-open socket is cleared by restarting the ticker.

## Remediation

- ✓ restart deployment/delta-ticker: applied
```

A real investigation usually runs longer: the live run below took 9 model calls. The longer it runs, the larger the share of its input read from cache.

## Output

Progress and the approval prompts go to stderr. The result goes to stdout as JSON: `run_id`, `report`, and `remediation`, the outcome of each proposed action. `--markdown` renders the same result for reading. The exit code is 1 if there is no valid report, or if an approved action failed.

From a real run against the live stack. The feed was healthy, so the agent proposed no remediation:

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
