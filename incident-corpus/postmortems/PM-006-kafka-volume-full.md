---
id: PM-006
type: postmortem
title: Kafka volume fills with a week of ticks
basis: scenario
duration_minutes: 71
severity: sev2
services: [market-data-service, order-service]
root_cause_category: capacity
related_runbooks: [RB-007, RB-002, RB-009]
---

# PM-006: Kafka volume fills with a week of ticks

## Summary
The `kafka-topics` Job creates `market-data.ticker` with the broker's default retention: 7 days, no size limit. For a strategy test the symbol list grew to about 120 contracts, and the topic took in roughly 3 GB a day. That filled the broker's 2 GiB volume in under a day. The broker stopped with `No space left on device` and went into CrashLoopBackOff, because the disk was still full on every restart.

## Impact
- 71 minutes of frozen position prices.
- symbols-api's `/tickers` stayed fresh: delta-ticker writes Redis independently of Kafka.
- Ticks not delivered within 30 s were dropped by the producer (`Message timed out`).

## Timeline (UTC)
- 03:12 `data-kafka-0` reaches 100%; the broker logs `No space left on device` and halts.
- 03:12 `md_ticks_published_total` goes flat; `md_kafka_produce_errors_total{reason="delivery"}` climbs.
- 03:13 position-updater logs `kafka_error` warnings.
- 04:05 A user notices positions stale while `/tickers` is live.
- 04:12 On-call finds kafka-0 in CrashLoopBackOff and `kubelet_volume_stats_used_bytes` at capacity.
- 04:15 The `local-path` volume can't be expanded. With the broker down, the topic can't be deleted either.
- 04:18 Kafka scaled to 0, PVC `data-kafka-0` deleted, Kafka scaled back to 1.
- 04:21 `kubectl apply -k deploy/k8s` re-runs the topics Job; `market-data.ticker` is recreated.
- 04:23 delta-ticker's producer reconnects on its own; position-updater restarted to rejoin. Prices update.

## Root cause
Retention sized by broker defaults rather than by a 2 GiB volume, and no alert on broker disk usage.

## What went well
- The split between fresh `/tickers` and frozen positions pointed at Kafka within minutes of looking.
- Losing the topic cost nothing: old ticks are useless, and the updater starts from `latest`.

## Action items
- The topics Job sets `retention.ms=21600000` (6 h) and `retention.bytes` on `market-data.ticker`. Open.
- Alert at 75% and 90% of `data-kafka-0`. Open.
- RB-007 checks broker disk first for storage errors. Done.
