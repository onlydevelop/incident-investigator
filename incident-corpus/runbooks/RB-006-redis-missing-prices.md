---
id: RB-006
type: runbook
title: Redis memory pressure and missing prices
services: [order-risk-service, market-data-service]
components: [redis]
alerts: [RedisEvictions, RedisMemoryHigh]
symptoms: [intermittent NO_PRICE rejects, some symbols missing]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-006: Redis memory pressure and missing prices

## Alert
- `RedisEvictions`: `increase(redis_evicted_keys_total[5m]) > 0`.
- `RedisMemoryHigh`: `redis_memory_used_bytes / redis_memory_max_bytes > 0.9`.

## How it looks
Orders fail with `NO_PRICE` intermittently, often for less-traded contracts. With `allkeys-lru`, Redis evicts the least recently used keys, which are exactly the illiquid strikes that update rarely.

## Triage
1. Confirm evictions vs. expiry. A key can also vanish because its 30 s TTL expired when no tick arrived.
   ```promql
   increase(redis_evicted_keys_total[10m])
   increase(redis_expired_keys_total[10m])
   ```
   - Evictions rising: memory problem, continue here.
   - Only expiries: market data stopped for those symbols; go to RB-001.
2. What is using memory?
   ```bash
   kubectl -n trading exec deploy/redis -- redis-cli --bigkeys
   kubectl -n trading exec deploy/redis -- redis-cli INFO memory
   ```
   Look for keys without TTL (debug caches, leftover order-book snapshots) growing unbounded.
3. Check whether Redis restarted recently (all keys lost at once). See PM-005.
   ```promql
   changes(redis_uptime_in_seconds[1h] < 60)
   ```

## Mitigation
- Delete the unbounded key family if it is non-critical, or add a TTL to it.
- Raise `maxmemory` if the node allows.
- Short-term: market-data can re-seed all latest prices on its next tick per symbol; illiquid ones stay missing until they trade, so consider a snapshot re-seed on startup.

## Verify
Evictions at zero and `NO_PRICE` rejects back to baseline.
