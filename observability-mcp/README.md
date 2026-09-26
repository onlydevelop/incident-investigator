# observability-mcp

An MCP server that gives a model read-only access to the stack's telemetry:
- PromQL queries against Prometheus;
- LogQL log search in Loki;
- TraceQL search and trace lookup in Tempo.

It talks to the [observability stack](../deploy/observability/README.md) on the local k3s through its `*.localhost` ingresses.

```sh
make obs-up                          # from the repo root: the stack it queries
make -C observability-mcp install    # venv in observability-mcp/.venv
make -C observability-mcp test       # unit tests; the live tests run too if the stack is up
```

## Using it from Claude Code

The repo's [`.mcp.json`](../.mcp.json) registers it as the `observability` server, so a Claude Code session started in this repo picks it up after `make install`. Claude Code asks once before it trusts a project server. Check it with:

```sh
$ claude mcp get observability
observability:
  Scope: Project config (shared via .mcp.json)
  Status: ✓ Connected
  Type: stdio
  Command: observability-mcp/.venv/bin/observability-mcp
```

For other MCP clients:
- **stdio:** run `observability-mcp/.venv/bin/observability-mcp`.
- **Streamable HTTP:** `make run-http` serves it at `http://127.0.0.1:8765/mcp`.
- **MCP Inspector:** `make inspect` opens it in the inspector, to try the tools by hand.

## Tools

Every tool is read-only (`readOnlyHint`). Times take:
- `now`;
- a duration ago (`15m`, `now-2h`);
- RFC 3339 (`2026-09-26T01:30:00Z`);
- Unix seconds.

| Tool | What it does | Main arguments |
|---|---|---|
| `prometheus_query` | PromQL at one instant. Each series' labels and value | `query`, `time` |
| `prometheus_query_range` | PromQL over a range. Each series gets `min`, `max`, `last` and about 60 `[time, value]` points | `query`, `start` (default `1h`), `end`, `step` |
| `prometheus_label_values` | A label's values. With the default `__name__`, metric names | `label`, `match` (a selector) |
| `prometheus_alerts` | Alerts the rules currently produce, with labels, annotations and `active_since` | `state`: `firing`, `pending` or `all` |
| `loki_query` | LogQL over a range. Log queries return lines grouped by stream; metric queries (`count_over_time`, `rate`) return series | `query`, `start`, `end`, `limit`, `direction`, `include_metadata` |
| `loki_label_values` | Stream label names, or one label's values (e.g. every `service_name`) | `label`, `selector` |
| `tempo_search` | TraceQL search. Trace ID, root service and span, duration, and the spans that matched | `query`, `start`, `end`, `limit`, `spans_per_trace` |
| `tempo_get_trace` | One trace by ID, as a flattened span tree | `trace_id`, `max_spans` |

`tempo_get_trace` returns every span with its `depth`, service, name, kind, start, `duration_ms`, status (`ok`, `unset` or `error: <message>`), attributes, and events such as exceptions. It also returns the trace's error count and each service's pod.

The server also sends MCP `instructions`: what runs where, the label conventions (`job="incident-investigator/<app>"`, `service_name`, the JSON log fields), and a suggested order for an investigation. Clients pass these to the model.

### Shaped for a model

- **Values:** sample values are numbers, not strings. `NaN` and `±Inf` stay strings, since JSON has no numbers for them.
- **Times and IDs:** every time is ISO 8601 UTC. Trace and span IDs are lowercase hex, 32 and 16 digits, the same as in the apps' log lines. Tempo's own APIs mix base64 and hex, and drop leading zeros.
- **Limits are never silent:** every limit sets `truncated: true` when it cuts something off. Log lines are cut at 2,000 characters (`line_truncated`), and span attributes at 500.
- **Loki streams:** each stream holds only its index labels. Structured metadata, such as `trace_id`, is per line and only with `include_metadata`, so a query doesn't split into one stream per trace. The Kubernetes fields that repeat on every line are left out.
- **Errors come back as tool errors the model can act on:** a PromQL or LogQL parse error from the backend, "unreachable, is the stack up (make obs-up)?", a timeout with "narrow the time range", or a trace that isn't found (older than 48h, or not flushed yet).

## Configuration

| Variable | Default |
|---|---|
| `PROMETHEUS_URL` | `http://prometheus.localhost` |
| `LOKI_URL` | `http://loki.localhost` |
| `TEMPO_URL` | `http://tempo.localhost` |
| `OBSERVABILITY_HTTP_TIMEOUT` | `30` (seconds per backend request) |

To reach the stack another way, for example through `kubectl port-forward`, set these in `.mcp.json` or the environment.

## Layout

| File | What |
|---|---|
| [`server.py`](src/observability_mcp/server.py) | The tools, the shaping of their results, and `main()` (stdio or streamable HTTP) |
| [`backends.py`](src/observability_mcp/backends.py) | HTTP calls to each backend, and their errors as `BackendError` |
| [`times.py`](src/observability_mcp/times.py) | Parsing times and durations |
| [`config.py`](src/observability_mcp/config.py) | Backend URLs and the timeout |
| [`tests/test_server.py`](tests/test_server.py) | Every tool through a real MCP client, in process, against fake backends that answer like the real APIs |
| [`tests/test_live.py`](tests/test_live.py) | The installed server over stdio against the running stack. Skipped when Prometheus doesn't answer |

Built on the MCP Python SDK 2.x, where v1's `FastMCP` is now `MCPServer`. In 2.x an unexpected exception reaches the model only as "Error executing tool ...". That's why every backend failure is raised as a `ToolError` that carries the backend's message.
