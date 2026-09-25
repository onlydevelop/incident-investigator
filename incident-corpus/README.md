# Incident Corpus: Paper-Trading Options Desk

Synthetic runbooks and postmortems for the capstone's RAG layer (Week 2). Every document is fictional and written for evaluation use.

## System covered

| Component | Details |
|---|---|
| `market-data-service` (Python) | Streams BTC option ticks from an exchange WebSocket (or replay mode), publishes to Kafka topic `md.ticks.v1` (key = contract symbol, 12 partitions), writes latest price to Redis key `price:{symbol}` (TTL 30s) |
| `order-risk-service` (Python) | REST `POST /orders`; prices orders from Redis, runs margin/risk checks, stores `orders` and `positions` in PostgreSQL db `trading`; consumer group `order-risk-marker` marks positions to market from `md.ticks.v1` |
| Kafka | 3 brokers, replication factor 3, `min.insync.replicas=2` |
| Redis | Single instance, `maxmemory-policy allkeys-lru` |
| PostgreSQL | Single primary, `max_connections=100` |
| Platform | k3s cluster, namespace `trading`; OTel Collector → Prometheus, Loki, Tempo, Grafana |

## SLOs

| SLO | Target |
|---|---|
| Order ack latency | p99 < 200 ms |
| Tick freshness | price age < 2 s for listed contracts |
| Order reject rate (non-margin reasons) | < 1% over 5 min |

## Metric and label conventions

These names are assumed throughout the corpus. Rename them here and in the documents if your instrumentation differs, so retrieval and agent tool calls stay consistent.

| Metric | Service | Meaning |
|---|---|---|
| `md_ws_connected` | market-data | 1 if WebSocket connected |
| `md_ws_reconnects_total` | market-data | Reconnect attempts |
| `md_ticks_received_total` / `md_ticks_published_total` | market-data | Ticks in / ticks produced to Kafka |
| `md_kafka_produce_errors_total` | market-data | Producer delivery failures |
| `md_tick_age_seconds{symbol}` | market-data | Now minus exchange timestamp of latest tick |
| `orders_total{status}` | order-risk | Orders by `accepted` / `rejected` |
| `order_rejects_total{reason}` | order-risk | `STALE_PRICE`, `NO_PRICE`, `INSUFFICIENT_MARGIN`, `RISK_TIMEOUT`, `LIMIT_EXCEEDED` |
| `http_server_request_duration_seconds` | order-risk | OTel HTTP latency histogram |
| `risk_check_duration_seconds` | order-risk | Risk check latency histogram |
| `db_pool_in_use` / `db_pool_size` | order-risk | SQLAlchemy/asyncpg pool usage |
| `kafka_consumergroup_lag{consumergroup,topic,partition}` | kafka-exporter | Consumer lag |
| `redis_evicted_keys_total`, `redis_memory_used_bytes` | redis-exporter | Redis memory |
| `pg_stat_activity_count{state}` | postgres-exporter | Connections by state |
| `otelcol_exporter_send_failed_spans` | otel-collector | Export failures |

Common log fields (Loki, JSON): `service`, `level`, `event`, `symbol`, `order_id`, `trace_id`, `reason`.
Trace context is propagated through Kafka message headers (`traceparent`), so a tick's produce and consume spans share a trace.

## Documents

| ID | Type | Title |
|---|---|---|
| RB-001 | Runbook | Stale market data / WebSocket feed down |
| RB-002 | Runbook | Kafka consumer lag on `md.ticks.v1` |
| RB-003 | Runbook | Order ack latency SLO breach |
| RB-004 | Runbook | Order reject rate spike |
| RB-005 | Runbook | PostgreSQL connection pool exhaustion |
| RB-006 | Runbook | Redis memory pressure and missing prices |
| RB-007 | Runbook | Kafka broker unavailable / producer errors |
| RB-008 | Runbook | Kafka hot partition |
| RB-009 | Runbook | Pod CrashLoopBackOff / OOMKilled |
| RB-010 | Runbook | Bad release: detect and roll back |
| RB-011 | Runbook | Telemetry gap (OTel Collector) |
| RB-012 | Runbook | Clock skew and negative tick age |
| PM-001 | Postmortem | Reconnect storm hits exchange rate limit |
| PM-002 | Postmortem | Risk-limit config typo blocks all large orders |
| PM-003 | Postmortem | Consumer rebalance storm during volatility spike |
| PM-004 | Postmortem | P&L report query starves the connection pool |
| PM-005 | Postmortem | Redis restart wipes latest prices |
| PM-006 | Postmortem | Kafka broker disk full |
| PM-007 | Postmortem | OTel Collector OOM hides a latency incident |
| PM-008 | Postmortem | Node clock drift after reboot rejects orders |

## Keeping evals honest

The postmortems describe incidents that share *symptoms* with the golden-set scenarios but have *different root causes*. For example, PM-001 is stale prices from rate-limited reconnects, while the golden-set case is a silent disconnect with no reconnect.

This is deliberate. If the corpus contained the exact answer to each golden-set case, the eval would measure retrieval, not reasoning. Keep new golden-set incidents out of this corpus, or split them into a held-out set.
