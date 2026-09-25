---
id: PM-004
type: postmortem
title: P&L report query starves the connection pool
date: 2026-07-09
duration_minutes: 27
severity: sev2
services: [order-risk-service]
root_cause_category: noisy-workload
related_runbooks: [RB-005, RB-003]
---

# PM-004: P&L report query starves the connection pool

## Summary
A new end-of-day P&L report endpoint was added to order-risk-service. It ran a large aggregate over `orders` and `positions` using the same connection pool as order handling. Several desk users opened the report at once; each request held a connection for 40 to 90 seconds. The pool emptied and new orders waited, then failed with `RISK_TIMEOUT`.

## Impact
- p99 order latency above 5 s for 27 minutes.
- 38% of orders rejected with `RISK_TIMEOUT`.

## Timeline (UTC)
- 16:00 Desk opens end-of-day report; 6 concurrent requests.
- 16:01 `db_pool_in_use / db_pool_size` reaches 1.0 on both pods.
- 16:02 `OrderLatencyP99High` and `DbPoolSaturated` fire.
- 16:06 On-call checks Postgres CPU: 35%, looks healthy; suspects network.
- 16:15 `pg_stat_activity` shows 12 long-running `active` queries from the report.
- 16:19 Queries terminated; report endpoint disabled by feature flag.
- 16:27 Latency and rejects normal.

## Root cause
A slow analytical query shared the pool with latency-critical order traffic. There was no statement timeout and no pool separation.

## What went wrong
- Healthy DB CPU misled the first diagnosis; the bottleneck was pool slots, not the DB.

## Action items
- Separate pool (size 2) and read replica for reporting. Done.
- `statement_timeout = 2s` on the order pool. Done.
- Index on `positions(account_id, updated_at)`; report now runs in 3 s. Done.
- RB-005 triage step: group connections by state and query. Done.
