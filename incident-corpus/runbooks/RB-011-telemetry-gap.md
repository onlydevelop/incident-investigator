---
id: RB-011
type: runbook
title: Telemetry gap (OTel Collector)
services: [otel-collector]
components: [opentelemetry, prometheus, loki, tempo]
alerts: [OtelExportFailures, TargetDown]
symptoms: [flat or missing metrics, no traces, dashboards empty, alerts silent]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-011: Telemetry gap (OTel Collector)

## Why this matters
When telemetry stops, dashboards go flat and alerts stay silent. That looks exactly like "everything is fine". Any investigation, human or AI, must first confirm its data is current before drawing conclusions from it.

## Signals
- `rate(otelcol_exporter_send_failed_spans[5m]) > 0`
- `up{job="otel-collector"} == 0`
- The newest data point in a panel is minutes old while the services are clearly running.

## Triage
1. Is the collector up?
   ```bash
   kubectl -n observability get pods -l app=otel-collector
   kubectl -n observability logs deploy/otel-collector --tail=100
   ```
   Look for `memory_limiter` refusals (collector shedding load), OOM restarts, or exporter errors to Tempo/Loki.
2. Is the gap total or partial?
   - All signals missing: collector down.
   - Only traces: Tempo ingestion or trace exporter failing.
   - Only one service: that service's SDK or its endpoint config (`OTEL_EXPORTER_OTLP_ENDPOINT`).
3. Cross-check with a source that bypasses the collector: `kubectl top pods`, direct logs via `kubectl logs`, the database, Kafka CLI.

## Mitigation
- Collector OOM: raise its memory limit and set `memory_limiter` below the container limit; add a `batch` processor. See PM-007.
- Reduce trace volume with tail sampling during load tests.

## Verify
Fresh data in all three signals; send failures at zero.

## Note for investigations
If an incident overlaps a telemetry gap, state that clearly in the findings and use direct sources for the gap window.
