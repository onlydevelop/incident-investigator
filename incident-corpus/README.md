# Incident Corpus: Paper-Trading Options Desk

Runbooks and postmortems for the capstone's RAG layer (Week 2). They describe the stack in this repository as it is deployed on the local k3s cluster: the same service names, Redis keys, Kafka topic, metrics, log events and trace spans the code produces.

## System covered

| Component | Details |
|---|---|
| `market-data-service` | `delta-ticker` streams Delta Exchange `v2/ticker` updates over a websocket; for each tick it writes Redis key `ticker:latest:<symbol>` (TTL 10 s) and publishes to Kafka topic `market-data.ticker` (key = symbol, 3 partitions). One replica, `Recreate` strategy, no reconnect loop, no probes. `symbols-api` (FastAPI, `:8000`) manages the subscription set `ticker:symbols` and serves `/tickers` from Redis |
| `order-service` | `orders-api` (FastAPI, `:8001`): `POST /positions` inserts a `pending` row and subscribes its symbol through symbols-api in one transaction (502 if the subscribe fails). `position-updater`: consumer group `order-service.position-updater` on `market-data.ticker`; opens pending positions (buy at ask, sell at bid) and sets `current_price` from later ticks, by exchange time |
| Kafka | One KRaft broker (`kafka-0`), replication factor 1, auto-create off, 2 GiB volume, 512 MB heap in a 1 GiB limit |
| Redis | `redis-0`, AOF on, no `maxmemory`, 256 MiB limit, 1 GiB volume |
| PostgreSQL | `postgres-0`, database `portfolio`, table `positions`, `max_connections=100`; each app process has a psycopg pool of at most 5 |
| Platform | Rancher Desktop k3s (single node), namespace `incident-investigator`; OTel Collector → Prometheus, Loki, Tempo, Grafana in namespace `observability`. Alertmanager is disabled |

## SLOs

| SLO | Target |
|---|---|
| Tick freshness | `md_tick_age_seconds` below 10 s for every subscribed, listed symbol |
| Position pricing | p99 `position_updater_tick_age_seconds` below 2 s |
| Position create latency | p99 `POST /positions` below 500 ms |
| orders-api availability | 5xx below 1% over 5 min |

## Metric, log and trace conventions

| Metric | From | Meaning |
|---|---|---|
| `md_ws_connected` | delta-ticker | 1 while the websocket is open |
| `md_ticks_received_total` / `md_ticks_published_total` | delta-ticker | Ticks from Delta / ticks Kafka acknowledged |
| `md_kafka_produce_errors_total{reason}` | delta-ticker | `queue_full`, `produce`, `delivery` |
| `md_cache_write_errors_total` | delta-ticker | Failed Redis writes |
| `md_tick_age_seconds{symbol}` | delta-ticker | Now minus the latest tick's exchange time; only for symbols that ticked since the process started |
| `http_server_request_duration_seconds` | symbols-api, orders-api | Latency histogram by `http_route`, `http_response_status_code` |
| `positions_created_total{side}`, `positions_opened_total` | orders-api, position-updater | Positions created / opened by a tick |
| `position_updater_ticks_total{result}` | position-updater | `applied`, `bad_message`, `db_error` |
| `position_updater_tick_age_seconds` | position-updater | Histogram, exchange time to processing, negatives clamped to 0 |
| `db_pool_size`, `db_pool_in_use`, `db_pool_max`, `db_pool_requests_waiting` | orders-api, position-updater | psycopg pool |
| `kafka_consumergroup_lag{consumergroup,topic,partition}`, `kafka_consumergroup_members`, `kafka_brokers` | kafka-exporter | Lag is `-1` for a partition with no committed offset |
| `pg_stat_activity_count{datname,state}`, `pg_settings_max_connections` | postgres-exporter | Connections |
| `redis_up`, `redis_uptime_in_seconds`, `redis_memory_used_bytes`, `redis_memory_max_bytes`, `redis_expired_keys_total`, `redis_evicted_keys_total` | redis-exporter | Redis |
| `traces_spanmetrics_*` | Tempo | Per-span rate, errors, latency |

App metrics have `job="incident-investigator/<app>"`; exporters have `job="postgres"`, `"redis"`, `"kafka-exporter"`.

Logs are JSON lines with `time`, `level`, `service`, `event`, `message`, event fields (`symbol`, `position_id`, `reason`, ...) and `trace_id`. Select them in Loki with `{service_name="<app>"}`. Useful events: `ws_open`, `ws_closed`, `no_symbols`, `symbols_read_failed`, `symbols_changed`, `publish_failed`, `cache_write_failed`, `position_created`, `position_rejected`, `postgres_unavailable`, `positions_opened`, `tick_failed`, `kafka_error`, and `psycopg.pool` for the pool's own warnings.

A tick is one trace: `v2/ticker process` (delta-ticker) → `SET` and `market-data.ticker publish` → `market-data.ticker process` (position-updater, via the `traceparent` header) → `UPDATE`. `POST /positions` has `INSERT`, `PUT` to symbols-api, and symbols-api's `PUT /symbols/{symbol}` with `SADD`.

Alert names in the runbooks are the rules these documents assume. The repo doesn't ship PrometheusRules yet, and with Alertmanager disabled, a rule that is added only shows on Prometheus's `/alerts` page.

## Documents

| ID | Type | Title |
|---|---|---|
| RB-001 | Runbook | Stale market data / WebSocket feed down |
| RB-002 | Runbook | position-updater lag on `market-data.ticker` |
| RB-003 | Runbook | orders-api latency SLO breach |
| RB-004 | Runbook | orders-api error spike (502 / 503) |
| RB-005 | Runbook | PostgreSQL connection pool exhaustion |
| RB-006 | Runbook | Redis down, missing tickers or lost symbol list |
| RB-007 | Runbook | Kafka broker unavailable / producer errors |
| RB-008 | Runbook | Positions stuck in pending |
| RB-009 | Runbook | Pod CrashLoopBackOff / OOMKilled |
| RB-010 | Runbook | Bad release: detect and roll back |
| RB-011 | Runbook | Telemetry gap (OTel Collector, exporters) |
| RB-012 | Runbook | Clock skew and negative tick age |
| PM-001 | Postmortem | WebSocket close exits delta-ticker into CrashLoopBackOff |
| PM-002 | Postmortem | Clearing the symbol list freezes open positions |
| PM-003 | Postmortem | Half-open websocket keeps the ticker connected but silent |
| PM-004 | Postmortem | Leaked connection pools exhaust Postgres after an outage |
| PM-005 | Postmortem | Ticker starts before Redis and reports no symbols |
| PM-006 | Postmortem | Kafka volume fills with a week of ticks |
| PM-007 | Postmortem | Missing PodMonitor hides a blocked position-updater |
| PM-008 | Postmortem | VM clock behind after host sleep delays stale-feed detection |

## Observed and scenario postmortems

Each postmortem's frontmatter has a `basis`:
- **`observed`** (PM-004, PM-005): reconstructed from telemetry of a real run on 2026-09-26. The metrics, log lines and times are real. The trigger ("the stack was being started and redeployed") is inferred from pod churn in the logs.
- **`scenario`** (the rest): incidents the current code and manifests allow, written as if they had happened. The mechanisms are real (for example, the ticker has no reconnect loop and no ping, the topic has default retention, and position-updater clamps negative ages). The times and numbers are illustrative, so these postmortems have no `date`.

Action items marked `Done` are true of the repository today. `Open` items are not implemented yet.

## Keeping evals honest

The postmortems describe incidents that share *symptoms* with the golden-set scenarios but have *different root causes*. For example, PM-003 is a half-open socket that stays "connected", while a golden-set case might be a ticker that exits and never comes back.

This is deliberate. If the corpus contained the exact answer to each golden-set case, the eval would measure retrieval, not reasoning. Keep new golden-set incidents out of this corpus, or split them into a held-out set.
