"""MCP server for the local observability stack: Prometheus metrics, Loki logs and Tempo traces.

    observability-mcp                      # stdio, for Claude Code and other MCP clients
    observability-mcp --transport streamable-http --port 8765

Every tool is read-only. Results are shaped for a model to read: numbers instead of strings, ISO
times, span trees flattened with their depth, and explicit `truncated` flags wherever a limit cut
something off.
"""
import argparse
import base64
import binascii
import logging
import math
from datetime import timedelta
from typing import Annotated, Any, Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from observability_mcp import config
from observability_mcp.backends import Backend, BackendError, NotFound
from observability_mcp.times import iso, iso_from_ns, parse_duration, parse_range, parse_time

INSTRUCTIONS = """\
Read-only access to the paper-trading stack's telemetry on the local k3s cluster.

What's there:
- Apps (namespace incident-investigator): delta-ticker (Delta websocket -> Redis + Kafka),
  symbols-api, orders-api (positions, Postgres), position-updater (Kafka consumer -> Postgres).
- Infra: postgres, redis, kafka, each with a Prometheus exporter.

Conventions:
- Prometheus: app metrics have job="incident-investigator/<app>"; exporter metrics have
  job="postgres" | "redis" | "kafka-exporter". Useful families: md_* (market data), db_pool_*,
  positions_*, position_updater_*, http_server_request_duration_seconds, kafka_consumergroup_lag,
  pg_stat_activity_count, redis_*, traces_spanmetrics_* (per-span rate/errors/latency from Tempo).
- Loki: select with {service_name="<app>"} or {k8s_namespace_name="incident-investigator"}.
  App lines are JSON with level, event, message, trace_id; filter with | event="..." or | json.
- Tempo: TraceQL, e.g. {resource.service.name="orders-api" && status=error} or {duration > 200ms}.

A typical investigation: prometheus_alerts or prometheus_query_range to see what changed and when,
loki_query for errors in that window, then tempo_get_trace on a trace_id from a log line.
Use prometheus_label_values / loki_label_values to discover names instead of guessing them.
"""

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
# Enough points for a model to see a trend without flooding its context.
TARGET_POINTS = 60
MAX_LINE_CHARS = 2000
MAX_ATTR_CHARS = 500
TIME_FORMATS = "now, a duration ago (15m, now-2h), RFC 3339 (2026-09-26T01:30:00Z) or Unix seconds"

# Parameter types shared by several tools; the descriptions end up in each tool's input schema.
Start = Annotated[str, Field(description=f"Range start: {TIME_FORMATS}.")]
End = Annotated[str, Field(description=f"Range end: {TIME_FORMATS}.")]
Since = Annotated[str, Field(description=f"Only data seen since then: {TIME_FORMATS}.")]
Limit = Annotated[int, Field(ge=1, le=5000)]
# Label names go into the URL path, so only what Prometheus and Loki allow as a name.
LABEL_NAME = r"^[a-zA-Z_][a-zA-Z0-9_]*$"

# Loki's structured metadata repeats these on every line; the stream labels already say which pod.
_NOISY_METADATA = ("k8s_", "container_", "service_instance_id")


def create_server(
    prometheus: Optional[Backend] = None,
    loki: Optional[Backend] = None,
    tempo: Optional[Backend] = None,
) -> MCPServer:
    prometheus = prometheus or Backend("Prometheus", config.PROMETHEUS_URL, config.HTTP_TIMEOUT)
    loki = loki or Backend("Loki", config.LOKI_URL, config.HTTP_TIMEOUT)
    tempo = tempo or Backend("Tempo", config.TEMPO_URL, config.HTTP_TIMEOUT)
    server = MCPServer("observability", instructions=INSTRUCTIONS)

    # --- Prometheus ---

    @server.tool(annotations=READ_ONLY)
    async def prometheus_query(
        query: Annotated[str, Field(description="PromQL, e.g. `sum by (job) (rate(http_server_request_duration_seconds_count[5m]))`.")],
        time: Annotated[Optional[str], Field(description=f"When to evaluate: {TIME_FORMATS}. Default now.")] = None,
        limit: Annotated[Limit, Field(description="The most series to return.")] = 50,
    ) -> dict[str, Any]:
        """Evaluates a PromQL expression at one instant (default now) and returns each series' labels
        and value. For a trend over time use prometheus_query_range."""
        at = _time(time)
        data = await _call(prometheus.get("/api/v1/query", {"query": query, "time": at.timestamp()}))
        result_type, result = data["data"]["resultType"], data["data"]["result"]
        if result_type in ("scalar", "string"):
            return {"result_type": result_type, "time": iso(at), "value": _number(result[1])}
        series = [{"labels": s["metric"], "value": _number(s["value"][1])} for s in result]
        return {"result_type": result_type, "time": iso(at), "series_count": len(series),
                "truncated": len(series) > limit, "series": series[:limit]}

    @server.tool(annotations=READ_ONLY)
    async def prometheus_query_range(
        query: Annotated[str, Field(description="PromQL, e.g. `histogram_quantile(0.99, sum by (le, job) (rate(http_server_request_duration_seconds_bucket[5m])))`.")],
        start: Start = "1h",
        end: End = "now",
        step: Annotated[Optional[str], Field(description="Resolution such as 15s or 5m. Default: the range divided into about 60 steps.")] = None,
        limit: Annotated[Limit, Field(description="The most series to return.")] = 20,
    ) -> dict[str, Any]:
        """Evaluates a PromQL expression over a time range. Each series comes back with its min, max
        and last value, and about 60 points [time, value]."""
        start_at, end_at = _range(start, end)
        step_s = _step(step, end_at - start_at)
        data = await _call(prometheus.get("/api/v1/query_range", {
            "query": query, "start": start_at.timestamp(), "end": end_at.timestamp(), "step": step_s,
        }))
        series = [_range_series(s) for s in data["data"]["result"]]
        return {"start": iso(start_at), "end": iso(end_at), "step_seconds": step_s, "series_count": len(series),
                "truncated": len(series) > limit, "series": series[:limit]}

    @server.tool(annotations=READ_ONLY)
    async def prometheus_label_values(
        label: Annotated[str, Field(pattern=LABEL_NAME, description="The label, e.g. __name__ (metric names), job, http_route, consumergroup.")] = "__name__",
        match: Annotated[Optional[str], Field(description='Only series matching this selector, e.g. `{job="incident-investigator/orders-api"}` or `{__name__=~"md_.*"}`.')] = None,
        start: Since = "1h",
        limit: Annotated[Limit, Field(description="The most values to return.")] = 500,
    ) -> dict[str, Any]:
        """Lists the values a label has, which is how to discover names. With the default label
        `__name__` it lists metric names."""
        start_at, end_at = _range(start, "now")
        data = await _call(prometheus.get(f"/api/v1/label/{label}/values", {
            "match[]": match, "start": start_at.timestamp(), "end": end_at.timestamp(),
        }))
        values = data["data"]
        return {"label": label, "count": len(values), "truncated": len(values) > limit, "values": values[:limit]}

    @server.tool(annotations=READ_ONLY)
    async def prometheus_alerts(
        state: Annotated[Literal["firing", "pending", "all"], Field(description="Which alerts to list.")] = "firing",
    ) -> dict[str, Any]:
        """Lists the alerts Prometheus's alerting rules currently produce, with their labels,
        annotations and since when they've been active. A quick first look at what's wrong."""
        data = await _call(prometheus.get("/api/v1/alerts"))
        alerts = [
            {"name": a["labels"].get("alertname"), "state": a["state"], "active_since": a.get("activeAt"),
             "labels": {k: v for k, v in a["labels"].items() if k != "alertname"},
             "annotations": a.get("annotations", {}), "value": _number(a.get("value"))}
            for a in data["data"]["alerts"] if state == "all" or a["state"] == state
        ]
        return {"state": state, "count": len(alerts), "alerts": alerts}

    # --- Loki ---

    @server.tool(annotations=READ_ONLY)
    async def loki_query(
        query: Annotated[str, Field(description=(
            'LogQL, e.g. `{service_name="orders-api"} | detected_level="ERROR"`, '
            '`{k8s_namespace_name="incident-investigator"} |= "Failed"`, '
            '`{service_name="position-updater"} | event="tick_failed"`, or a metric query such as '
            '`sum by (service_name) (count_over_time({k8s_namespace_name="incident-investigator"} | detected_level="ERROR" [5m]))`.'))],
        start: Start = "1h",
        end: End = "now",
        limit: Annotated[Limit, Field(description="The most lines to return, across all streams.")] = 100,
        direction: Annotated[Literal["backward", "forward"], Field(description="backward returns the newest lines, forward the oldest.")] = "backward",
        include_metadata: Annotated[bool, Field(description=(
            "Also return each line's structured metadata (event, trace_id, detected_level, ...). "
            "App lines already carry these in their JSON."))] = False,
    ) -> dict[str, Any]:
        """Runs a LogQL query over a time range. A log query returns matching lines grouped by stream
        (newest first unless direction=forward). A metric query, such as count_over_time or rate,
        returns series like prometheus_query_range."""
        start_at, end_at = _range(start, end)
        params = {"query": query, "start": _ns(start_at), "end": _ns(end_at), "limit": limit, "direction": direction}
        # Keeps structured metadata out of the stream labels, so each stream is one pod's output
        # rather than one stream per trace_id.
        data = await _call(loki.get("/loki/api/v1/query_range", params,
                                    headers={"X-Loki-Response-Encoding-Flags": "categorize-labels"}))
        result_type, result = data["data"]["resultType"], data["data"]["result"]
        base = {"query": query, "start": iso(start_at), "end": iso(end_at), "result_type": result_type}
        if result_type != "streams":
            series = [_range_series(s) for s in result]
            return {**base, "series_count": len(series), "series": series}
        streams = [_stream(s, include_metadata) for s in result]
        line_count = sum(len(s["lines"]) for s in streams)
        return {**base, "line_count": line_count, "truncated": line_count >= limit, "streams": streams}

    @server.tool(annotations=READ_ONLY)
    async def loki_label_values(
        label: Annotated[Optional[str], Field(pattern=LABEL_NAME, description="A label such as service_name, k8s_namespace_name or k8s_pod_name. Omit to list label names.")] = None,
        selector: Annotated[Optional[str], Field(description='Only streams matching this selector, e.g. `{k8s_namespace_name="incident-investigator"}`.')] = None,
        start: Since = "1h",
    ) -> dict[str, Any]:
        """Lists Loki's stream label names, or the values of one label. This is how to discover
        which services, pods and namespaces have logs."""
        start_at, end_at = _range(start, "now")
        params = {"query": selector, "start": _ns(start_at), "end": _ns(end_at)}
        path = f"/loki/api/v1/label/{label}/values" if label else "/loki/api/v1/labels"
        values = (await _call(loki.get(path, params))).get("data") or []
        return {"label": label, "count": len(values), "values": values}

    # --- Tempo ---

    @server.tool(annotations=READ_ONLY)
    async def tempo_search(
        query: Annotated[str, Field(description=(
            'TraceQL, e.g. `{resource.service.name="orders-api" && status=error}`, '
            '`{name="POST /positions" && duration > 200ms}`, '
            '`{resource.service.name="position-updater"} && {resource.service.name="delta-ticker"}`. '
            '`{}` matches every trace.'))] = "{}",
        start: Start = "1h",
        end: End = "now",
        limit: Annotated[Limit, Field(description="The most traces to return.")] = 20,
        spans_per_trace: Annotated[int, Field(ge=1, le=100, description="The most matching spans to show per trace.")] = 3,
    ) -> dict[str, Any]:
        """Finds traces with TraceQL. Each result has the trace ID, its root service and span, its
        start and duration, and the spans that matched."""
        start_at, end_at = _range(start, end)
        data = await _call(tempo.get("/api/search", {
            "q": query, "start": int(start_at.timestamp()), "end": math.ceil(end_at.timestamp()),
            "limit": limit, "spss": spans_per_trace,
        }))
        traces = [_search_result(t) for t in data.get("traces") or []]
        return {"query": query, "start": iso(start_at), "end": iso(end_at), "count": len(traces), "traces": traces}

    @server.tool(annotations=READ_ONLY)
    async def tempo_get_trace(
        trace_id: Annotated[str, Field(pattern=r"^\s*[0-9a-fA-F]{1,32}\s*$", description="The trace ID in hex, e.g. a trace_id from a log line.")],
        max_spans: Annotated[int, Field(ge=1, le=5000, description="The most spans to return, in tree order.")] = 200,
    ) -> dict[str, Any]:
        """Fetches one trace by ID, such as a trace_id from a log line or a tempo_search result. Spans
        come in tree order, each with its depth, service, name, kind, start, duration, status,
        attributes and events (exceptions included)."""
        trace_id = trace_id.strip().lower().zfill(32)
        try:
            data = await tempo.get(f"/api/v2/traces/{trace_id}")
        except NotFound:
            raise ToolError(f"Trace {trace_id} not found. It may be older than Tempo's 48h retention, "
                            "or so recent that it hasn't been written yet (retry in a few seconds).") from None
        except BackendError as e:
            raise ToolError(str(e)) from None
        return _trace(trace_id, data.get("trace") or data, max_spans)

    return server


# --- Helpers ---

async def _call(request):
    try:
        return await request
    except BackendError as e:
        raise ToolError(str(e)) from None


def _time(value: Optional[str]):
    try:
        return parse_time(value)
    except ValueError as e:
        raise ToolError(str(e)) from None


def _range(start: Optional[str], end: Optional[str]):
    try:
        return parse_range(start, end)
    except ValueError as e:
        raise ToolError(str(e)) from None


def _step(step: Optional[str], span: timedelta) -> float:
    if step:
        try:
            return parse_duration(step).total_seconds()
        except ValueError as e:
            raise ToolError(str(e)) from None
    return max(1.0, round(span.total_seconds() / TARGET_POINTS))


def _ns(moment) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def _number(value: Any) -> float | str | None:
    """Prometheus sends sample values as strings. NaN and ±Inf stay strings, since JSON has no
    number for them."""
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else str(value)


def _range_series(series: dict) -> dict[str, Any]:
    points = [[iso_from_ns(int(float(t) * 1e9)), _number(v)] for t, v in series.get("values", [])]
    numbers = [v for _, v in points if isinstance(v, float)]
    summary = {"min": min(numbers), "max": max(numbers), "last": points[-1][1]} if numbers else {}
    return {"labels": series["metric"], **summary, "points": points}


def _stream(stream: dict, include_metadata: bool) -> dict[str, Any]:
    lines = []
    for value in stream["values"]:
        text = value[1].rstrip("\n")
        line = {"time": iso_from_ns(value[0]), "line": text[:MAX_LINE_CHARS]}
        if len(text) > MAX_LINE_CHARS:
            line["line_truncated"] = True
        if include_metadata and len(value) > 2:
            metadata = {k: v for k, v in (value[2].get("structuredMetadata") or {}).items()
                        if not k.startswith(_NOISY_METADATA)}
            if metadata:
                line["metadata"] = metadata
        lines.append(line)
    return {"labels": stream["stream"], "lines": lines}


def _hex_id(value: str, width: int) -> str:
    """IDs as the apps log them: lowercase hex, `width` digits (32 for a trace, 16 for a span).
    Tempo's search API gives hex without leading zeros, its trace API base64 (OTLP JSON)."""
    if not value:
        return ""
    if len(value) <= width and all(c in "0123456789abcdefABCDEF" for c in value):
        return value.lower().zfill(width)
    try:
        return base64.b64decode(value, validate=True).hex().zfill(width)
    except (binascii.Error, ValueError):
        return value


def _any_value(value: dict) -> Any:
    """An OTLP AnyValue as a plain value."""
    if "stringValue" in value:
        text = value["stringValue"]
        return text if len(text) <= MAX_ATTR_CHARS else text[:MAX_ATTR_CHARS] + "…"
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return value["doubleValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "arrayValue" in value:
        return [_any_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return _attributes(value["kvlistValue"].get("values", []))
    return None


def _attributes(attributes: list[dict]) -> dict[str, Any]:
    return {a["key"]: _any_value(a.get("value", {})) for a in attributes or []}


def _status(status: dict) -> str:
    code = status.get("code", "STATUS_CODE_UNSET")
    if code in ("STATUS_CODE_ERROR", 2):
        return f"error: {status['message']}" if status.get("message") else "error"
    return "ok" if code in ("STATUS_CODE_OK", 1) else "unset"


def _search_result(trace: dict) -> dict[str, Any]:
    matched = []
    for span_set in trace.get("spanSets") or ([trace["spanSet"]] if trace.get("spanSet") else []):
        for span in span_set.get("spans", []):
            attributes = _attributes(span.get("attributes"))
            matched.append({
                "span_id": _hex_id(span.get("spanID", ""), 16),
                "service": attributes.pop("service.name", None),
                "name": span.get("name"),
                "start": iso_from_ns(span["startTimeUnixNano"]),
                "duration_ms": int(span.get("durationNanos", 0)) / 1e6,
                **({"attributes": attributes} if attributes else {}),
            })
    return {
        "trace_id": _hex_id(trace["traceID"], 32),
        "root_service": trace.get("rootServiceName"),
        "root_span": trace.get("rootTraceName"),
        "start": iso_from_ns(trace["startTimeUnixNano"]),
        "duration_ms": trace.get("durationMs", 0),
        "matched_spans": matched,
    }


def _trace(trace_id: str, trace: dict, max_spans: int) -> dict[str, Any]:
    spans, resources = [], {}
    for resource_spans in trace.get("resourceSpans", []):
        resource = _attributes(resource_spans.get("resource", {}).get("attributes"))
        service = resource.get("service.name", "unknown")
        resources.setdefault(service, {k: v for k, v in resource.items()
                                       if k in ("service.namespace", "k8s.namespace.name", "k8s.pod.name",
                                                "k8s.deployment.name", "service.version")})
        for scope_spans in resource_spans.get("scopeSpans", []):
            for span in scope_spans.get("spans", []):
                start, end = int(span["startTimeUnixNano"]), int(span["endTimeUnixNano"])
                spans.append({
                    "span_id": _hex_id(span.get("spanId", ""), 16),
                    "parent_span_id": _hex_id(span.get("parentSpanId", ""), 16) or None,
                    "service": service,
                    "name": span.get("name"),
                    "kind": str(span.get("kind", "")).removeprefix("SPAN_KIND_").lower(),
                    "_start_ns": start,
                    "_end_ns": end,
                    "start": iso_from_ns(start),
                    "duration_ms": (end - start) / 1e6,
                    "status": _status(span.get("status") or {}),
                    "attributes": _attributes(span.get("attributes")),
                    **({"events": [{"name": e.get("name"), "time": iso_from_ns(e["timeUnixNano"]),
                                    "attributes": _attributes(e.get("attributes"))}
                                   for e in span["events"]]} if span.get("events") else {}),
                })
    if not spans:
        raise ToolError(f"Trace {trace_id} has no spans.")

    # Depth-first from the roots, children by start time. A span whose parent isn't in the trace
    # (not yet flushed, or dropped) is treated as a root.
    ids = {s["span_id"] for s in spans}
    children: dict[Optional[str], list[dict]] = {}
    for span in spans:
        parent = span["parent_span_id"] if span["parent_span_id"] in ids else None
        children.setdefault(parent, []).append(span)
    ordered: list[dict] = []

    def walk(parent: Optional[str], depth: int):
        for span in sorted(children.get(parent, []), key=lambda s: s["_start_ns"]):
            span["depth"] = depth
            ordered.append(span)
            walk(span["span_id"], depth + 1)

    walk(None, 0)
    first = min(s["_start_ns"] for s in spans)
    last = max(s["_end_ns"] for s in spans)
    for span in ordered:
        del span["_start_ns"], span["_end_ns"]
    roots = children.get(None, [])
    return {
        "trace_id": trace_id,
        "root": {"service": roots[0]["service"], "name": roots[0]["name"]} if roots else None,
        "start": iso_from_ns(first),
        "duration_ms": (last - first) / 1e6,
        "span_count": len(ordered),
        "error_count": sum(s["status"].startswith("error") for s in ordered),
        "services": resources,
        "truncated": len(ordered) > max_spans,
        "spans": ordered[:max_spans],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="streamable-http only")
    parser.add_argument("--port", type=int, default=8765, help="streamable-http only")
    args = parser.parse_args()
    # httpx logs every request at INFO; on stdio that's stderr noise in the client's log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server = create_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
