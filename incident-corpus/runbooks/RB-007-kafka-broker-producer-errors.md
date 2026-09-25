---
id: RB-007
type: runbook
title: Kafka broker unavailable / producer errors
services: [market-data-service]
components: [kafka]
alerts: [KafkaUnderReplicatedPartitions, TickProduceErrors, KafkaBrokerDown]
symptoms: [ticks received but not published, NotEnoughReplicas errors, consumer starved]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-007: Kafka broker unavailable / producer errors

## Alert
- `TickProduceErrors`: `rate(md_kafka_produce_errors_total[2m]) > 0`.
- `KafkaUnderReplicatedPartitions`: `sum(kafka_topic_partition_under_replicated_partition) > 0`.

## How it looks
`md_ticks_received_total` keeps rising but `md_ticks_published_total` flattens. Redis prices may still be fresh (market-data writes Redis directly), so orders work while position marking stalls. This split is a strong signal the problem is in Kafka, not the feed.

## Triage
1. Broker status:
   ```bash
   kubectl -n trading get pods -l app=kafka
   kubectl -n trading exec deploy/kafka-tools -- kafka-topics.sh --bootstrap-server kafka:9092 --describe --under-replicated-partitions
   ```
2. Producer error types in logs:
   ```logql
   {service="market-data-service"} |= "KafkaError" | json
   ```
   - `NOT_ENOUGH_REPLICAS`: fewer than 2 in-sync replicas; one broker down plus a slow one.
   - `MSG_TIMED_OUT` / `_TRANSPORT`: broker unreachable.
   - `KafkaStorageException` / disk errors: go straight to broker disk usage (PM-006).
3. Broker disk:
   ```promql
   kubelet_volume_stats_used_bytes{persistentvolumeclaim=~"data-kafka-.*"} / kubelet_volume_stats_capacity_bytes
   ```

## Mitigation
- Crashed broker: check its logs, restart the pod, and wait for ISR to recover before touching others.
- Disk full: lower retention on `md.ticks.v1` (ticks are replayable), then expand the PVC.
- Do not lower `min.insync.replicas` to "fix" produce errors; it trades an outage for silent data loss.

## Verify
Under-replicated partitions at zero, produce errors at zero, published rate matches received rate.
