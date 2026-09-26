# Observability stack (local k3s)

Prometheus, Grafana, Loki, Tempo and an OpenTelemetry Collector, installed with Helm into the `observability` namespace of Rancher Desktop's k3s. This is the stack the [incident corpus](../../incident-corpus/README.md) describes: OTel Collector → Prometheus, Loki, Tempo, Grafana.

```sh
make -C deploy/observability up      # install or upgrade everything, wait until ready (from the repo root: make obs-up)
make -C deploy/observability smoke   # send a log, a trace and a metric; read each one back
make -C deploy/observability status  # releases, pods, ingresses, volumes
```

| URL | What |
|---|---|
| <http://grafana.localhost> | Grafana. User `admin`, password from `make grafana-password` |
| <http://prometheus.localhost> | Prometheus UI and HTTP API |
| <http://loki.localhost> | Loki HTTP API (`/loki/api/v1/query_range`, ...) |
| <http://tempo.localhost> | Tempo HTTP API (`/api/v2/traces/<id>`, `/api/search`) |

Traefik serves them on `localhost:80`, the same way as the app ingresses in [`../k8s`](../k8s/README.md). There's no auth on any of them, so keep this to the local cluster.

## How data flows

```
pod log files ──┐
                ├─> otel-collector (DaemonSet) ─┬─> Loki        logs     (OTLP, /otlp)
apps, OTLP ─────┘                               ├─> Tempo       traces   (OTLP gRPC)
                                                └─> Prometheus  metrics  (OTLP receiver)
Tempo metrics generator ── remote write ──> Prometheus   traces_spanmetrics_*, traces_service_graph_*
Prometheus ── scrapes ──> ServiceMonitors/PodMonitors in every namespace
```

- **Logs:** the collector tails every container's log file on the node. You get them without changing the app. Each line is tagged with `k8s_namespace_name`, `k8s_pod_name`, `k8s_container_name`, `service_name` and so on. For JSON lines from the `incident-investigator` namespace, the collector also:
  - turns the fields into structured metadata;
  - sets the record's trace ID, so each line links to its trace.
- **Traces and app metrics:** apps send OTLP to the collector. Set these on the app:
  ```yaml
  env:
    - {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: http://otel-collector.observability:4318}
    - {name: OTEL_SERVICE_NAME, value: orders-api}
  ```
  The Service uses `internalTrafficPolicy: Local`, so each pod reaches the collector on its own node.
- **Scraped metrics:** a `ServiceMonitor` or `PodMonitor` in any namespace is picked up. No `release` label is needed.
- **The `job` label for pushed metrics** is `<service.namespace>/<service.name>`. The collector sets `service.namespace` to the Kubernetes namespace, so for example `job="incident-investigator/orders-api"`.
- **Grafana links the three:**
  - A `trace_id` in a log opens the trace in Tempo.
  - A span opens its service's logs around that time, and its request rate and p99.
  - Exemplars on Prometheus histograms open their trace.
  - Tempo also draws the service graph.

The paper-trading apps and their Redis, Kafka and Postgres are already wired in. [`../k8s/README.md`](../k8s/README.md#observability) lists every metric, log field and trace they produce.

## Files

| File | What |
|---|---|
| [`values/kube-prometheus-stack.yaml`](values/kube-prometheus-stack.yaml) | Prometheus, Grafana (with the Loki and Tempo datasources), kube-state-metrics, node-exporter |
| [`values/loki.yaml`](values/loki.yaml) | Loki single binary on a 5 Gi volume, 72h retention |
| [`values/tempo.yaml`](values/tempo.yaml) | Tempo single binary on a 5 Gi volume, 48h retention, metrics generator on |
| [`values/otel-collector.yaml`](values/otel-collector.yaml) | The collector: pipelines, exporters, and the rule that parses the apps' JSON logs |
| [`extras.yaml`](extras.yaml) | Loki and Tempo ingresses, and a ServiceMonitor for Loki (its chart has none) |
| [`smoke-test.sh`](smoke-test.sh) | The end-to-end check behind `make smoke` |

Chart versions are pinned in the [`Makefile`](Makefile). To upgrade one, bump the version there, run `make render`, then run the component's own target (`make loki` etc.).

## Things to know

- **The Prometheus release is shared.** `kube-prometheus-stack` was installed on this cluster before this directory existed, and other projects' monitors (for example `convoy-services`) scrape into it. The values file keeps every setting the release already had. Keep changes to it additive, and render and diff before upgrading. It stays on chart 88.3.0 on purpose.
- **Tempo's chart moved** from `grafana/tempo` (deprecated) to `grafana-community/tempo`. Chart 3.x ships Tempo 3, so this stays on 2.x for now.
- **Collector component names** follow collector 0.160's naming (`otlp_http`, `otlp_grpc`). The chart rewrites the old `otlphttp`/`otlp` names, but warns that it will stop doing that.
- **`make down`** removes Loki, Tempo and the collector, but keeps their volumes. Prometheus and Grafana stay, because they're shared.
- **Memory:** Loki, Tempo and the collector together use about 270 MiB when idle. Their limits add up to about 1.9 GiB.

## Verified

This is from a real run: `make up` on a cluster that already had kube-prometheus-stack, then `make smoke`:

```
run 8b343907, trace d9ab846c0598b73f5eb910676657f023: sending to http://otel-collector.observability:4318
  sent logs
  sent traces
  sent metrics
ok    Loki has the log
ok    Tempo has the trace
ok    Prometheus has the metric
```

Also checked on the same run:
- All 13 Prometheus scrape targets are up, including Loki, Tempo and the collector.
- Tempo's `traces_spanmetrics_*` and `traces_service_graph_*` series are in Prometheus.
- Loki has pod logs from `kube-system` and `observability`.
- Grafana's health check passes for the Prometheus, Loki and Tempo datasources.
