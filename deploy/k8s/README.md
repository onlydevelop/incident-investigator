# Local k3s deployment

The whole stack runs on Rancher Desktop's k3s in the `incident-investigator` namespace: Redis, Kafka and Postgres, plus both services. It uses plain Kustomize (`kubectl apply -k`), so there's no Helm and nothing extra to install.

```sh
make k8s-start              # from the repo root: start the stack and wait until it's ready
make k8s-stop               # scale everything to 0; config and data are kept

make -C deploy/k8s deploy   # after code changes: rebuild images, apply, restart apps, wait
make -C deploy/k8s smoke    # end-to-end check through the ingress and the in-cluster Kafka
```

| URL | What |
|---|---|
| <http://orders.localhost/docs> | order-service positions API |
| <http://market-data.localhost/docs> | market-data-service symbols and tickers API |

Traefik, which ships with k3s, serves both on `localhost:80`. Any `*.localhost` name resolves to 127.0.0.1 in curl and browsers, so there's no `/etc/hosts` to edit.

This is separate from the Docker Compose stack, and the two can run side by side. They don't share data: each has its own Redis symbol list, Kafka topic and Postgres database.

## Walkthrough: deploy, trade, clean up

This goes through the whole lifecycle once: build and deploy, open two paper positions, watch live prices fill them in, delete them, and remove the stack. Run every command from the repo root.

The output shown is from real runs. Prices, times and ids will differ for you, and some output is trimmed (`...`).

### 0. Prerequisites

- **Rancher Desktop** with Kubernetes enabled and the Docker engine (moby) selected. The kube context must be `rancher-desktop`, or pass `KUBE_CONTEXT=<name>` to every `make` command.
- **`curl` and `jq`.**
- **Symbols must be live:** the examples use `C-BTC-80000-091026` and `P-BTC-80000-091026`, BTC options that expire on 9 Oct 2026. After that date, pick symbols that are still trading from the [Delta Exchange](https://www.delta.exchange/) options chain.

```sh
$ kubectl config current-context
rancher-desktop
```

### 1. Build and deploy

```sh
$ make k8s-deploy
docker build --target runtime -t market-data/delta-ticker:local .../market-data-service
docker build --target runtime -t order-service/order-service:local .../order-service
kubectl --context rancher-desktop apply -k .
namespace/incident-investigator created
secret/postgres-credentials-bf2mm7cmg9 created
...
statefulset.apps/kafka created
job.batch/kafka-topics created
ingress.networking.k8s.io/portfolio created
...
job.batch/kafka-topics condition met
deployment "delta-ticker" successfully rolled out
deployment "symbols-api" successfully rolled out
deployment "orders-api" successfully rolled out
deployment "position-updater" successfully rolled out

orders API:      http://orders.localhost/docs
market-data API: http://market-data.localhost/docs
```

The first run pulls the Redis, Kafka and Postgres images and builds both app images, which takes a few minutes. If something doesn't come up within 120s, the command prints the failing pods and warning events and exits. `make -C deploy/k8s diagnose` prints the same at any time.

Check that everything is running, and that both APIs answer through the ingress:

```sh
$ make k8s-status
NAME                                    READY   STATUS      RESTARTS   AGE
pod/delta-ticker-67b5b65564-22vkj       1/1     Running     0          40s
pod/kafka-0                             1/1     Running     0          2m
pod/kafka-topics-8c29l                  0/1     Completed   0          2m
pod/orders-api-7677f68896-c82tb         1/1     Running     0          40s
pod/position-updater-788bf9457f-l5rxk   1/1     Running     0          40s
pod/postgres-0                          1/1     Running     0          2m
pod/redis-0                             1/1     Running     0          2m
pod/symbols-api-6bdf4bbd5-p8wfp         1/1     Running     0          40s
...

$ curl http://orders.localhost/health
{"status":"ok"}
$ curl http://market-data.localhost/health
{"status":"ok"}
```

On a fresh deploy the ticker has no symbols yet, so it waits. The apps log one JSON object per line (see [Observability](#observability)):

```sh
$ make -C deploy/k8s logs APP=delta-ticker
{"time": "...", "level": "info", "service": "delta-ticker", "event": "symbols_loaded", "message": "Loaded symbols from ticker:symbols: []", "symbols": []}
{"time": "...", "level": "info", "service": "delta-ticker", "event": "startup", "message": "Publishing to Kafka topic market-data.ticker", ...}
{"time": "...", "level": "info", "service": "delta-ticker", "event": "ws_open", "message": "Socket opened"}
{"time": "...", "level": "info", "service": "delta-ticker", "event": "no_symbols", "message": "No symbols configured yet; waiting for the next refresh"}
```

To read only the messages, pipe through `jq -rR 'fromjson? | .message'`.

That's expected. Creating a position subscribes its symbol, as the next step shows.

### 2. Create positions

Open a long call (buy) and a short put (sell). Each request does two things before it returns:
1. Stores the position in Postgres as `pending`.
2. Subscribes its symbol in market-data-service.

If step 2 fails, nothing is stored and the API returns `502`.

```sh
$ BUY=$(curl -s -X POST http://orders.localhost/positions -H 'content-type: application/json' \
    -d '{"symbol": "C-BTC-80000-091026", "side": "buy", "qty": 1}' | tee /dev/stderr | jq .id)
{"id":1,"time":"2026-09-25T17:02:47.078900+05:30","symbol":"C-BTC-80000-091026","side":"buy","qty":1.0,
 "status":"pending","entry_price":null,"entry_time":null,"current_price":null,"current_price_time":null}

$ SELL=$(curl -s -X POST http://orders.localhost/positions -H 'content-type: application/json' \
    -d '{"symbol": "P-BTC-80000-091026", "side": "sell", "qty": 2}' | tee /dev/stderr | jq .id)
{"id":2,"time":"2026-09-25T17:02:47.840403+05:30","symbol":"P-BTC-80000-091026","side":"sell","qty":2.0,
 "status":"pending",...}

$ echo "buy=$BUY sell=$SELL"
buy=1 sell=2
```

Both symbols are now subscribed:

```sh
$ curl -s http://market-data.localhost/symbols | jq .symbols
[
  "C-BTC-80000-091026",
  "P-BTC-80000-091026"
]
```

The API rejects bad input with `422` and stores nothing:

```sh
$ curl -s -X POST http://orders.localhost/positions -H 'content-type: application/json' \
    -d '{"symbol": "BTCUSD", "side": "buy", "qty": 1}' | jq -r '.detail[0].msg'
String should match pattern '^[CP]-[A-Z0-9]+-\d+(\.\d+)?-\d{6}$'
```

### 3. Check them

The ticker picks up new symbols on its next refresh, within 30 seconds:

```sh
$ make -C deploy/k8s logs APP=delta-ticker
{"time": "...", "level": "info", "service": "delta-ticker", "event": "symbols_changed", "message": "Symbols changed: +['C-BTC-80000-091026', 'P-BTC-80000-091026'] -[]", ...}
```

Ticks themselves aren't logged: `md_ticks_received_total` and `md_tick_age_seconds` in Prometheus track them. Set `LOG_LEVEL=DEBUG` on the deployment to log every tick's payload.

The first tick after a position was created opens it:
- a **buy** enters at the **ask**;
- a **sell** enters at the **bid**.

```sh
$ make -C deploy/k8s logs APP=position-updater | jq -rR 'fromjson? | .message'
Consuming market-data.ticker as order-service.position-updater
Opened 1 position(s) in C-BTC-80000-091026 at bid=5543.0 ask=5609.0
Opened 1 position(s) in P-BTC-80000-091026 at bid=596.0 ask=608.0
```

Every tick after that sets `current_price` to the price the position would close at: the **bid** for a buy and the **ask** for a sell.

```sh
$ curl -s http://orders.localhost/positions/$BUY | jq '{status, entry_price, current_price, current_price_time}'
{
  "status": "open",
  "entry_price": 5609.0,
  "current_price": 5544.0,
  "current_price_time": "2026-09-25T17:03:31.889699+05:30"
}
```

List and filter the positions:

```sh
$ curl -s 'http://orders.localhost/positions?status=open' \
    | jq -r '.positions[] | "\(.id)  \(.side)  \(.qty)  \(.symbol)  entry=\(.entry_price)  now=\(.current_price)"'
1  buy  1.0  C-BTC-80000-091026  entry=5609.0  now=5544.0
2  sell  2.0  P-BTC-80000-091026  entry=596.0  now=609.0

$ curl -s 'http://orders.localhost/positions?symbol=P-BTC-80000-091026' | jq .count
1
```

Or look at the table in Postgres directly:

```sh
$ kubectl -n incident-investigator exec -e PGTZ=Asia/Kolkata postgres-0 -- psql -U portfolio -d portfolio -c \
    "SELECT id, symbol, side, qty, status, entry_price, current_price FROM positions ORDER BY id"
 id |       symbol       | side | qty | status | entry_price | current_price
----+--------------------+------+-----+--------+-------------+---------------
  1 | C-BTC-80000-091026 | buy  |   1 | open   |      5609.0 |        5544.0
  2 | P-BTC-80000-091026 | sell |   2 | open   |       596.0 |         609.0
(2 rows)
```

If a position stays `pending` for more than about 30 seconds, see [Troubleshooting](#troubleshooting).

### 4. Delete the positions

```sh
$ curl -s -o /dev/null -w '%{http_code}\n' -X DELETE http://orders.localhost/positions/$BUY
204
$ curl -s -o /dev/null -w '%{http_code}\n' -X DELETE http://orders.localhost/positions/$SELL
204
$ curl -s http://orders.localhost/positions/$BUY
{"detail":"Position 1 not found"}
$ curl -s http://orders.localhost/positions | jq .count
0
```

Deleting a position doesn't unsubscribe its symbol, which may be in use for something else. To stop streaming these two symbols:

```sh
$ for s in C-BTC-80000-091026 P-BTC-80000-091026; do
    curl -s -o /dev/null -w "$s %{http_code}\n" -X DELETE http://market-data.localhost/symbols/$s
  done
C-BTC-80000-091026 204
P-BTC-80000-091026 204
```

The ticker unsubscribes on its next refresh (`Symbols changed: +[] -[...]` in its logs).

### 5. Undeploy

Choose how much to remove:

| Command | Removes | Keeps | Bring it back with |
|---|---|---|---|
| `make k8s-stop` | All pods, by scaling to 0 | Every object, the config, and the data | `make k8s-start` (fast; no rebuild) |
| `make -C deploy/k8s delete` | Workloads, services, ingress, config, secret | The namespace and its volumes: positions, symbols, topics | `make k8s-start` |
| `make -C deploy/k8s delete-all` | The whole `incident-investigator` namespace, **including all data** | Nothing | `make k8s-deploy` (starts empty) |

To remove everything:

```sh
$ make -C deploy/k8s delete-all
kubectl --context rancher-desktop delete namespace incident-investigator --ignore-not-found
namespace "incident-investigator" deleted

$ kubectl get namespace incident-investigator
Error from server (NotFound): namespaces "incident-investigator" not found
```

This deletes the namespace's Postgres, Redis and Kafka volumes, so it can't be undone. The built images stay in Docker. `docker image rm market-data/delta-ticker:local order-service/order-service:local` removes them too.

## What's deployed

| Workload | Kind | Notes |
|---|---|---|
| `redis`, `postgres`, `kafka` | StatefulSet, 1 replica | Each has a PVC on the `local-path` storage class (1, 2 and 2 GiB), so data survives restarts and redeploys. Kafka is single-node KRaft with a 512 MB heap. Redis and Postgres each have a Prometheus exporter as a sidecar |
| `kafka-exporter` | Deployment | Consumer lag and topic offsets for Prometheus |
| `kafka-topics` | Job | Waits for Kafka, then creates `market-data.ticker` (3 partitions) if it's missing. It deletes itself 5 minutes after finishing, so the next `apply` runs it again |
| `delta-ticker` | Deployment, 1 replica, `Recreate` | Only one may run, or every tick would be published twice |
| `symbols-api` | Deployment + Service `:8000` | Ready once Redis answers (`/health`) |
| `orders-api` | Deployment + Service `:8001` | Ready once Postgres answers (`/health`) |
| `position-updater` | Deployment | An init container waits for Postgres, because the updater creates the schema on startup |
| `portfolio` | Ingress (Traefik) | `orders.localhost` goes to `orders-api`, `market-data.localhost` to `symbols-api` |
| `infra-exporters` | PodMonitor, from [`monitoring/`](monitoring) | Only applied when the [observability stack](../observability/README.md) is installed, since the PodMonitor kind comes with it |

Services keep the names used in Docker Compose (`redis`, `kafka`, `postgres`, `symbols-api`). The apps therefore get the same connection settings, from the `app-config` ConfigMap. `DATABASE_URL` is built from the `postgres-credentials` Secret. Both are generated in [`kustomization.yaml`](kustomization.yaml), and a change to either rolls the pods that use it.

The credentials are for local development only (`portfolio`/`portfolio`, the same as Compose).

## Observability

With the [observability stack](../observability/README.md) installed (`make obs-up` from the repo root), every workload here is covered by metrics, logs and traces. Deploy this stack after it, or run `make apply` again, so the PodMonitor gets created.

| Signal | How it gets there | Where to look |
|---|---|---|
| **App metrics** | The apps push over OTLP to the collector (`OTEL_EXPORTER_OTLP_ENDPOINT` in `app-config`) | Prometheus, `job="incident-investigator/<service>"` |
| **Infra metrics** | `postgres-exporter`, `redis-exporter` and `kafka-exporter`, scraped by the `infra-exporters` PodMonitor | Prometheus, `job="postgres"`, `"redis"`, `"kafka-exporter"` |
| **Logs** | Every container's stdout. The apps write JSON lines, which the collector turns into fields | Loki, `{k8s_namespace_name="incident-investigator"}` |
| **Traces** | The apps push over OTLP | Tempo. A request or a tick is one trace across services |

The apps' `service.name` is `delta-ticker`, `symbols-api`, `orders-api` or `position-updater`. Loki calls it `service_name`, and Tempo's span metrics call it `service`.

### Metrics

| Metric | From | What it tells you |
|---|---|---|
| `md_ws_connected` | delta-ticker | 1 while the Delta websocket is open |
| `md_ticks_received_total`, `md_ticks_published_total` | delta-ticker | Ticks in from Delta; ticks Kafka acknowledged |
| `md_kafka_produce_errors_total{reason}` | delta-ticker | `queue_full`, `produce` or `delivery` failures |
| `md_cache_write_errors_total` | delta-ticker | Failed writes of the latest tick to Redis |
| `md_tick_age_seconds{symbol}` | delta-ticker | Now minus the latest tick's exchange time. It keeps rising if a symbol stops updating |
| `http_server_request_duration_seconds` | symbols-api, orders-api | Latency histogram by `http_route`, `http_request_method`, `http_response_status_code` |
| `positions_created_total{side}` | orders-api | Positions created |
| `db_pool_size`, `db_pool_in_use`, `db_pool_max`, `db_pool_requests_waiting` | orders-api, position-updater | The psycopg connection pool |
| `position_updater_ticks_total{result}` | position-updater | `applied`, `bad_message` or `db_error` |
| `positions_opened_total` | position-updater | Pending positions opened by a tick |
| `position_updater_tick_age_seconds` | position-updater | Histogram of how old a tick's exchange time is when the updater applies it |
| `kafka_consumergroup_lag{consumergroup,topic,partition}` | kafka-exporter | The updater's lag on `market-data.ticker` (`-1` for a partition it hasn't committed on yet) |
| `pg_stat_activity_count{datname,state}`, `pg_settings_max_connections` | postgres-exporter | Connections by state, against the limit |
| `redis_memory_used_bytes`, `redis_evicted_keys_total`, `redis_keyspace_hits_total` | redis-exporter | Redis memory and cache behaviour |
| `traces_spanmetrics_*`, `traces_service_graph_*` | Tempo | Rate, errors and duration of every span, and calls between services |

### Logs

Each app log line is one JSON object:
- `time`, `level`, `service`, `event` and `message` on every line.
- The fields relevant to that event, such as `symbol`, `position_id`, `reason` and `topic`.
- `trace_id` and `span_id` when the line was written inside a span.

The collector turns these into Loki structured metadata, so you can filter without parsing:

```logql
{service_name="orders-api"} | event="position_rejected"
{k8s_namespace_name="incident-investigator"} | detected_level=~"ERROR|WARNING"
{service_name="position-updater"} | json | reason="postgres_unavailable"
```

In Grafana, a line's `trace_id` links to the trace in Tempo.

### Traces

- **Creating a position:** `POST /positions` on orders-api has three children:
  - the Postgres INSERT;
  - the HTTP call to symbols-api;
  - symbols-api's handler (from `traceparent`), with the Redis SADD under it.
- **A tick:** `v2/ticker process` on delta-ticker has the Redis SET and `market-data.ticker publish` as children. The position-updater's `market-data.ticker process` continues the same trace from the Kafka message's `traceparent` header, with the Postgres UPDATEs under it.

Health and docs probes aren't traced or access-logged.

## Images

k3s here uses Rancher Desktop's Docker daemon as its container runtime. So `make images` builds `market-data/delta-ticker:local` and `order-service/order-service:local` straight into the runtime, with no registry. The manifests use `imagePullPolicy: Never`, so a missing image fails immediately (`ErrImageNeverPull`) instead of trying to pull from Docker Hub.

The tag stays `:local`, so `make deploy` runs `rollout restart` to move pods onto a freshly built image. After changing code, just run `make deploy` again.

## Hardening

- **Every app container** runs as UID 10001 (`runAsNonRoot`), with a read-only root filesystem, no Linux capabilities, no privilege escalation and the default seccomp profile.
- **Probes:** readiness checks each API's `/health`, which touches Redis or Postgres. Liveness checks `/docs`, so a database outage takes a pod out of the Service instead of restarting it in a loop.
- **Every pod sets `enableServiceLinks: false`.** Kubernetes would otherwise inject variables like `KAFKA_PORT=tcp://10.43.x.x:9092`, which the Kafka image reads as broker config.
- **Every container has requests and limits.** The whole namespace requests about 1.4 GiB of memory.

## Makefile

| Command | Description |
|---|---|
| `make start` | Build the images only if they don't exist yet, apply the manifests, and wait until ready. It works after `stop`, after `delete`, and on a fresh cluster. `apply` resets every workload to its manifest's replica count |
| `make stop` | Scale every Deployment and StatefulSet to 0 and wait for the pods to exit. Everything else stays, including the config, the Secret, the ingress and all PVCs |
| `make deploy` | Force a rebuild with `images`, then `apply`, `restart` and `wait`. Use it after changing code; `start` would keep the old images |
| `make images` | Build both runtime images |
| `make apply` | `kubectl apply -k .` |
| `make restart` | Roll the four app Deployments onto the current `:local` images |
| `make wait` | Wait for the StatefulSets, the topics Job and the apps (up to 120s per step). If anything isn't ready, it runs `diagnose` and fails |
| `make diagnose` | Pods, plus the latest warning events |
| `make status` | Pods, services, ingress and PVCs |
| `make logs APP=<name>` | Follow one workload's logs, e.g. `APP=position-updater` |
| `make smoke` | Run [`scripts/smoke-test.sh`](../../scripts/smoke-test.sh) through the ingress. `delta-ticker` is scaled to 0 during the run so the made-up test symbol is never sent to Delta |
| `make render` | Print the rendered manifests |
| `make delete` | Delete everything except the PVCs |
| `make delete-all` | Delete the namespace, including all data |

It uses the `rancher-desktop` kube context. Override it with `KUBE_CONTEXT=<name>`.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `make wait` fails with `CreateContainerConfigError` | Usually an image built before the Dockerfiles switched to `USER 10001` (`runAsNonRoot` can't verify a user name) | `make deploy` rebuilds the images |
| `ErrImageNeverPull` | The images haven't been built, or were built on a different Docker context | `make images` |
| Kafka pod restarts with `OOMKilled` | The heap plus JVM overhead exceeds the 1 GiB limit | Raise `limits.memory` in [`infra/kafka.yaml`](infra/kafka.yaml) |
| `orders.localhost` returns 404 | The Ingress isn't applied, or Traefik isn't running | `make status`, then `kubectl -n kube-system get pods -l app.kubernetes.io/name=traefik` |
| A position stays `pending` | The in-cluster ticker hasn't picked up the symbol yet (up to 30s), or the symbol isn't trading | `make logs APP=delta-ticker` and look for `Symbols changed` |
