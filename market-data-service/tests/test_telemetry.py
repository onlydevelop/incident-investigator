import json
import logging
import time

import pytest

from delta_ticker import DeltaTickerClient, telemetry
from delta_ticker.client import _latest_tick
from delta_ticker.telemetry import JsonFormatter, _AccessLogFields

from test_payload import MESSAGE

SYMBOL = MESSAGE["symbol"]


def record(msg="hello", **extra):
    rec = logging.makeLogRecord({"name": "delta_ticker.x", "levelname": "WARNING", "msg": msg})
    rec.__dict__.update(extra)
    return rec


class TestJsonFormatter:
    def test_fields(self):
        line = json.loads(JsonFormatter("delta-ticker").format(record(event="ws_closed", symbol=SYMBOL)))

        assert line["service"] == "delta-ticker"
        assert line["level"] == "warning"
        assert line["event"] == "ws_closed"
        assert line["message"] == "hello"
        assert line["symbol"] == SYMBOL
        assert "trace_id" not in line

    def test_event_defaults_to_logger_name(self):
        assert json.loads(JsonFormatter("x").format(record()))["event"] == "delta_ticker.x"

    def test_trace_ids_inside_a_span(self, tracing):
        with tracing.start_as_current_span("s") as span:
            line = json.loads(JsonFormatter("x").format(record()))

        ctx = span.get_span_context()
        assert line["trace_id"] == format(ctx.trace_id, "032x")
        assert line["span_id"] == format(ctx.span_id, "016x")


class TestAccessLog:
    def access(self, path):
        return logging.makeLogRecord({"msg": '%s - "%s %s HTTP/%s" %d',
                                      "args": ("10.0.0.1:5", "GET", path, "1.1", 200)})

    @pytest.mark.parametrize("path", ["/health", "/docs"])
    def test_probes_are_dropped(self, path):
        assert not _AccessLogFields().filter(self.access(path))

    def test_requests_get_fields(self):
        rec = self.access("/symbols?x=1")

        assert _AccessLogFields().filter(rec)
        assert (rec.event, rec.method, rec.path, rec.status) == ("http_request", "GET", "/symbols?x=1", 200)


class TestTicks:
    @pytest.fixture(autouse=True)
    def _clean(self):
        _latest_tick.clear()

    def test_tick_is_traced_and_its_exchange_time_recorded(self, tracing):
        seen = []
        client = DeltaTickerClient([SYMBOL], on_payload=seen.append)

        client._on_message(None, json.dumps(MESSAGE))

        [span] = [s for s in tracing.spans() if s.name == "v2/ticker process"]
        assert span.attributes["symbol"] == SYMBOL
        assert seen[0].symbol == SYMBOL
        assert _latest_tick[SYMBOL] == MESSAGE["timestamp"] / 1_000_000

    def test_tick_age_grows_while_no_ticks_arrive(self, metric_points):
        _latest_tick[SYMBOL] = time.time() - 42

        [(attrs, age)] = metric_points()["md_tick_age"]

        assert attrs == {"symbol": SYMBOL}
        assert 42 <= age < 50

    def test_unsubscribed_symbols_stop_reporting_age(self, metric_points):
        client = DeltaTickerClient([SYMBOL])
        _latest_tick[SYMBOL] = time.time()

        client.update_symbols([])

        assert metric_points().get("md_tick_age", []) == []

    def test_bad_message_is_logged_with_reason(self, caplog):
        DeltaTickerClient([SYMBOL])._on_message(None, "{not json")

        assert caplog.records[0].reason == "json"


class TestSetup:
    @pytest.fixture(autouse=True)
    def _restore_logging(self):
        """setup() replaces the root logger's handlers; put them back for the other tests."""
        root = logging.getLogger()
        handlers, level = root.handlers[:], root.level
        yield
        root.handlers[:] = handlers
        root.setLevel(level)

    def test_without_endpoint_only_configures_json_logs(self, monkeypatch, capsys):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        monkeypatch.setattr(telemetry.trace, "set_tracer_provider", lambda p: pytest.fail("no export configured"))

        telemetry.setup("delta-ticker")
        logging.getLogger("delta_ticker.x").info("hi", extra={"event": "e"})

        line = json.loads(capsys.readouterr().out)
        assert (line["service"], line["event"], line["message"]) == ("delta-ticker", "e", "hi")

    def test_with_endpoint_exports_traces_and_metrics(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
        installed = {}
        monkeypatch.setattr(telemetry.trace, "set_tracer_provider", lambda p: installed.update(tracer=p))
        monkeypatch.setattr(telemetry.metrics, "set_meter_provider", lambda p: installed.update(meter=p))

        telemetry.setup("delta-ticker")

        try:
            assert installed["tracer"].resource.attributes["service.name"] == "delta-ticker"
            assert installed["meter"]._sdk_config.resource.attributes["service.name"] == "delta-ticker"
        finally:
            installed["tracer"].shutdown()
            installed["meter"].shutdown(timeout_millis=100)
