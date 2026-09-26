"""Logs, traces and metrics.

Logs are always JSON lines on stdout: `time`, `level`, `service`, `event`, `message`, any `extra=`
fields (`symbol`, `position_id`, `reason`, ...) and, inside a span, `trace_id` and `span_id`. The OTel Collector
tails them into Loki, where the trace ID links to the trace in Tempo.

Traces and metrics are exported over OTLP only when OTEL_EXPORTER_OTLP_ENDPOINT is set (it is on
k3s). Otherwise `tracer` and `meter` are the OpenTelemetry API's no-ops, so tests and docker-compose
run exactly as before. The exporter reads the rest of its settings from the standard OTEL_*
variables, e.g. OTEL_METRIC_EXPORT_INTERVAL.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

from opentelemetry import metrics, trace

# Module-level, so instruments can be created at import; they bind to the real providers once
# setup() installs them.
tracer = trace.get_tracer("order_service")
meter = metrics.get_meter("order_service")

# Attributes every LogRecord has; anything else on a record came from `extra=` and is logged as a field.
_RECORD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}
# uvicorn's access log for Kubernetes probes, every few seconds, is noise.
_PROBE_PATHS = {"/health", "/docs"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _RECORD_ATTRS and k != "event"})
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            entry["trace_id"] = format(span.trace_id, "032x")
            entry["span_id"] = format(span.span_id, "016x")
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class _AccessLogFields(logging.Filter):
    """Drops probe requests from uvicorn's access log and turns the rest into fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple) or len(record.args) != 5:
            return True
        _client, method, path, _version, status = record.args
        if path.split("?")[0] in _PROBE_PATHS:
            return False
        record.event, record.method, record.path, record.status = "http_request", method, path, status
        return True


def setup(service: str) -> None:
    """Configures logging and, if OTEL_EXPORTER_OTLP_ENDPOINT is set, trace and metric export.
    Call once, first thing in an entry point; `service` becomes service.name."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), handlers=[handler], force=True)
    logging.getLogger("uvicorn.access").addFilter(_AccessLogFields())

    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return

    # Imported here so the SDK is only loaded when something is exported.
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # Also picks up OTEL_RESOURCE_ATTRIBUTES; the name given here wins over OTEL_SERVICE_NAME.
    resource = Resource.create({"service.name": service})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
    )
