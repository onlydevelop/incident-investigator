---
id: PM-007
type: postmortem
title: Missing PodMonitor hides a blocked position-updater
basis: scenario
duration_minutes: 46
severity: sev2
services: [order-service]
root_cause_category: observability
related_runbooks: [RB-011, RB-002, RB-005]
---

# PM-007: Missing PodMonitor hides a blocked position-updater

## Summary
After the cluster was rebuilt, `make k8s-deploy` ran before `make obs-up`. The `infra-exporters` PodMonitor is only applied when its CRD exists, so it was skipped. App metrics arrived over OTLP as usual, but Prometheus had no `pg_*`, `redis_*` or `kafka_consumergroup_lag` series. Two days later a `psql` session left open in a transaction held a row lock on `positions`. position-updater's `UPDATE` for that symbol waited on the lock, and because the updater is single-threaded, every symbol stopped updating. The lag panel showed "No data", which was read as "no lag".

## Impact
- 46 minutes with no price updates on any position.
- New positions stayed `pending`.

## Timeline (UTC)
- 09:30 A debugging session runs `BEGIN; UPDATE positions SET qty = 2 WHERE id = 3;` in `psql` and is left open.
- 09:31 The next tick for that symbol blocks on the row lock; the poll loop stops for all partitions.
- 09:31 `position_updater_ticks_total` rate drops to zero.
- 09:40 The consumer lag panel shows "No data".
- 10:05 On-call follows RB-002. `kafka_consumergroup_lag` returns nothing, and `up{job="kafka-exporter"}` doesn't exist; the PodMonitor is missing.
- 10:10 `kafka-consumer-groups.sh` shows growing lag. `pg_stat_activity` shows the `psql` session `idle in transaction` since 09:30 and the updater waiting on `Lock`.
- 10:16 The `psql` session is terminated; the updater drains the backlog in seconds.
- 10:20 `make -C deploy/k8s apply` creates the PodMonitor.

## Root cause
An open transaction held a row lock, and one blocked `UPDATE` stops the single-threaded updater completely. The missing PodMonitor removed the metric that would have shown it.

## What went wrong
- "No data" and "zero" look the same on a dashboard.

## Action items
- `idle_in_transaction_session_timeout = '60s'` on the `portfolio` database. Open.
- A `lock_timeout` on the updater's connections, so a blocked `UPDATE` fails as `db_error` and the loop moves on. Open.
- Alert on `absent(up{job="kafka-exporter"})`, `absent(up{job="postgres"})` and `absent(up{job="redis"})`. Open.
- RB-011 lists the missing PodMonitor case. Done.
