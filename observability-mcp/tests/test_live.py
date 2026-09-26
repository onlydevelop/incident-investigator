"""The real server binary over stdio, against the running observability stack, the way an MCP
client launches it. Skipped unless Prometheus answers (make obs-up, and the app stack deployed)."""
import shutil
import sys
from pathlib import Path

import anyio
import httpx
import pytest
from mcp import Client, StdioServerParameters

from observability_mcp import config

pytestmark = pytest.mark.live


def _reachable() -> bool:
    try:
        return httpx.get(f"{config.PROMETHEUS_URL}/-/ready", timeout=2).is_success
    except httpx.HTTPError:
        return False


if not _reachable():
    pytest.skip(f"Prometheus not reachable at {config.PROMETHEUS_URL}", allow_module_level=True)


@pytest.fixture(scope="module")
def results():
    """Runs a short investigation in one stdio session: metrics, then logs, then a trace."""
    command = shutil.which("observability-mcp", path=str(Path(sys.executable).parent))
    params = StdioServerParameters(command=command, args=[])

    async def run():
        out = {}
        async with Client(params) as client:
            async def call(name, **args):
                result = await client.call_tool(name, args)
                assert not result.is_error, result.content[0].text
                return result.structured_content
            out["tools"] = [t.name for t in (await client.list_tools()).tools]
            out["up"] = await call("prometheus_query", query='up{job=~"postgres|redis|kafka-exporter"}')
            out["range"] = await call("prometheus_query_range", query="sum by (job) (rate(md_ticks_received_total[5m]))", start="30m")
            out["metrics"] = await call("prometheus_label_values", match='{__name__=~"md_.*"}')
            out["services"] = await call("loki_label_values", label="service_name")
            out["logs"] = await call("loki_query", query='{k8s_namespace_name="incident-investigator"} |= "trace_id"', start="6h", limit=5)
            out["search"] = await call("tempo_search", query='{resource.service.name="position-updater"}', start="6h", limit=1)
            if out["search"]["traces"]:
                out["trace"] = await call("tempo_get_trace", trace_id=out["search"]["traces"][0]["trace_id"])
        return out
    return anyio.run(run)


def test_all_tools_listed(results):
    assert len(results["tools"]) == 8


def test_infra_exporters_are_up(results):
    jobs = {s["labels"]["job"]: s["value"] for s in results["up"]["series"]}
    assert jobs == {"postgres": 1.0, "redis": 1.0, "kafka-exporter": 1.0}


def test_app_metrics_are_discoverable(results):
    assert "md_ticks_received_total" in results["metrics"]["values"]
    assert results["range"]["series"], "no tick rate: is delta-ticker streaming?"


def test_app_logs_are_searchable(results):
    assert {"orders-api", "position-updater", "delta-ticker"} <= set(results["services"]["values"])
    assert results["logs"]["line_count"] > 0


def test_a_tick_trace_spans_both_services(results):
    if "trace" not in results:
        pytest.skip("no position-updater traces in the last 6h")
    services = {s["service"] for s in results["trace"]["spans"]}
    assert {"delta-ticker", "position-updater"} <= services
    assert results["trace"]["spans"][0]["depth"] == 0
