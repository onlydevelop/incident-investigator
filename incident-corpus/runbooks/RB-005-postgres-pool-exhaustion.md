---
id: RB-005
type: runbook
title: PostgreSQL connection pool exhaustion
services: [order-service]
components: [postgresql, orders-api, position-updater]
alerts: [PostgresConnectionsHigh, DbPoolWaiting]
symptoms: [503 Postgres unavailable, too many clients already, db_error ticks, readiness failures]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-005: PostgreSQL connection pool exhaustion

## Alert
- `PostgresConnectionsHigh`: `sum(pg_stat_activity_count{datname="portfolio"}) > 80` (of `pg_settings_max_connections` = 100).
- `DbPoolWaiting`: `max(db_pool_requests_waiting) > 0` for 1 min.

## How it looks
There are two different failures, and they need different fixes:
- **A process's own pool is empty**: requests fail with `couldn't get a connection after 5.00 sec` (psycopg_pool's `PoolTimeout`) and orders-api returns 503. Each process has a pool of at most 5 connections. Postgres itself may be nearly idle.
- **The server is full**: psycopg.pool warnings say `FATAL:  sorry, too many clients already`. Total connections reached `max_connections=100`. See PM-004.

Normal is about 5 connections in total with one replica of each app.

## Triage
1. Who holds connections, and in what state? The apps don't set `application_name`, so group by `client_addr` and match it to pod IPs (`kubectl -n incident-investigator get pods -o wide`).
   ```bash
   kubectl -n incident-investigator exec postgres-0 -- psql -U portfolio -d portfolio -c \
     "SELECT client_addr, state, count(*), max(now() - state_change) AS oldest
        FROM pg_stat_activity WHERE datname = 'portfolio' GROUP BY 1, 2 ORDER BY 3 DESC;"
   ```
   - Many `idle in transaction` from orders-api: `POST /positions` is waiting on symbols-api inside its transaction (RB-003).
   - Many `idle` connections from one pod, far more than 5: that process has opened several pools. Look for pool names above `pool-1` in its logs (`reconnection attempt in pool 'pool-39'`). See PM-004.
   - `active` UPDATEs from position-updater that stay active: lock waits; go to step 3.
2. Check total demand: replicas × 5 for orders-api and position-updater, plus the exporter and any `psql` sessions, must stay well below 100. Scaling replicas can itself cause this incident.
3. Find lock waits and their holder:
   ```sql
   SELECT pid, state, wait_event_type, now() - xact_start AS xact_age, left(query, 80)
   FROM pg_stat_activity WHERE datname = 'portfolio' AND (wait_event_type = 'Lock' OR state = 'idle in transaction');
   ```

## Mitigation
- Leaked connections from app pods: restart the apps; the connections close when the old pods exit.
  ```bash
  kubectl -n incident-investigator rollout restart deploy/orders-api deploy/position-updater
  ```
- Kill a lock holder or a stuck session: `SELECT pg_terminate_backend(<pid>);`
- Do not raise `max_connections` as the first fix; it hides a leak rather than stopping it.
- Never raise replicas without recalculating total connections.

## Verify
Total connections back near baseline (about 5), `db_pool_requests_waiting` at 0, and no psycopg.pool warnings for 10 min.
