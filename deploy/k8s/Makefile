# Deploy the whole stack to the local k3s (Rancher Desktop). Run from this directory, or
# from anywhere with `make -C deploy/k8s <target>`.
NS          := incident-investigator
KUBECTL     := kubectl --context $(or $(KUBE_CONTEXT),rancher-desktop)
K           := $(KUBECTL) -n $(NS)
ROOT        := $(abspath ../..)
TAG         := local
MD_IMAGE    := market-data/delta-ticker:$(TAG)
OS_IMAGE    := order-service/order-service:$(TAG)
APPS        := deployment/delta-ticker deployment/symbols-api deployment/orders-api deployment/position-updater
# Set when ../observability is installed: the PodMonitor kind exists, so monitoring/ can be applied.
HAS_MONITORING = $(KUBECTL) get crd podmonitors.monitoring.coreos.com >/dev/null 2>&1
ORDERS_URL  := http://orders.localhost
MD_URL      := http://market-data.localhost
# Per step of `make wait`; a pod stuck in a config or image error fails well before this.
TIMEOUT     := 120s

# Rancher Desktop exposes Docker on ~/.rd/docker.sock when it can't use /var/run/docker.sock.
ifeq ($(DOCKER_HOST),)
ifeq ($(wildcard /var/run/docker.sock),)
ifneq ($(wildcard $(HOME)/.rd/docker.sock),)
export DOCKER_HOST := unix://$(HOME)/.rd/docker.sock
endif
endif
endif

.DEFAULT_GOAL := help
.PHONY: help start stop images-if-missing images deploy apply restart wait diagnose status logs smoke render delete delete-all

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

start: images-if-missing apply wait ## Start the stack (after `stop`, `delete`, or on a fresh cluster); reuses built images
	@echo
	@echo "orders API:      $(ORDERS_URL)/docs"
	@echo "market-data API: $(MD_URL)/docs"

stop: ## Scale every workload to 0; objects, config and data volumes stay, so `start` is quick
	@if [ -z "$$($(K) get deployment,statefulset -o name 2>/dev/null)" ]; then \
		echo "nothing to stop in namespace $(NS)"; \
	else \
		$(K) scale deployment,statefulset --all --replicas=0 && \
		$(K) wait --for=delete pod --all --field-selector=status.phase!=Succeeded --timeout=$(TIMEOUT) \
			|| { $(MAKE) --no-print-directory diagnose; exit 1; }; \
		echo "stopped; PVCs kept. 'make start' brings it back"; \
	fi

images-if-missing: ## Build an image only if it doesn't exist yet (use `images` to force a rebuild)
	@docker image inspect $(MD_IMAGE) >/dev/null 2>&1 || docker build --target runtime -t $(MD_IMAGE) $(ROOT)/market-data-service
	@docker image inspect $(OS_IMAGE) >/dev/null 2>&1 || docker build --target runtime -t $(OS_IMAGE) $(ROOT)/order-service

images: ## Build both service images into the docker daemon k3s uses
	docker build --target runtime -t $(MD_IMAGE) $(ROOT)/market-data-service
	docker build --target runtime -t $(OS_IMAGE) $(ROOT)/order-service

deploy: images apply restart wait ## Build images, apply the manifests, roll the apps onto the new images, wait
	@echo
	@echo "orders API:      $(ORDERS_URL)/docs"
	@echo "market-data API: $(MD_URL)/docs"

apply: ## kubectl apply -k (no image build); adds the exporters' PodMonitor if ../observability is installed
	$(KUBECTL) apply -k .
	@if $(HAS_MONITORING); then $(KUBECTL) apply -k monitoring; \
	else echo "no PodMonitor CRD (make -C ../observability up); infra exporters won't be scraped"; fi

restart: ## Restart the apps so they pick up freshly built :$(TAG) images
	$(K) rollout restart $(APPS)

wait: ## Wait until infra and apps are ready; on failure print what's wrong instead of hanging
	@$(K) rollout status statefulset/redis statefulset/postgres statefulset/kafka deployment/kafka-exporter --timeout=$(TIMEOUT) \
		|| { $(MAKE) --no-print-directory diagnose; exit 1; }
	@# The Job deletes itself 5 minutes after completing, so "not found" means it already succeeded.
	@if $(K) get job kafka-topics >/dev/null 2>&1; then \
		$(K) wait --for=condition=complete job/kafka-topics --timeout=$(TIMEOUT) \
			|| { $(MAKE) --no-print-directory diagnose; exit 1; }; \
	else echo "job/kafka-topics already completed and cleaned up"; fi
	@$(K) rollout status $(APPS) --timeout=$(TIMEOUT) \
		|| { $(MAKE) --no-print-directory diagnose; exit 1; }

diagnose: ## Show pods that aren't ready and recent warning events
	@echo "--- pods"
	@$(K) get pods -o wide
	@echo "--- warning events (latest 15)"
	@$(K) get events --field-selector type=Warning --sort-by=.lastTimestamp | tail -15

status: ## Pods, services, ingress and volumes
	$(K) get pods,svc,ingress,pvc -o wide

logs: ## Follow an app's logs: make logs APP=orders-api
	@test -n "$(APP)" || { echo "usage: make logs APP=delta-ticker|symbols-api|orders-api|position-updater|kafka|kafka-exporter|postgres|redis"; exit 1; }
	$(K) logs -f --tail=50 $$($(K) get pods -l app=$(APP) -o name | head -1)

smoke: ## Run scripts/smoke-test.sh against the cluster (pauses delta-ticker meanwhile)
	$(K) scale deployment/delta-ticker --replicas=0
	ORDERS_URL=$(ORDERS_URL) MARKET_DATA_URL=$(MD_URL) KAFKA_EXEC="$(K) exec -i kafka-0 --" \
		$(ROOT)/scripts/smoke-test.sh; status=$$?; \
		$(K) scale deployment/delta-ticker --replicas=1; exit $$status

render: ## Print the rendered manifests
	$(KUBECTL) kustomize .

delete: ## Remove everything except the namespace and its data volumes (PVCs)
	@# Not `delete -k .`: that includes the Namespace, and deleting it would delete the PVCs too.
	$(K) delete all,ingress,configmap,secret -l app.kubernetes.io/part-of=portfolio --ignore-not-found
	@if $(HAS_MONITORING); then $(K) delete podmonitor -l app.kubernetes.io/part-of=portfolio --ignore-not-found; fi
	@echo "PVCs kept (make deploy reuses them); 'make delete-all' removes the namespace and its data"

delete-all: ## Remove the namespace, including the Postgres, Kafka and Redis data
	$(KUBECTL) delete namespace $(NS) --ignore-not-found
