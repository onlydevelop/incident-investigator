# Shortcuts for the local k3s stack; the real targets live in deploy/k8s/Makefile and
# deploy/observability/Makefile.
K8S := $(MAKE) --no-print-directory -C deploy/k8s
OBS := $(MAKE) --no-print-directory -C deploy/observability

.DEFAULT_GOAL := help
.PHONY: help k8s-start k8s-stop k8s-status k8s-deploy obs-up obs-status obs-smoke

help: ## Show available targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

k8s-start: ## Start the k3s stack (builds images only if missing), wait until ready
	@$(K8S) start

k8s-stop: ## Scale the k3s stack to 0; config and data are kept
	@$(K8S) stop

k8s-status: ## Pods, services, ingress and volumes of the k3s stack
	@$(K8S) status

k8s-deploy: ## Rebuild images and redeploy (after code changes)
	@$(K8S) deploy

obs-up: ## Install or upgrade Prometheus, Grafana, Loki, Tempo and the OTel Collector, wait until ready
	@$(OBS) up

obs-status: ## Releases, pods, ingresses and volumes of the observability stack
	@$(OBS) status

obs-smoke: ## Send a log, trace and metric through the collector and read each back
	@$(OBS) smoke
