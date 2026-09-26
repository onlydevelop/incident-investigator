import json
import logging

import pytest
from fastapi.testclient import TestClient

from order_service import api, telemetry
from order_service import store as store_module
from order_service.api import create_app
from order_service.telemetry import JsonFormatter

from fakes import CALL


def test_json_log_line_has_fields_and_trace_ids(tracing):
    record = logging.makeLogRecord({"name": "order_service.api", "levelname": "INFO", "msg": "Created position 7",
                                    "event": "position_created", "position_id": 7})

    with tracing.start_as_current_span("request") as span:
        line = json.loads(JsonFormatter("orders-api").format(record))

    assert {k: line[k] for k in ("service", "level", "event", "message", "position_id")} == {
        "service": "orders-api", "level": "info", "event": "position_created",
        "message": "Created position 7", "position_id": 7,
    }
    assert line["trace_id"] == format(span.get_span_context().trace_id, "032x")


def test_pool_gauges_report_from_url_pools(pool, metric_points, monkeypatch):
    monkeypatch.setattr(store_module, "_pools", [pool])

    points = metric_points()

    stats = pool.get_stats()
    assert points["db_pool_size"] == [({}, stats["pool_size"])]
    assert points["db_pool_max"] == [({}, 2)]
    assert points["db_pool_in_use"] == [({}, 0)]
    assert points["db_pool_requests_waiting"] == [({}, 0)]


def test_created_positions_are_counted_by_side(create, metric_points):
    def count(side):
        return dict((a["side"], v) for a, v in metric_points().get("positions_created", [])).get(side, 0)
    before = count("sell")

    assert create(side="sell").status_code == 201

    assert count("sell") == before + 1


def test_rejected_create_is_logged_with_reason(create, symbols, caplog):
    symbols.fail = True

    assert create().status_code == 502

    [record] = [r for r in caplog.records if getattr(r, "event", None) == "position_rejected"]
    assert (record.symbol, record.reason) == (CALL, "market_data_unavailable")


@pytest.fixture
def restore_logging():
    """setup() replaces the root logger's handlers; put them back for the other tests."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_setup_without_endpoint_only_configures_json_logs(monkeypatch, restore_logging, capsys):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", lambda p: pytest.fail("no export configured"))

    telemetry.setup("orders-api")
    logging.getLogger("order_service.x").info("hi", extra={"event": "e"})

    line = json.loads(capsys.readouterr().out)
    assert (line["service"], line["event"], line["message"]) == ("orders-api", "e", "hi")


def test_setup_with_endpoint_exports_traces_and_metrics(monkeypatch, restore_logging):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    installed = {}
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", lambda p: installed.update(tracer=p))
    monkeypatch.setattr(telemetry.metrics, "set_meter_provider", lambda p: installed.update(meter=p))

    telemetry.setup("orders-api")

    try:
        assert installed["tracer"].resource.attributes["service.name"] == "orders-api"
        assert installed["meter"]._sdk_config.resource.attributes["service.name"] == "orders-api"
    finally:
        installed["tracer"].shutdown()
        installed["meter"].shutdown(timeout_millis=100)


def test_access_log_drops_probes_and_adds_fields():
    def access(path):
        return logging.makeLogRecord({"msg": '%s - "%s %s HTTP/%s" %d', "args": ("10.0.0.1:5", "POST", path, "1.1", 201)})
    fields = telemetry._AccessLogFields()

    assert not fields.filter(access("/health"))
    record = access("/positions")
    assert fields.filter(record)
    assert (record.event, record.method, record.path, record.status) == ("http_request", "POST", "/positions", 201)
    assert fields.filter(logging.makeLogRecord({"msg": "not an access log"}))


def test_instrument_traces_requests(store, symbols, tracing):
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    app = create_app(store, symbols)
    api.instrument(app)
    try:
        TestClient(app).get("/positions")
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
        HTTPXClientInstrumentor().uninstrument()
        PsycopgInstrumentor().uninstrument()

    assert "GET /positions" in {s.name for s in tracing.spans()}
