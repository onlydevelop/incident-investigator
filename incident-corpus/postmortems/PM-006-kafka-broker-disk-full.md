---
id: PM-006
type: postmortem
title: Kafka broker disk full
date: 2026-08-12
duration_minutes: 63
severity: sev2
services: [market-data-service, order-risk-service]
root_cause_category: capacity
related_runbooks: [RB-007, RB-002]
---

# PM-006: Kafka broker disk full

## Summary
A test topic created for replay-mode experiments had `retention.ms=-1` (keep forever) and received several days of replayed ticks. Its partitions filled broker 2's 20 GiB volume. Broker 2 went offline; with one broker already slow from compaction, several `md.ticks.v1` partitions dropped below `min.insync.replicas=2`, and market-data produces failed with `NOT_ENOUGH_REPLICAS`.

## Impact
- 63 minutes of partial tick loss to Kafka (4 of 12 partitions).
- Positions for affected symbols not re-marked.
- Orders unaffected: Redis prices stayed fresh because market-data writes Redis before producing.

## Timeline (UTC)
- 07:40 Broker 2 disk reaches 100%; broker shuts down with `KafkaStorageException`.
- 07:41 `KafkaUnderReplicatedPartitions` and `TickProduceErrors` fire.
- 07:50 On-call restarts broker 2; it fails again immediately (disk still full).
- 08:10 `kubelet_volume_stats` shows the PVC full; `kafka-log-dirs.sh` points to `replay.ticks.test`.
- 08:25 Test topic deleted; broker 2 starts; ISR recovers by 08:43.

## Root cause
No disk alert on broker volumes, and topic creation allowed unlimited retention.

## What went well
- Split symptoms (Redis fresh, marks stale) pointed to Kafka quickly.

## Action items
- Alert at 75% and 90% broker volume usage. Done.
- Topic creation policy: max retention 7 days; test topics on a separate cluster. Done.
- RB-007 lists disk as the first check for storage exceptions. Done.
