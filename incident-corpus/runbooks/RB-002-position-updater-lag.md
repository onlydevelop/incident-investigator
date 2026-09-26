---
id: RB-002
type: runbook
title: position-updater lag on market-data.ticker
services: [order-service]
components: [position-updater, kafka, postgresql]
alerts: [PositionUpdaterLagHigh, PositionUpdaterTickAgeHigh]
symptoms: [current_price behind the market, positions open late, tick age rising in position-updater]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-002: position-updater lag on market-data.ticker

## Alert
- `PositionUpdaterLagHigh`: `max(kafka_consumergroup_lag{consumergroup="order-service.position-updater"}) > 500` for 5 min.
- `PositionUpdaterTickAgeHigh`: p99 of `position_updater_tick_age_seconds` above 10 s for 5 min.
  ```promql
  histogram_quantile(0.99, sum by (le) (rate(position_updater_tick_age_seconds_bucket[5m])))
  ```

## Impact
`current_price` trails the market and pending positions open late. Entry prices stay correct: a position opens on the first tick whose exchange time is at or after its creation, so a late tick still gives the right entry. symbols-api's `/tickers` is **not** affected, because it reads Redis, not Kafka.

## Triage
1. Lag per partition:
   ```promql
   kafka_consumergroup_lag{consumergroup="order-service.position-updater"}
   ```
   A value of `-1` means the group has never committed on that partition (no subscribed symbol hashes there yet). It is not lag. Filter with `> 0`; never `sum` the raw series.
2. Is anyone consuming? `kafka_consumergroup_members{consumergroup="order-service.position-updater"}` at 0 means the updater is down, crashlooping, or still in its `wait-for-postgres` init container.
3. Input versus output:
   ```promql
   sum(rate(md_ticks_published_total[1m]))
   sum by (result) (rate(position_updater_ticks_total[1m]))
   ```
   A rising `result="db_error"` means Postgres is failing (RB-005). Those ticks are dropped, not retried; the next tick for the symbol catches up.
4. Slow processing: in Tempo, `{resource.service.name="position-updater" && name="market-data.ticker process" && duration > 100ms}`, then look at the two child `UPDATE` spans. The loop is single-threaded, so one `UPDATE` waiting on a row lock stops every partition. See PM-007.
5. Describe the group directly:
   ```bash
   kubectl -n incident-investigator exec kafka-0 -- /opt/kafka/bin/kafka-consumer-groups.sh \
     --bootstrap-server kafka:9092 --describe --group order-service.position-updater
   ```

## Mitigation
- Updater not running: check `kubectl -n incident-investigator logs deploy/position-updater -c wait-for-postgres`, then RB-009.
- Blocked or slow `UPDATE`s: find the lock holder with RB-005 step 3 and terminate it.
- Throughput: scale to at most 3 replicas, one per partition; a 4th sits idle. Each replica adds a pool of up to 5 Postgres connections.
- Large backlog after an outage: replay is harmless, because `apply_tick` ignores ticks older than a position's prices. To skip it, scale the updater to 0 and reset the group's offsets with `--reset-offsets --to-latest --execute`.

## Verify
Lag near zero on every partition that has a committed offset, and tick age p99 below 2 s for 10 min.
