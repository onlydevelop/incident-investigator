---
id: PM-007
type: postmortem
title: OTel Collector OOM hides a latency incident
date: 2026-08-30
duration_minutes: 71
severity: sev2
services: [otel-collector, order-risk-service]
root_cause_category: observability
related_runbooks: [RB-011, RB-003]
---

# PM-007: OTel Collector OOM hides a latency incident

## Summary
During a load test, trace volume rose about 15 times. The OTel Collector had no `memory_limiter` processor and was OOMKilled repeatedly. At the same time, order-risk latency degraded from a slow Redis node. Because metrics flowed through the collector, the latency alert never fired. The incident was noticed 48 minutes late, from user reports.

## Impact
- 71 minutes of degraded order latency (p99 about 900 ms).
- 48 minutes with no metrics, traces, or logs from the trading namespace.

## Timeline (UTC)
- 14:00 Load test starts with 100% trace sampling.
- 14:06 Collector OOMKilled; restarts every 2 to 3 minutes.
- 14:09 Redis node begins swapping; order latency rises.
- 14:09 to 14:57 Dashboards flat; no alerts. `up{job="otel-collector"}` flapped, but nothing alerted on it.
- 14:57 Desk reports slow order acks.
- 15:03 On-call finds collector crashlooping; uses `kubectl logs` and `redis-cli --latency` directly.
- 15:11 Redis moved to a healthy node; latency normal.
- 15:20 Collector limits raised; telemetry restored.

## Root cause
Collector memory had no limiter or headroom, and there was no alert on telemetry freshness. The Redis issue was the user-facing cause; the collector failure hid it.

## Action items
- `memory_limiter` at 80% of the container limit; tail sampling at 10% during load. Done.
- Dead-man's-switch alert: fires if no metrics received from `trading` for 3 min, routed outside the collector. Done.
- RB-011 created: verify telemetry freshness before trusting flat dashboards. Done.
