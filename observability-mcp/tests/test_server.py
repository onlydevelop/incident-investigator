"""Every tool through a real MCP client, in process, against fake Prometheus/Loki/Tempo backends
(httpx.MockTransport) that answer with the shapes the real APIs return."""
import base64
import json

import anyio
import httpx
import pytest
from mcp import Client

from observability_mcp.backends import Backend
from observability_mcp.server import create_server

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
ROOT, CHILD, GRANDCHILD = "b7ad6b7169203331", "00f067aa0ba902b7", "1111111111111111"
NS = 1790386200_000_000_000  # 2026-09-26T01:30:00Z


class FakeBackends:
    """Routes requests by path to canned JSON responses and records every request."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, text="not found")
        if isinstance(route, Exception):
            raise route
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)

    def backend(self, name: str) -> Backend:
        return Backend(name, f"http://{name.lower()}", transport=httpx.MockTransport(self.handler))

    def last(self, path: str) -> httpx.Request:
        return [r for r in self.requests if r.url.path == path][-1]


@pytest.fixture
def fake():
    return FakeBackends()


@pytest.fixture
def call(fake):
    server = create_server(fake.backend("Prometheus"), fake.backend("Loki"), fake.backend("Tempo"))

    def call(tool: str, **arguments):
        async def run():
            async with Client(server) as client:
                return await client.call_tool(tool, arguments)
        result = anyio.run(run)
        if result.is_error:
            return "ERROR: " + result.content[0].text
        return result.structured_content
    return call


def b64(hex_id: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_id)).decode()


# --- Prometheus ---

def test_instant_query_returns_numbers(fake, call):
    fake.routes["/api/v1/query"] = {"status": "success", "data": {"resultType": "vector", "result": [
        {"metric": {"job": "postgres"}, "value": [1790386200, "1"]},
        {"metric": {"job": "redis"}, "value": [1790386200, "NaN"]},
    ]}}

    out = call("prometheus_query", query="up", time="2026-09-26T01:30:00Z", limit=1)

    assert out["series"] == [{"labels": {"job": "postgres"}, "value": 1.0}]
    assert out["series_count"] == 2 and out["truncated"]
    assert out["time"] == "2026-09-26T01:30:00.000Z"
    assert fake.last("/api/v1/query").url.params["time"] == "1790386200.0"


def test_scalar_query(fake, call):
    fake.routes["/api/v1/query"] = {"status": "success", "data": {"resultType": "scalar", "result": [1790386200, "+Inf"]}}

    assert call("prometheus_query", query="1/0")["value"] == "+Inf"


def test_range_query_summarises_each_series_and_picks_a_step(fake, call):
    fake.routes["/api/v1/query_range"] = {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"symbol": "C"}, "values": [[1790386200, "0.5"], [1790386260, "3"], [1790386320, "1.25"]]},
    ]}}

    out = call("prometheus_query_range", query="md_tick_age_seconds", start="1h")

    [series] = out["series"]
    assert (series["min"], series["max"], series["last"]) == (0.5, 3.0, 1.25)
    assert series["points"][0] == ["2026-09-26T01:30:00.000Z", 0.5]
    assert out["step_seconds"] == 60  # 1h / 60 points
    assert fake.last("/api/v1/query_range").url.params["step"] == "60"


def test_range_query_explicit_step_and_bad_step(fake, call):
    fake.routes["/api/v1/query_range"] = {"status": "success", "data": {"resultType": "matrix", "result": []}}

    assert call("prometheus_query_range", query="up", step="15s")["step_seconds"] == 15
    assert "not a duration" in call("prometheus_query_range", query="up", step="fast")


def test_backend_error_reaches_the_model(fake, call):
    fake.routes["/api/v1/query"] = httpx.Response(400, json={
        "status": "error", "errorType": "bad_data", "error": "1:4: parse error: unexpected end of input"})

    out = call("prometheus_query", query="up{")

    assert out.startswith("ERROR:") and "parse error: unexpected end of input" in out


def test_unreachable_backend_says_how_to_fix_it(fake, call):
    fake.routes["/api/v1/query"] = httpx.ConnectError("connection refused")

    assert "unreachable at http://prometheus" in call("prometheus_query", query="up")
    assert "make obs-up" in call("prometheus_query", query="up")


def test_timeout_suggests_narrowing(fake, call):
    fake.routes["/api/v1/query"] = httpx.ReadTimeout("slow")

    assert "Narrow the time range" in call("prometheus_query", query="up")


def test_bad_time_is_an_error_not_a_crash(call):
    assert "not a time" in call("prometheus_query", query="up", time="yesterday")
    assert "must be before" in call("prometheus_query_range", query="up", start="now", end="1h")


def test_label_values(fake, call):
    fake.routes["/api/v1/label/__name__/values"] = {"status": "success", "data": ["md_a", "md_b", "md_c"]}

    out = call("prometheus_label_values", match='{__name__=~"md_.*"}', limit=2)

    assert out == {"label": "__name__", "count": 3, "truncated": True, "values": ["md_a", "md_b"]}
    assert fake.last("/api/v1/label/__name__/values").url.params["match[]"] == '{__name__=~"md_.*"}'


def test_label_name_is_validated(call):
    assert call("prometheus_label_values", label="../../admin").startswith("ERROR:")


def test_alerts_filtered_by_state(fake, call):
    fake.routes["/api/v1/alerts"] = {"status": "success", "data": {"alerts": [
        {"labels": {"alertname": "TargetDown", "job": "redis"}, "annotations": {"summary": "down"},
         "state": "firing", "activeAt": "2026-09-26T01:00:00Z", "value": "1e+00"},
        {"labels": {"alertname": "Watchdog"}, "annotations": {}, "state": "pending", "value": "1"},
    ]}}

    firing = call("prometheus_alerts")
    assert firing["count"] == 1
    assert firing["alerts"][0] == {"name": "TargetDown", "state": "firing", "active_since": "2026-09-26T01:00:00Z",
                                   "labels": {"job": "redis"}, "annotations": {"summary": "down"}, "value": 1.0}
    assert call("prometheus_alerts", state="all")["count"] == 2


# --- Loki ---

LOKI_STREAMS = {"status": "success", "data": {"resultType": "streams", "result": [{
    "stream": {"service_name": "orders-api", "k8s_pod_name": "orders-api-1"},
    "values": [
        [str(NS), '{"level": "error", "event": "postgres_unavailable"}\n',
         {"structuredMetadata": {"event": "postgres_unavailable", "trace_id": TRACE_ID, "k8s_node_name": "n1"}}],
        [str(NS - 1_000_000_000), "x" * 2500],
    ],
}]}}


def test_log_query_groups_lines_by_stream(fake, call):
    fake.routes["/loki/api/v1/query_range"] = LOKI_STREAMS

    out = call("loki_query", query='{service_name="orders-api"}', start="2026-09-26T01:00:00Z",
               end="2026-09-26T01:31:00Z", limit=2)

    [stream] = out["streams"]
    assert stream["labels"]["service_name"] == "orders-api"
    first, second = stream["lines"]
    assert first == {"time": "2026-09-26T01:30:00.000Z", "line": '{"level": "error", "event": "postgres_unavailable"}'}
    assert len(second["line"]) == 2000 and second["line_truncated"]
    assert out["line_count"] == 2 and out["truncated"]
    request = fake.last("/loki/api/v1/query_range")
    assert request.headers["X-Loki-Response-Encoding-Flags"] == "categorize-labels"
    assert request.url.params["start"] == "1790384400000000000"
    assert request.url.params["direction"] == "backward"


def test_log_query_metadata_without_kubernetes_noise(fake, call):
    fake.routes["/loki/api/v1/query_range"] = LOKI_STREAMS

    first = call("loki_query", query="{}", include_metadata=True)["streams"][0]["lines"][0]

    assert first["metadata"] == {"event": "postgres_unavailable", "trace_id": TRACE_ID}


def test_loki_metric_query_returns_series(fake, call):
    fake.routes["/loki/api/v1/query_range"] = {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"service_name": "orders-api"}, "values": [[1790386200, "4"], [1790386500, "7"]]},
    ]}}

    out = call("loki_query", query="sum by (service_name) (count_over_time({}[5m]))")

    assert out["result_type"] == "matrix"
    assert out["series"][0]["max"] == 7.0


def test_loki_parse_error_is_passed_on(fake, call):
    fake.routes["/loki/api/v1/query_range"] = httpx.Response(400, text="parse error at line 1, col 2: syntax error\n")

    assert "parse error at line 1" in call("loki_query", query="{")


def test_loki_label_names_and_values(fake, call):
    fake.routes["/loki/api/v1/labels"] = {"status": "success", "data": ["service_name", "k8s_pod_name"]}
    fake.routes["/loki/api/v1/label/service_name/values"] = {"status": "success", "data": ["orders-api"]}

    assert call("loki_label_values")["values"] == ["service_name", "k8s_pod_name"]
    out = call("loki_label_values", label="service_name", selector='{k8s_namespace_name="x"}')
    assert out == {"label": "service_name", "count": 1, "values": ["orders-api"]}
    assert fake.last("/loki/api/v1/label/service_name/values").url.params["query"] == '{k8s_namespace_name="x"}'


# --- Tempo ---

def test_trace_search(fake, call):
    fake.routes["/api/search"] = {"traces": [{
        "traceID": TRACE_ID.lstrip("0") + "",  # Tempo drops leading zeros
        "rootServiceName": "delta-ticker", "rootTraceName": "v2/ticker process",
        "startTimeUnixNano": str(NS), "durationMs": 15,
        "spanSets": [{"spans": [{"spanID": "3eea58559728b306", "name": "market-data.ticker process",
                                 "startTimeUnixNano": str(NS + 1_000_000), "durationNanos": "968375",
                                 "attributes": [{"key": "service.name", "value": {"stringValue": "position-updater"}}]}],
                      "matched": 1}],
    }]}

    out = call("tempo_search", query='{resource.service.name="position-updater"}', start="30m", spans_per_trace=1)

    [trace] = out["traces"]
    assert trace["trace_id"] == TRACE_ID
    assert (trace["root_service"], trace["root_span"], trace["duration_ms"]) == ("delta-ticker", "v2/ticker process", 15)
    assert trace["matched_spans"] == [{"span_id": "3eea58559728b306", "service": "position-updater",
                                       "name": "market-data.ticker process", "start": "2026-09-26T01:30:00.001Z",
                                       "duration_ms": 0.968375}]
    assert fake.last("/api/search").url.params["spss"] == "1"


def span(span_id, parent, name, start_ms, end_ms, **extra):
    return {"spanId": b64(span_id), **({"parentSpanId": b64(parent)} if parent else {}), "name": name,
            "kind": "SPAN_KIND_SERVER", "startTimeUnixNano": str(NS + start_ms * 1_000_000),
            "endTimeUnixNano": str(NS + end_ms * 1_000_000), **extra}


def resource(service, *spans):
    return {"resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": service}},
        {"key": "k8s.pod.name", "value": {"stringValue": f"{service}-1"}},
        {"key": "telemetry.sdk.name", "value": {"stringValue": "opentelemetry"}},
    ]}, "scopeSpans": [{"spans": list(spans)}]}


TRACE = {"trace": {"resourceSpans": [
    resource("position-updater",
             span(GRANDCHILD, CHILD, "UPDATE", 3, 4, kind="SPAN_KIND_CLIENT",
                  status={"code": "STATUS_CODE_ERROR", "message": "connection refused"},
                  events=[{"name": "exception", "timeUnixNano": str(NS + 3_500_000), "attributes": [
                      {"key": "exception.type", "value": {"stringValue": "OperationalError"}}]}]),
             span(CHILD, ROOT, "market-data.ticker process", 2, 5, attributes=[
                 {"key": "messaging.kafka.offset", "value": {"intValue": "42"}},
                 {"key": "retry", "value": {"boolValue": False}},
                 {"key": "ratio", "value": {"doubleValue": 0.5}},
                 {"key": "tags", "value": {"arrayValue": {"values": [{"stringValue": "a"}]}}},
                 {"key": "db.statement", "value": {"stringValue": "S" * 600}}])),
    resource("delta-ticker", span(ROOT, None, "v2/ticker process", 0, 10, status={"code": "STATUS_CODE_OK"})),
]}}


def test_get_trace_builds_the_span_tree(fake, call):
    fake.routes[f"/api/v2/traces/{TRACE_ID}"] = TRACE

    out = call("tempo_get_trace", trace_id=TRACE_ID.upper().lstrip("0"))

    assert out["trace_id"] == TRACE_ID
    assert out["root"] == {"service": "delta-ticker", "name": "v2/ticker process"}
    assert (out["span_count"], out["error_count"], out["duration_ms"]) == (3, 1, 10.0)
    assert out["services"]["position-updater"] == {"k8s.pod.name": "position-updater-1"}
    assert [(s["depth"], s["span_id"], s["service"]) for s in out["spans"]] == [
        (0, ROOT, "delta-ticker"), (1, CHILD, "position-updater"), (2, GRANDCHILD, "position-updater")]
    root, child, grandchild = out["spans"]
    assert root["parent_span_id"] is None and root["status"] == "ok" and root["kind"] == "server"
    assert child["parent_span_id"] == ROOT and child["status"] == "unset"
    assert child["attributes"]["messaging.kafka.offset"] == 42
    assert (child["attributes"]["retry"], child["attributes"]["ratio"], child["attributes"]["tags"]) == (False, 0.5, ["a"])
    assert len(child["attributes"]["db.statement"]) == 501
    assert grandchild["status"] == "error: connection refused"
    assert grandchild["events"] == [{"name": "exception", "time": "2026-09-26T01:30:00.003Z",
                                     "attributes": {"exception.type": "OperationalError"}}]
    assert grandchild["kind"] == "client" and grandchild["duration_ms"] == 1.0


def test_get_trace_limits_spans(fake, call):
    fake.routes[f"/api/v2/traces/{TRACE_ID}"] = TRACE

    out = call("tempo_get_trace", trace_id=TRACE_ID, max_spans=1)

    assert out["truncated"] and len(out["spans"]) == 1 and out["span_count"] == 3


def test_span_with_missing_parent_becomes_a_root(fake, call):
    fake.routes[f"/api/v2/traces/{TRACE_ID}"] = {"trace": {"resourceSpans": [
        resource("orders-api", span(CHILD, "ffffffffffffffff", "--", 0, 1))]}}

    [orphan] = call("tempo_get_trace", trace_id=TRACE_ID)["spans"]

    assert orphan["depth"] == 0 and orphan["parent_span_id"] == "ffffffffffffffff"


def test_missing_trace(fake, call):
    assert "not found" in call("tempo_get_trace", trace_id=TRACE_ID)
    fake.routes[f"/api/v2/traces/{TRACE_ID}"] = {"trace": {}}
    assert "has no spans" in call("tempo_get_trace", trace_id=TRACE_ID)


def test_trace_id_must_be_hex(call):
    assert call("tempo_get_trace", trace_id="not-a-trace").startswith("ERROR:")


def test_tempo_unreachable(fake, call):
    fake.routes[f"/api/v2/traces/{TRACE_ID}"] = httpx.ConnectError("refused")

    assert "Tempo is unreachable" in call("tempo_get_trace", trace_id=TRACE_ID)


# --- The server itself ---

def test_tools_are_listed_read_only_with_described_parameters(fake):
    async def run():
        async with Client(create_server(fake.backend("Prometheus"), fake.backend("Loki"), fake.backend("Tempo"))) as c:
            return (await c.list_tools()).tools
    tools = {t.name: t for t in anyio.run(run)}

    assert set(tools) == {"prometheus_query", "prometheus_query_range", "prometheus_label_values", "prometheus_alerts",
                          "loki_query", "loki_label_values", "tempo_search", "tempo_get_trace"}
    for tool in tools.values():
        assert tool.annotations.read_only_hint and tool.description
        for name, schema in tool.input_schema["properties"].items():
            assert schema.get("description"), f"{tool.name}.{name} has no description"


def test_non_json_response(fake, call):
    fake.routes["/api/v1/alerts"] = httpx.Response(200, text="<html>proxy error</html>")

    assert "isn't JSON" in call("prometheus_alerts")


def test_structured_output_is_json_serialisable(fake, call):
    fake.routes[f"/api/v2/traces/{TRACE_ID}"] = TRACE

    json.dumps(call("tempo_get_trace", trace_id=TRACE_ID))


def test_attribute_and_id_edge_cases():
    from observability_mcp.server import _any_value, _hex_id

    assert _any_value({"kvlistValue": {"values": [{"key": "a", "value": {"intValue": "1"}}]}}) == {"a": 1}
    assert _any_value({}) is None
    assert _hex_id("not base64 or hex!", 16) == "not base64 or hex!"
    assert _hex_id("", 16) == ""


@pytest.mark.parametrize("argv, expected", [
    ([], ("stdio", {})),
    (["--transport", "streamable-http", "--port", "9999"], ("streamable-http", {"host": "127.0.0.1", "port": 9999})),
])
def test_main_runs_the_chosen_transport(monkeypatch, argv, expected):
    from observability_mcp import server as server_module

    runs = []
    monkeypatch.setattr("sys.argv", ["observability-mcp", *argv])
    monkeypatch.setattr(server_module.MCPServer, "run", lambda self, transport, **kw: runs.append((transport, kw)))

    server_module.main()

    assert runs == [expected]
