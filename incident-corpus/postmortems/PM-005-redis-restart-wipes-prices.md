---
id: PM-005
type: postmortem
title: Redis restart wipes latest prices
date: 2026-07-28
duration_minutes: 19
severity: sev2
services: [order-risk-service, market-data-service]
root_cause_category: missing-persistence
related_runbooks: [RB-006, RB-009]
---

# PM-005: Redis restart wipes latest prices

## Summary
The Redis pod was rescheduled during routine node maintenance. Redis ran without persistence, so every `price:{symbol}` key was lost. Liquid contracts refilled within seconds from new ticks, but illiquid strikes stayed missing until they next traded, some for 19 minutes. Orders on those contracts failed with `NO_PRICE`.

## Impact
- 19 minutes of `NO_PRICE` rejects on about 40% of listed contracts (mostly far-dated and deep OTM strikes).
- Liquid contracts affected for under 10 seconds.

## Timeline (UTC)
- 11:30 Node drained for kernel update; Redis pod moves to another node.
- 11:30 Redis starts empty.
- 11:31 `NO_PRICE` rejects spike, then drop as liquid symbols refill.
- 11:33 Rejects settle at about 6% of orders; no alert fires (threshold 10%).
- 11:45 Desk reports specific strikes untradeable.
- 11:47 On-call sees no evictions (`redis_evicted_keys_total` flat) but `redis_uptime_in_seconds` reset at 11:30.
- 11:49 Market-data restarted to trigger a full snapshot re-seed.
- 11:49 All prices present; rejects normal.

## Root cause
Redis held the only copy of latest prices with no persistence and no re-seed on startup. Market-data only writes a key when a tick arrives.

## What went wrong
- The first check (evictions) was the wrong one; restart and eviction look the same to the order path.

## Action items
- Market-data watches Redis restarts and re-seeds from an exchange REST snapshot. Done.
- PodDisruptionBudget and scheduled maintenance windows for Redis. Done.
- Alert on `NO_PRICE` rate by symbol count, not just order share. Done.
- RB-006 triage now checks uptime reset alongside evictions. Done.
