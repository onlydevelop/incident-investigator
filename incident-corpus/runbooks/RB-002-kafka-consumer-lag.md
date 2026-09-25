---
id: RB-002
type: runbook
title: Kafka consumer lag on md.ticks.v1
services: [order-risk-service]
components: [kafka, postgresql]
alerts: [TickConsumerLagHigh]
symptoms: [positions mispriced, P&L jumps when lag clears, mark age rising]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-002: Kafka consumer lag on md.ticks.v1

## Alert
`TickConsumerLagHigh`: `sum(kafka_consumergroup_lag{consumergroup="order-risk-marker"}) > 5000` for 2 min.

## Impact
Positions are marked with old prices. P&L shown to the desk is wrong and jumps when the consumer catches up. Order pricing is **not** affected, because orders read prices from Redis, not from the consumer.

## Triage
1. Is lag on all partitions or a few?
   ```promql
   sum by (partition) (kafka_consumergroup_lag{consumergroup="order-risk-marker"})
   ```
   - One or two partitions: go to RB-008 (hot partition).
   - All partitions: continue.
2. Did the input rate rise, or did consumption slow down?
   ```promql
   rate(md_ticks_published_total[1m])
   ```
   A volatility spike can multiply tick rate 5 to 10 times.
3. Check for rebalances in logs:
   ```logql
   {service="order-risk-service"} |~ "rebalanc|max.poll|revoked|assigned"
   ```
   Repeated revoke/assign cycles mean consumers are timing out. See PM-003.
4. Check per-message processing time in Tempo: find `md.ticks.v1 process` spans and look at the child DB span. Slow position updates are the usual cause.
5. Describe the group directly:
   ```bash
   kubectl -n trading exec deploy/kafka-tools -- kafka-consumer-groups.sh \
     --bootstrap-server kafka:9092 --describe --group order-risk-marker
   ```

## Mitigation
- Rate spike, healthy consumers: scale consumers up to partition count (12).
  ```bash
  kubectl -n trading scale deploy/order-risk-service --replicas=4
  ```
- Rebalance storm: raise `max.poll.interval.ms` or reduce `max.poll.records`, then restart.
- Slow DB writes: see RB-005; batch position updates per poll instead of per tick.

## Verify
Lag trending to zero and stable consumer membership for 10 min.
