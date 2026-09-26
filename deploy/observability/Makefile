# Prometheus, Grafana, Loki, Tempo and the OTel Collector on the local k3s (Rancher Desktop), in the
# `observability` namespace. Run from this directory, or with `make -C deploy/observability <target>`.
NS          := observability
KUBE_CTX    := $(or $(KUBE_CONTEXT),rancher-desktop)
KUBECTL     := kubectl --context $(KUBE_CTX)
K           := $(KUBECTL) -n $(NS)
HELM        := helm --kube-context $(KUBE_CTX) -n $(NS)
TIMEOUT     := 180s

# release  chart  version. Pinned; bump deliberately and read the chart's upgrade notes.
# kube-prometheus-stack stays on the version the release was first installed with.
KPS_CHART   := prometheus-community/kube-prometheus-stack --version 88.3.0
LOKI_CHART  := grafana/loki --version 7.3.0
TEMPO_CHART := grafana-community/tempo --version 2.4.0
OTEL_CHART  := open-telemetry/opentelemetry-collector --version 0.173.1

.DEFAULT_GOAL := help
.PHONY: help repos up prometheus loki tempo collector extras wait status render smoke grafana-password down

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

repos: ## Add and update the Helm chart repositories
	@helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
	@helm repo add grafana https://grafana.github.io/helm-charts >/dev/null
	@helm repo add grafana-community https://grafana-community.github.io/helm-charts >/dev/null
	@helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts >/dev/null
	helm repo update prometheus-community grafana grafana-community open-telemetry

# Backends before the collector, so it has somewhere to send from its first second.
up: repos prometheus loki tempo collector extras wait ## Install or upgrade the whole stack and wait until it's ready
	@echo
	@echo "Grafana:    http://grafana.localhost  (admin / \`make grafana-password\`)"
	@echo "Prometheus: http://prometheus.localhost"
	@echo "Loki:       http://loki.localhost     Tempo: http://tempo.localhost"
	@echo "OTLP:       http://otel-collector.$(NS):4318 (HTTP), :4317 (gRPC), from inside the cluster"

prometheus: ## Install or upgrade kube-prometheus-stack (Prometheus, Grafana, exporters)
	$(HELM) upgrade --install --create-namespace kube-prometheus-stack $(KPS_CHART) -f values/kube-prometheus-stack.yaml

loki: ## Install or upgrade Loki
	$(HELM) upgrade --install loki $(LOKI_CHART) -f values/loki.yaml

tempo: ## Install or upgrade Tempo
	$(HELM) upgrade --install tempo $(TEMPO_CHART) -f values/tempo.yaml

collector: ## Install or upgrade the OTel Collector DaemonSet
	$(HELM) upgrade --install otel-collector $(OTEL_CHART) -f values/otel-collector.yaml

extras: ## Apply the Loki and Tempo ingresses and the Loki ServiceMonitor
	$(KUBECTL) apply -f extras.yaml

wait: ## Wait until every workload in the namespace is rolled out
	@for w in $$($(K) get deployment,statefulset,daemonset -o name); do \
		$(K) rollout status $$w --timeout=$(TIMEOUT) || { $(K) get pods; exit 1; }; \
	done

status: ## Releases, pods, ingresses and volumes
	@$(HELM) list
	@echo
	@$(K) get pods,ingress,pvc

render: ## Render every chart with these values (no cluster changes); catches values mistakes
	@helm template kube-prometheus-stack $(KPS_CHART) -n $(NS) -f values/kube-prometheus-stack.yaml >/dev/null
	@helm template loki $(LOKI_CHART) -n $(NS) -f values/loki.yaml >/dev/null
	@helm template tempo $(TEMPO_CHART) -n $(NS) -f values/tempo.yaml >/dev/null
	@helm template otel-collector $(OTEL_CHART) -n $(NS) -f values/otel-collector.yaml >/dev/null
	@echo "all charts render"

smoke: ## Send a log, a trace and a metric through the collector and read each back
	KUBECTL="$(K)" ./smoke-test.sh

grafana-password: ## Print Grafana's admin password
	@$(K) get secret kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo

down: ## Remove Loki, Tempo and the collector (their volumes are kept). Prometheus and Grafana stay
	$(HELM) uninstall otel-collector tempo loki --ignore-not-found
	$(KUBECTL) delete -f extras.yaml --ignore-not-found
