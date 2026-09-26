---
id: RB-007
type: runbook
title: Kafka broker unavailable / producer errors
services: [market-data-service, order-service]
components: [kafka, delta-ticker, position-updater]
alerts: [TickProduceErrors, KafkaBrokerDown, KafkaVolumeFilling]
symptoms: [ticks received but not published, Message timed out, positions frozen while /tickers is fresh]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-007: Kafka broker unavailable / producer errors

## Alert
- `TickProduceErrors`: `sum(rate(md_kafka_produce_errors_total[2m])) > 0`. The series only exists after the first error, so an absent series means zero.
- `KafkaBrokerDown`: `kafka_brokers < 1` or `up{job="kafka-exporter"} == 0`.
- `KafkaVolumeFilling`: `data-kafka-0` above 75% of capacity.

## How it looks
`md_ticks_received_total` keeps rising but `md_ticks_published_total` flattens. symbols-api's `/tickers` stays fresh (delta-ticker writes Redis independently), while position prices freeze. This split is a strong signal the problem is in Kafka, not the feed.

There is one broker and the topic has replication factor 1: there is no failover, and any broker restart is an outage for `market-data.ticker`.

## Triage
1. Broker status:
   ```bash
   kubectl -n incident-investigator get pod kafka-0
   kubectl -n incident-investigator describe pod kafka-0 | grep -A5 "Last State"
   kubectl -n incident-investigator logs kafka-0 --previous | tail -50
   ```
   `OOMKilled` means the 512 MB heap plus JVM overhead exceeded the 1 GiB limit.
2. Producer errors by reason:
   ```promql
   sum by (reason) (rate(md_kafka_produce_errors_total[5m]))
   ```
   - `delivery` with `Message timed out`: the broker was unreachable for more than 30 s (`message.timeout.ms`). Those ticks are dropped, not replayed.
   - `queue_full`: the producer's local queue filled during a long outage.
   - `produce` or `Unknown topic`: the topic is missing. Auto-create is off; the `kafka-topics` Job creates it.
   ```logql
   {service_name="delta-ticker"} | json | event=~"publish_failed|librdkafka"
   ```
3. Broker disk: `KafkaStorageException` or `No space left on device` in the broker log means go straight to disk usage (PM-006).
   ```promql
   kubelet_volume_stats_used_bytes{persistentvolumeclaim="data-kafka-0"} / kubelet_volume_stats_capacity_bytes{persistentvolumeclaim="data-kafka-0"}
   ```

## Mitigation
- Broker crashed or OOMKilled: let it restart and raise `limits.memory` in `deploy/k8s/infra/kafka.yaml` if it was OOM.
- Topic missing: re-run the Job with `kubectl apply -k deploy/k8s`. It deletes itself 5 minutes after finishing, so each apply recreates it.
- Disk full: set a short `retention.ms` on `market-data.ticker`; old ticks are useless because the updater starts from `latest`. The `local-path` volume can't be expanded in place.
- Do not restart delta-ticker to fix produce errors: its producer reconnects on its own, and a restart drops the websocket and any queued ticks.

## Verify
Produce errors at zero, published rate matches received rate, and consumer lag drains (RB-002).
