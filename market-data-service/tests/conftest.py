import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# OpenTelemetry's global providers can be set once per process, so the whole session shares them.
# Until they're set, the code's tracer and meter are no-ops, exactly as in docker-compose.
_spans = InMemorySpanExporter()
_metric_reader = InMemoryMetricReader()


@pytest.fixture(scope="session", autouse=True)
def _telemetry():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_spans))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[_metric_reader]))


@pytest.fixture
def tracing():
    """A tracer; the spans finished during the test are in `tracing.spans()`."""
    _spans.clear()
    tracer = trace.get_tracer("tests")
    tracer.spans = _spans.get_finished_spans
    return tracer


@pytest.fixture
def metric_points():
    """Returns {metric name: [(attributes, value), ...]} as collected right now."""
    def collect():
        points = {}
        data = _metric_reader.get_metrics_data()
        for rm in data.resource_metrics if data else []:
            for sm in rm.scope_metrics:
                for m in sm.metrics:
                    points[m.name] = [(dict(p.attributes), getattr(p, "value", None)) for p in m.data.data_points]
        return points
    return collect
