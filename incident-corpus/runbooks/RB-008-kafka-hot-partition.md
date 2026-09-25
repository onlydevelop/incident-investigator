---
id: RB-008
type: runbook
title: Kafka hot partition
services: [order-risk-service, market-data-service]
components: [kafka]
alerts: [TickConsumerLagSkewed]
symptoms: [one contract lags while others fine, lag on single partition]
severity: sev3
last_reviewed: 2026-09-25
---

# RB-008: Kafka hot partition

## Alert
`TickConsumerLagSkewed`: max partition lag more than 10 times the median for 5 min.

## How it looks
Most positions re-mark on time, but a few contracts (usually the most traded near-dated ATM strikes) show stale marks. Total lag may look acceptable because the other partitions are empty.

## Why it happens
Ticks are keyed by contract symbol, so all ticks for one symbol land on one partition and are processed by one consumer. When a single contract dominates volume, its partition becomes the bottleneck. Two busy symbols hashing to the same partition make it worse.

## Triage
1. Find the hot partition:
   ```promql
   topk(3, kafka_consumergroup_lag{consumergroup="order-risk-marker"})
   ```
2. Find which symbols map to it: in Tempo, filter consume spans by `messaging.kafka.partition` and group by `symbol` attribute; or sample the partition:
   ```bash
   kafka-console-consumer.sh --bootstrap-server kafka:9092 --topic md.ticks.v1 --partition <n> --max-messages 200 --property print.key=true | cut -f1 | sort | uniq -c | sort -rn
   ```
3. Check the consumer that owns it for slow processing (same span analysis as RB-002).

## Mitigation
- Short-term: the marker only needs the latest price per symbol, so conflate: drop intermediate ticks for the same symbol within a poll batch.
- Longer-term: separate topics or dedicated partitions for top-volume contracts. Do not switch to random keys; per-symbol ordering is required.

## Verify
Partition lag distribution even, stale marks cleared.
