---
id: RB-003
type: runbook
title: Order ack latency SLO breach
services: [order-risk-service]
components: [postgresql, redis]
alerts: [OrderLatencyP99High]
symptoms: [slow order acks, client timeouts, RISK_TIMEOUT rejects]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-003: Order ack latency SLO breach

## Alert
`OrderLatencyP99High`: p99 of `POST /orders` above 200 ms for 5 min.
```promql
histogram_quantile(0.99, sum by (le) (rate(http_server_request_duration_seconds_bucket{service="order-risk-service",http_route="/orders"}[5m])))
```

## Triage
1. Open a slow trace in Tempo (`duration > 200ms`, route `/orders`). The order path has four child spans: Redis price read, risk check, DB insert, DB position update. Find the one that grew.
2. Match the slow span to a runbook:

| Slow span | Likely cause | Next step |
|---|---|---|
| DB acquire / insert | Pool exhausted or lock wait | RB-005 |
| Redis GET | Redis memory pressure or network | RB-006 |
| Risk check (CPU) | Pod CPU throttled or GIL contention | Check `container_cpu_cfs_throttled_periods_total` |
| All spans evenly slow | Node pressure or noisy neighbour | Check node CPU/memory |

3. Correlate with deploys: did a release land shortly before? See RB-010.
4. Check if latency is on all pods or one: split by `pod` label. One slow pod usually means a bad node or a stuck worker.

## Mitigation
- One bad pod: delete it and let it reschedule.
- CPU throttling: raise CPU limit or add replicas; Python services often need more workers rather than more CPU per worker.
- DB or Redis cause: follow the linked runbook.

## Verify
p99 below 200 ms for 15 min and `RISK_TIMEOUT` rejects back to zero.
