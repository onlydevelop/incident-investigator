---
id: PM-003
type: postmortem
title: Consumer rebalance storm during volatility spike
date: 2026-06-21
duration_minutes: 44
severity: sev2
services: [order-risk-service]
root_cause_category: capacity
related_runbooks: [RB-002, RB-005]
---

# PM-003: Consumer rebalance storm during volatility spike

## Summary
A sharp BTC move raised tick volume about 8 times. The marker consumer wrote one position update per tick, synchronously, inside the poll loop. Processing a batch took longer than `max.poll.interval.ms` (60 s), so consumers were kicked from the group, triggering rebalances that stopped all consumption. Lag grew to 1.9 million messages.

## Impact
- Position marks up to 22 minutes stale.
- Displayed P&L wrong during the most volatile hour of the month.
- Order pricing unaffected (it reads Redis).

## Timeline (UTC)
- 02:10 Tick rate rises from about 400/s to about 3,200/s.
- 02:14 First `max.poll.interval.ms exceeded` log; consumer leaves group.
- 02:14 to 02:40 Rebalance roughly every 70 s; consumers spend most time rejoining.
- 02:18 `TickConsumerLagHigh` fires.
- 02:25 On-call scales to 6 replicas. More members means longer rebalances; lag grows faster.
- 02:44 Tempo shows each message's DB span at 15 to 30 ms; batch of 500 takes too long.
- 02:47 Hotfix: conflate ticks to latest per symbol per batch, one bulk update per poll.
- 02:54 Lag drains; membership stable.

## Root cause
Per-tick synchronous writes made processing time scale with tick rate. Under load, batches exceeded the poll interval.

## Contributing factors
- Scaling up during a rebalance storm made it worse.
- No alert on rebalance frequency.

## Action items
- Conflation plus bulk upsert per poll. Done.
- `max.poll.records` lowered to 200. Done.
- Alert on more than 3 rebalances in 10 min. Done.
- RB-002 notes: check rebalances before scaling. Done.
