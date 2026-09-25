---
id: RB-005
type: runbook
title: PostgreSQL connection pool exhaustion
services: [order-risk-service]
components: [postgresql]
alerts: [DbPoolSaturated, PostgresConnectionsHigh]
symptoms: [latency spike then timeouts, RISK_TIMEOUT rejects, pool timeout errors]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-005: PostgreSQL connection pool exhaustion

## Alert
- `DbPoolSaturated`: `db_pool_in_use / db_pool_size > 0.9` for 2 min.
- `PostgresConnectionsHigh`: `sum(pg_stat_activity_count) > 85` (of `max_connections=100`).

## How it looks
Latency climbs first, then requests fail with pool timeout errors (`QueuePool limit ... reached` for SQLAlchemy, `TooManyConnectionsError` for asyncpg). The DB itself may be mostly idle.

## Triage
1. Who holds connections, and in what state?
   ```sql
   SELECT application_name, state, count(*), max(now() - query_start) AS oldest
   FROM pg_stat_activity WHERE datname = 'trading'
   GROUP BY 1, 2 ORDER BY 3 DESC;
   ```
   - Many `idle in transaction`: code opened a transaction and did slow work (network call, Kafka produce) before committing.
   - Few long `active` queries: a heavy query is holding connections. See PM-004.
   - Many short `active` queries: plain load; pool too small for current traffic.
2. Check total pool demand: replicas × pool size (+ overflow) must stay below `max_connections` minus a margin for admin tools. Scaling replicas can itself cause this incident.
3. Check the slow-query log and locks:
   ```sql
   SELECT pid, wait_event_type, wait_event, left(query, 80) FROM pg_stat_activity WHERE wait_event_type = 'Lock';
   ```

## Mitigation
- Kill a runaway query: `SELECT pg_terminate_backend(<pid>);`
- Idle-in-transaction leak: set `idle_in_transaction_session_timeout = '30s'` as a guard; fix the code path.
- Load: raise pool size only if the DB has headroom; otherwise add PgBouncer.
- Never raise replicas without recalculating total connections.

## Verify
`db_pool_in_use / db_pool_size` below 0.7 and no pool timeout errors for 10 min.
