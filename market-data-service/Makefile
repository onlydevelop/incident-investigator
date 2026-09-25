PYTHON ?= python3

# docker-compose services, split so each group can be managed on its own.
DEPS   := redis
TICKER := delta-ticker

# Rancher Desktop exposes Docker on ~/.rd/docker.sock when it can't use /var/run/docker.sock.
ifeq ($(DOCKER_HOST),)
ifeq ($(wildcard /var/run/docker.sock),)
ifneq ($(wildcard $(HOME)/.rd/docker.sock),)
export DOCKER_HOST := unix://$(HOME)/.rd/docker.sock
endif
endif
endif

.DEFAULT_GOAL := help
.PHONY: help up down restart \
        deps-start deps-stop deps-restart deps-status deps-logs \
        ticker-build ticker-start ticker-stop ticker-restart ticker-status ticker-logs ticker-run-local test

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

## --- Everything ---

up: deps-start ticker-start ## Start dependencies, then the ticker

down: ## Stop and remove all containers (data volumes are kept)
	docker compose down

restart: down up ## Restart everything

## --- Dependencies (redis) ---

deps-start: ## Start dependencies and wait until healthy
	docker compose up -d --wait $(DEPS)

deps-stop: ## Stop dependencies (data volumes are kept)
	docker compose stop $(DEPS)

deps-restart: ## Restart dependencies
	docker compose restart $(DEPS)

deps-status: ## Show dependency containers
	docker compose ps $(DEPS)

deps-logs: ## Follow dependency logs
	docker compose logs -f $(DEPS)

## --- Delta ticker (docker) ---

ticker-build: ## Build the delta_ticker image
	docker compose build $(TICKER)

ticker-start: ## Build if needed and start delta_ticker in a container
	docker compose up -d --build $(TICKER)

ticker-stop: ## Stop the delta_ticker container
	docker compose stop $(TICKER)

ticker-restart: ## Rebuild and restart delta_ticker (picks up code changes)
	docker compose up -d --build --force-recreate $(TICKER)

ticker-status: ## Show the delta_ticker container
	docker compose ps $(TICKER)

ticker-logs: ## Follow delta_ticker output
	docker compose logs -f $(TICKER)

ticker-run-local: ## Run delta_ticker on the host, in the foreground (Ctrl+C to stop)
	PYTHONPATH=src $(PYTHON) -u -m delta_ticker

## --- Development ---

test: ## Run unit tests
	$(PYTHON) -m pytest
