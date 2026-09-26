---
id: RB-011
type: runbook
title: Telemetry gap (OTel Collector, exporters)
services: [otel-collector, market-data-service, order-service]
components: [opentelemetry, prometheus, loki, tempo, podmonitor]
alerts: [OtelExportFailures, TelemetryStale]
symptoms: [flat or missing metrics, No data panels, no traces, alerts silent]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-011: Telemetry gap (OTel Collector, exporters)

## Why this matters
When telemetry stops, dashboards go flat and alerts stay silent. That looks exactly like "everything is fine". Any investigation, human or AI, must first confirm its data is current before drawing conclusions from it.

Alertmanager is disabled in this stack, so a firing alert is only visible on Prometheus's `/alerts` page or through the `prometheus_alerts` tool. Nobody gets paged.

## Signals
- The newest data point in a panel is minutes old while the pods are clearly running: `time() - timestamp(md_ws_connected)`.
- `{__name__=~"otelcol_exporter_send_failed_.*"}` rising: the collector can't reach Loki, Tempo or Prometheus.
- `up{namespace="observability"} == 0` for any collector or backend target.

## Triage
1. Which signals are missing?
   - **App metrics missing, infra metrics present**: the collector is down, or the apps can't reach `OTEL_EXPORTER_OTLP_ENDPOINT`. The apps log export errors and keep running.
   - **Infra metrics missing, app metrics present** (`pg_*`, `redis_*`, `kafka_consumergroup_lag`): the `infra-exporters` PodMonitor isn't applied, usually because the app stack was deployed before the observability stack (PM-007).
   - **Only logs missing**: the collector's filelog receiver or Loki.
   - **Only traces missing**: Tempo ingestion or the trace exporter.
2. Is the collector up? It is a DaemonSet with a 384 MiB limit, and its `memory_limiter` refuses data at 80% of that.
   ```bash
   kubectl -n observability get ds otel-collector
   kubectl -n observability logs ds/otel-collector --tail=100
   ```
3. Cross-check with a source that bypasses the collector: `kubectl logs`, `psql`, `kafka-consumer-groups.sh`, `redis-cli`, `curl /tickers`.
4. Not every missing series is a gap. Counters such as `md_kafka_produce_errors_total` and `md_cache_write_errors_total` only appear after their first increment, and `md_tick_age_seconds` only has series for symbols that ticked since the ticker started.
5. Retention limits what you can look back on: Prometheus 2 days, Loki 72 h, Tempo 48 h.

## Mitigation
- Collector or backends missing: `make obs-up` from the repo root.
- PodMonitor missing: `make -C deploy/k8s apply` once the observability stack is installed.
- Collector refusing data: raise its memory limit; `memory_limiter` follows it.

## Verify
Fresh data in all three signals, infra exporter targets `up`, and send failures at zero.

## Note for investigations
If an incident overlaps a telemetry gap, state that clearly in the findings and use direct sources for the gap window.
