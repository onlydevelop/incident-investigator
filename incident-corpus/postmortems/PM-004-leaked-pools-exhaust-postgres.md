---
id: PM-004
type: postmortem
title: Leaked connection pools exhaust Postgres after an outage
basis: observed
date: 2026-09-26
duration_minutes: 10
severity: sev2
services: [order-service]
root_cause_category: resource-leak
related_runbooks: [RB-005, RB-004, RB-009]
---

# PM-004: Leaked connection pools exhaust Postgres after an outage

## Summary
While the stack was being started and redeployed, Postgres was unavailable from about 01:22 to 01:28 UTC. orders-api builds its `PositionStore` on the first request, and the readiness probe calls `/health` every 5 s. Each probe opened a new psycopg `ConnectionPool`; when schema creation failed, the pool was not closed and kept reconnecting in the background. One orders-api process reached `pool-55`. When Postgres came back, every leaked pool connected at once, and new clients got `FATAL:  sorry, too many clients already`.

## Impact
- About 10 minutes with no ready orders-api pod: positions could not be created or read.
- position-updater, starting at 01:27, could not get connections either.
- Local paper trading only; no positions were lost.

## Timeline (UTC, 2026-09-26)
- 01:22:15 delta-ticker starts; Redis and Postgres are not reachable yet (PM-005).
- 01:22:30 Two orders-api pods (a rollout was in progress) log `postgres_unavailable` on `/health` every 5 s: `Postgres unavailable: couldn't get a connection after 5.00 sec`.
- 01:23 to 01:27 postgres-exporter `up` is 0. psycopg.pool warnings peak at about 160 per minute.
- 01:27 position-updater starts after its init container sees Postgres.
- 01:28 Postgres is back on a new pod IP; `sum(pg_stat_activity_count)` is 92 of 100.
- 01:28 to 01:31 `sorry, too many clients already` in orders-api and position-updater. Leaked pools log `reconnection attempt in pool 'pool-39' failed after 300.0 sec`.
- 01:31 All apps redeployed with the fix; old pods exit and their connections close.
- 01:32 Connections back to 5; errors stop.

## Root cause
`PositionStore.from_url()` opened a pool with `min_size=1` and let the schema error propagate without closing it. With lazy creation per request and a 5 s readiness probe, every probe during the outage leaked one pool, and each pool held a connection as soon as Postgres returned.

## What went wrong
- The error looked like Postgres capacity, but Postgres was nearly idle: almost every connection belonged to an idle leaked pool.
- Restarting Postgres would not have helped; the leaked pools reconnect immediately.
- The pool numbers in log messages (`pool-55`) were the clearest signal. One process should only ever have `pool-1`.

## Action items
- `from_url()` closes the pool if schema creation fails (`store.py`, commit c7e0f18). Done.
- Alert when `sum(pg_stat_activity_count)` passes 80 of `max_connections` 100. Open.
- RB-005 triage groups connections by `client_addr` and looks for pool names above `pool-1`. Done.
