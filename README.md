# incident-investigator

[![CI](../../actions/workflows/ci.yml/badge.svg?branch=main)](../../actions/workflows/ci.yml?query=branch%3Amain)
[![build](../../raw/badges/build.svg)](../../actions/workflows/ci.yml?query=branch%3Amain)
[![lint](../../raw/badges/lint.svg)](../../actions/workflows/ci.yml?query=branch%3Amain)
[![smoke test](../../raw/badges/smoke.svg)](../../actions/workflows/ci.yml?query=branch%3Amain)

[![market-data-service tests](../../raw/badges/tests-market-data-service.svg)](market-data-service/README.md)
[![market-data-service coverage](../../raw/badges/coverage-market-data-service.svg)](market-data-service/README.md)
[![order-service tests](../../raw/badges/tests-order-service.svg)](order-service/README.md)
[![order-service coverage](../../raw/badges/coverage-order-service.svg)](order-service/README.md)

Paper-trading stack for Delta Exchange options.

| Directory | What it is |
|---|---|
| [`infra/`](infra/README.md) | Shared Redis, Kafka and Postgres on the `portfolio` Docker network |
| [`market-data-service/`](market-data-service/README.md) | Streams Delta tickers to Kafka and Redis; symbols API on `:8000` |
| [`order-service/`](order-service/README.md) | Paper positions in Postgres, priced from the Kafka ticker feed; positions API on `:8001` |
| [`deploy/k8s/`](deploy/k8s/README.md) | The whole stack on the local k3s (Rancher Desktop), with Kustomize |
| [`deploy/observability/`](deploy/observability/README.md) | Prometheus, Grafana, Loki, Tempo and the OTel Collector on the same k3s, with Helm (`make obs-up`) |
| [`observability-mcp/`](observability-mcp/README.md) | MCP server for Prometheus queries, Loki log search and Tempo trace lookup. Registered for Claude Code in [`.mcp.json`](.mcp.json) |
| [`rag/`](rag/README.md) | Chunking and hybrid retrieval over the [incident corpus](incident-corpus/README.md) of runbooks and postmortems |
| [`agent/`](agent/README.md) | LangGraph agent that investigates incidents with the observability MCP tools and the RAG retriever, and writes a report |
| [`scripts/smoke-test.sh`](scripts/smoke-test.sh) | End-to-end check of a running stack, on Compose or k3s |

## Running the stack

There are two ways to run the whole stack locally. They keep separate data and can run side by side, but together they use about 2 GB of memory, so usually run one at a time.

### On the local k3s (Rancher Desktop)

Run these from the repo root; `make help` lists them. They call the targets in [`deploy/k8s/Makefile`](deploy/k8s/Makefile).

| Command | What it does |
|---|---|
| `make k8s-start` | Start the stack and wait until every pod is ready. Images are built only if they don't exist yet. Works after `k8s-stop`, and on a fresh cluster |
| `make k8s-stop` | Scale every workload to 0. Config, ingress and data volumes are kept, so the next `k8s-start` comes back with the same positions and symbols |
| `make k8s-status` | Pods, services, ingress and volumes |
| `make k8s-deploy` | Rebuild both images and roll them out. Use this after changing code; `k8s-start` keeps the images it already has |

The APIs are then at <http://orders.localhost/docs> and <http://market-data.localhost/docs>.

These need `make -C deploy/k8s <target>`:

| Command | What it does |
|---|---|
| `smoke` | Run the end-to-end smoke test in the cluster |
| `logs APP=<name>` | Follow one workload's logs, e.g. `APP=position-updater` |
| `diagnose` | Show pods that aren't ready, and recent warning events |
| `delete` | Remove all workloads but keep the data volumes |
| `delete-all` | Remove the namespace **and all its data** |

For a start-to-finish example, see [the k3s walkthrough](deploy/k8s/README.md#walkthrough-deploy-trade-clean-up). It deploys the stack, creates positions and checks them, deletes them, and removes the stack. [`deploy/k8s/README.md`](deploy/k8s/README.md) has the rest of the details.

### With Docker Compose

| Command | What it does |
|---|---|
| `make -C order-service up` | Start everything: shared infra, market-data-service and order-service |
| `make -C order-service down` | Stop order-service. Infra and market-data-service keep running |
| `make -C market-data-service down` | Stop market-data-service |
| `make -C infra down` | Stop Redis, Kafka and Postgres. Data volumes are kept |

The APIs are then at <http://localhost:8001/docs> (orders) and <http://localhost:8000/docs> (market data). Each service's README lists the rest of its `make` targets.

## Observability

On k3s, with the [observability stack](deploy/observability/README.md) installed (`make obs-up`), every component sends metrics to Prometheus, logs to Loki and traces to Tempo. You can explore all three in Grafana at <http://grafana.localhost>. On Docker Compose the apps still write JSON logs, but they export no metrics or traces.

| Component | Metrics (Prometheus) | Logs (Loki) | Traces (Tempo) |
|---|---|---|---|
| **delta-ticker** | `md_ws_connected`, `md_ticks_received_total`, `md_ticks_published_total`, `md_kafka_produce_errors_total{reason}`, `md_cache_write_errors_total`, `md_tick_age_seconds{symbol}` | JSON: `symbols_changed`, `ws_open`/`ws_closed`/`ws_error`, `publish_failed`, `cache_write_failed`, librdkafka messages. Ticks only at `LOG_LEVEL=DEBUG` | `v2/ticker process` per websocket update, with the Redis `SET` and the `market-data.ticker publish` under it. The trace continues into Kafka through `traceparent` |
| **symbols-api** | `http_server_request_duration_seconds` by route, method and status | JSON access log, without `/health` and `/docs` | One span per request, with the Redis calls under it. Continues orders-api's trace |
| **orders-api** | `http_server_request_duration_seconds`, `positions_created_total{side}`, `db_pool_size` / `_in_use` / `_max` / `_requests_waiting` | JSON: `position_created`, `position_rejected{reason}`, `position_deleted`, `postgres_unavailable`, access log | One span per request, with the Postgres queries and the call to symbols-api under it |
| **position-updater** | `position_updater_ticks_total{result}`, `positions_opened_total`, `position_updater_tick_age_seconds` (histogram), `db_pool_*` | JSON: `positions_opened`, `bad_message`, `tick_failed{reason}`, `kafka_error`, librdkafka messages | `market-data.ticker process` per message, continuing delta-ticker's trace, with the Postgres `UPDATE`s under it |
| **Postgres** | postgres-exporter sidecar: `pg_stat_activity_count{state}`, `pg_settings_max_connections`, database sizes, locks | Container stdout | Client spans only, from the apps |
| **Redis** | redis-exporter sidecar: `redis_memory_used_bytes`, `redis_evicted_keys_total`, `redis_keyspace_hits_total` / `_misses_total`, commands | Container stdout | Client spans only, from the apps |
| **Kafka** | kafka-exporter: `kafka_consumergroup_lag{consumergroup,topic,partition}`, topic and partition offsets, broker count | Container stdout | Producer and consumer spans only, from the apps |
| **Across services** | Tempo derives `traces_spanmetrics_*` (rate, errors and duration per span) and `traces_service_graph_*` (calls between services) | Every line gets `k8s_namespace_name`, `k8s_pod_name`, `service_name` | The service graph covers user → orders-api → symbols-api → Redis, orders-api → Postgres, and delta-ticker → Kafka → position-updater → Postgres |
| **Observability stack** | The collector's `otelcol_*` metrics, plus Loki, Tempo, Prometheus, Grafana, kube-state-metrics and node-exporter | Container stdout | — |

- **How to query:**
  - Every app log line has `time`, `level`, `service`, `event`, `message`, and `trace_id` when it was written inside a span. The fields are also Loki structured metadata, so `{service_name="orders-api"} | event="position_rejected"` works without `| json`.
  - App metrics carry `job="incident-investigator/<service>"`. Infra metrics carry `job="postgres"`, `"redis"` or `"kafka-exporter"`.
- **Querying it from a model:** [`observability-mcp`](observability-mcp/README.md) exposes all three backends as MCP tools.
- **Where the details are:** each metric is described in [deploy/k8s/README.md](deploy/k8s/README.md#observability). How it's collected is in [deploy/observability/README.md](deploy/observability/README.md).

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main`, every pull request, and on demand. A new push cancels the run already in progress for the same branch.

| Job | What it checks |
|---|---|
| Lint | `ruff check` with [`ruff.toml`](ruff.toml): pyflakes, likely bugs and import order. Also checks that every compose file is valid, runs shellcheck on `scripts/`, and renders the k8s manifests with `kubectl kustomize` |
| Test market-data-service | Unit tests with branch coverage. Fails below 80%; it's at 83% now |
| Test order-service | Unit tests against a real Postgres 18 service container, with branch coverage. Fails below 95%; it's at 100% now |
| Test observability-mcp | The MCP server's tools against fake Prometheus, Loki and Tempo, with branch coverage. Fails below 90%; it's at 98% now |
| Build | Builds each service's runtime image with a layer cache. Nothing is pushed |
| Smoke test | Runs after the four jobs above pass. Starts the real stack (infra, the symbols API, order-service) and runs `scripts/smoke-test.sh` |
| Publish badges | Pushes and manual runs on `main` only. Turns the results above into the README badges (see [Badges](#badges)) |

The smoke test doesn't start `delta-ticker`, so CI never depends on Delta Exchange being reachable. It publishes its own ticks to Kafka instead. It creates a position, checks that the first tick sets `entry_price` from the ask and the next sets `current_price` from the bid, then deletes the position.

Each test job writes a coverage table to the run's summary page. It also uploads `coverage.xml`, the HTML report and `junit.xml` as artifacts, kept for 14 days.

### Badges

The badges at the top of this README come from the latest run on `main`.

- **CI** is GitHub's own badge for the whole workflow.
- **The others** are made by the `badges` job with [`scripts/badge.py`](scripts/badge.py), from that run's job results, JUnit files and `coverage.xml` files. They're force-pushed to the `badges` branch as a single commit, so they never add commits to `main`.
- **When a job fails,** its badge turns red. The tests badge then shows how many tests failed, and a coverage report that was never produced shows as grey "unknown".
- **Links are relative** (`../../raw/badges/...`), so they work in any fork or under any repo name, and in a private repo for anyone who can view it.
- **Before the first run on `main`,** the `badges` branch doesn't exist, so these images appear broken.

[Dependabot](.github/dependabot.yml) opens weekly PRs for Python dependencies, base images and actions.

### Running the same checks locally

```sh
docker run --rm -v "$PWD":/src -w /src ghcr.io/astral-sh/ruff:0.16.9 check .   # lint
make -C market-data-service coverage
make -C order-service coverage             # starts infra if needed, for Postgres
make -C order-service up && scripts/smoke-test.sh      # smoke test on Docker Compose
make k8s-start && make -C deploy/k8s smoke             # smoke test on k3s
```

On Docker Compose, if `delta-ticker` is running while you run the smoke test, it may briefly try to subscribe the test symbol `C-SMOKETEST-1-311299` on Delta. To avoid that, stop it with `docker stop delta-ticker` first. The k3s `smoke` target does this for you.
