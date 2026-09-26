---
id: RB-003
type: runbook
title: orders-api latency SLO breach
services: [order-service, market-data-service]
components: [orders-api, symbols-api, postgresql, redis]
alerts: [OrdersApiLatencyHigh, DbPoolWaiting]
symptoms: [slow POST /positions, slow GETs, readiness probe timeouts]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-003: orders-api latency SLO breach

## Alert
`OrdersApiLatencyHigh`: p99 of `POST /positions` above 500 ms for 5 min.
```promql
histogram_quantile(0.99, sum by (le, http_route) (rate(http_server_request_duration_seconds_bucket{job="incident-investigator/orders-api"}[5m])))
```

## How the request works
`POST /positions` inserts the row, then calls symbols-api's `PUT /symbols/{symbol}` **inside the same transaction**, and commits only if that call succeeds. POST /positions keeps its Postgres transaction open while it calls symbols-api (up to the 5 s `MARKET_DATA_TIMEOUT_SECONDS`), so a slow symbols-api holds pool connections and slows every other request, including `/health`.

## Triage
1. Which route is slow? Split the query above by `http_route`. If GETs are slow too, suspect the pool.
2. Open a slow trace in Tempo: `{resource.service.name="orders-api" && name="POST /positions" && duration > 500ms}`. Its children are the `INSERT`, the outgoing `PUT` to symbols-api, and symbols-api's `PUT /symbols/{symbol}` with a Redis `SADD` under it.
3. Match the slow span to a runbook:

| Slow span | Likely cause | Next step |
|---|---|---|
| `PUT` to symbols-api / `SADD` | symbols-api or Redis slow | RB-006 |
| Gap before `INSERT` starts | Waiting for a pool connection (5 per process) | RB-005 |
| `INSERT` itself | Postgres lock wait or I/O | RB-005 |
| Every span evenly slow | CPU throttling or node pressure | Check `container_cpu_cfs_throttled_periods_total` |

4. Confirm pool waits: `max(db_pool_requests_waiting{job="incident-investigator/orders-api"}) > 0`.
5. Correlate with deploys: did a release land shortly before? See RB-010.

## Mitigation
- symbols-api or Redis slow: fix that side; restarting orders-api does not help, it just refills the pool with waiting requests.
- Do not raise `MARKET_DATA_TIMEOUT_SECONDS`: a longer timeout holds each connection longer and makes pool exhaustion worse.
- Pool waits with a healthy symbols-api: follow RB-005.
- orders-api runs one replica. Adding replicas adds pools of 5 connections each; check the total against `max_connections` first (RB-005).

## Verify
p99 below 500 ms for 15 min, `db_pool_requests_waiting` at 0, and no readiness failures on orders-api.
