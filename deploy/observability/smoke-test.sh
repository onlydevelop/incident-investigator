#!/usr/bin/env bash
# End-to-end check of the observability stack: sends one log, one trace and one metric over OTLP to
# the collector (from a throwaway pod, since the collector is only reachable in the cluster), then
# reads each one back from Loki, Tempo and Prometheus through their *.localhost ingresses.
#
#   make -C deploy/observability smoke
set -euo pipefail

KUBECTL=${KUBECTL:-kubectl --context rancher-desktop -n observability}
COLLECTOR=${COLLECTOR:-http://otel-collector.observability:4318}
LOKI_URL=${LOKI_URL:-http://loki.localhost}
TEMPO_URL=${TEMPO_URL:-http://tempo.localhost}
PROMETHEUS_URL=${PROMETHEUS_URL:-http://prometheus.localhost}

run=$(openssl rand -hex 4)
trace_id=$(openssl rand -hex 16)
span_id=$(openssl rand -hex 8)
now_ns="$(date +%s)000000000"
end_ns="$(( $(date +%s) + 1 ))000000000"
resource='{"attributes":[{"key":"service.name","value":{"stringValue":"smoke-test"}}]}'

log_body=$(cat <<EOF
{"resourceLogs":[{"resource":$resource,"scopeLogs":[{"logRecords":[{
  "timeUnixNano":"$now_ns","severityText":"INFO","body":{"stringValue":"smoke test $run"},
  "traceId":"$trace_id","spanId":"$span_id"}]}]}]}
EOF
)
trace_body=$(cat <<EOF
{"resourceSpans":[{"resource":$resource,"scopeSpans":[{"spans":[{
  "traceId":"$trace_id","spanId":"$span_id","name":"smoke-test","kind":2,
  "startTimeUnixNano":"$now_ns","endTimeUnixNano":"$end_ns",
  "attributes":[{"key":"run","value":{"stringValue":"$run"}}]}]}]}]}
EOF
)
metric_body=$(cat <<EOF
{"resourceMetrics":[{"resource":$resource,"scopeMetrics":[{"metrics":[{"name":"smoke_test_value","gauge":{"dataPoints":[{
  "asInt":"1","timeUnixNano":"$now_ns","attributes":[{"key":"run","value":{"stringValue":"$run"}}]}]}}]}]}]}
EOF
)

echo "run $run, trace $trace_id: sending to $COLLECTOR"
# shellcheck disable=SC2086  # KUBECTL is a command with arguments
$KUBECTL run "otel-smoke-$run" --rm -i --quiet --restart=Never --image=curlimages/curl:8.16.0 \
  --env="LOG=$log_body" --env="TRACE=$trace_body" --env="METRIC=$metric_body" --env="C=$COLLECTOR" \
  --command -- sh -ec '
    post() { curl -fsS -o /dev/null -H "content-type: application/json" --data "$2" "$C/v1/$1"; echo "  sent $1"; }
    post logs "$LOG"
    post traces "$TRACE"
    post metrics "$METRIC"'

# Each backend gets up to 30s: the collector batches, and Prometheus/Loki index on arrival.
check() {  # name, command that succeeds once the data is there
  local name=$1; shift
  for _ in $(seq 30); do
    if "$@" >/dev/null 2>&1; then echo "ok    $name"; return 0; fi
    sleep 1
  done
  echo "FAIL  $name"; return 1
}

loki_has_log() {
  curl -fsG "$LOKI_URL/loki/api/v1/query_range" \
    --data-urlencode "query={service_name=\"smoke-test\"} |= \"smoke test $run\"" --data-urlencode "since=10m" |
    grep -q "smoke test $run"
}
tempo_has_trace() {
  curl -fs "$TEMPO_URL/api/v2/traces/$trace_id" | grep -q "\"$run\""
}
prometheus_has_metric() {
  curl -fsG "$PROMETHEUS_URL/api/v1/query" --data-urlencode "query=smoke_test_value{run=\"$run\"}" |
    grep -q "\"run\":\"$run\""
}

failed=0
check "Loki has the log"          loki_has_log || failed=1
check "Tempo has the trace"       tempo_has_trace || failed=1
check "Prometheus has the metric" prometheus_has_metric || failed=1
exit $failed
