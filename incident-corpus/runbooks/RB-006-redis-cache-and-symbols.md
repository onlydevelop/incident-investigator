---
id: RB-006
type: runbook
title: Redis down, missing tickers or lost symbol list
services: [market-data-service, order-service]
components: [redis, delta-ticker, symbols-api]
alerts: [RedisDown, TickerCacheWriteErrors]
symptoms: [/tickers empty or 404, symbols-api 503, POST /positions 502, ticker subscribed to nothing]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-006: Redis down, missing tickers or lost symbol list

## Alert
- `RedisDown`: `redis_up == 0` or `absent(redis_up)` for 1 min.
- `TickerCacheWriteErrors`: `rate(md_cache_write_errors_total[5m]) > 0`.

## What Redis holds
- `ticker:latest:<symbol>`: the latest payload, TTL 10 s. Only symbols-api's `/tickers` reads it. Nothing in the position path reads Redis, so a Redis outage does not stop positions from pricing.
- `ticker:symbols`: the subscription list, no expiry. It survives restarts through AOF (`--appendonly yes`) on a 1 GiB volume.

No `maxmemory` is set (`redis_memory_max_bytes` is 0), so Redis never evicts; it grows until the 256 MiB container limit and gets OOMKilled.

## Triage
1. Is Redis up, and did it restart? `redis_up`, and a reset in `redis_uptime_in_seconds`.
2. What does each app see?
   - delta-ticker logs `cache_write_failed` and counts `md_cache_write_errors_total`, but keeps publishing to Kafka.
   - symbols-api returns 503 `Redis unavailable: ...` and goes unready, so orders-api returns 502 on every create (RB-004).
   - The ticker's 30 s refresh fails with `symbols_read_failed` and keeps its current subscriptions.
3. Is the symbol list intact?
   ```bash
   kubectl -n incident-investigator exec redis-0 -c redis -- redis-cli SMEMBERS ticker:symbols
   kubectl -n incident-investigator exec postgres-0 -- psql -U portfolio -d portfolio -tAc \
     "SELECT DISTINCT symbol FROM positions ORDER BY 1"
   ```
   Every symbol with an open or pending position must be in the set. A missing one means someone removed it (PM-002).
4. A missing `ticker:latest:*` key usually expired 10 s after the symbol's last tick (RB-001); evictions stay at 0 without `maxmemory`.
5. If Redis was down when the ticker started, it logs `Loaded symbols from ticker:symbols: []` and `No symbols configured yet` even though the set is intact (PM-005).

## Mitigation
- Re-subscribe symbols from the positions table: `curl -X PUT http://market-data.localhost/symbols/<symbol>` for each symbol from step 3.
- Redis OOMKilled: raise the limit, or set `maxmemory` with `volatile-ttl` so only the TTL'd ticker keys can be evicted, never the symbol set.

## Verify
`SMEMBERS` covers every position's symbol, `/tickers` lists them, and cache write errors are at zero.
