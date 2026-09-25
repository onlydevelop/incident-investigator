PYTHON ?= python3

RUN_DIR    := .run
TICKER_PID := $(RUN_DIR)/delta_ticker.pid
TICKER_LOG := $(RUN_DIR)/delta_ticker.log

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
        ticker-start ticker-stop ticker-restart ticker-status ticker-logs ticker-run

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

## --- Everything ---

up: deps-start ticker-start ## Start dependencies, then the ticker

down: ticker-stop deps-stop ## Stop the ticker, then dependencies

restart: down up ## Restart everything

## --- Dependencies (docker compose: redis) ---

deps-start: ## Start dependencies and wait until healthy
	docker compose up -d --wait

deps-stop: ## Stop dependencies (data volumes are kept)
	docker compose down

deps-restart: ## Restart dependencies
	docker compose restart

deps-status: ## Show dependency containers
	docker compose ps

deps-logs: ## Follow dependency logs
	docker compose logs -f

## --- Delta ticker ---

ticker-start: ## Start delta_ticker in the background
	@mkdir -p $(RUN_DIR)
	@if [ -f $(TICKER_PID) ] && kill -0 $$(cat $(TICKER_PID)) 2>/dev/null; then \
		echo "delta_ticker already running (pid $$(cat $(TICKER_PID)))"; \
	else \
		nohup $(PYTHON) -u delta_ticker.py >> $(TICKER_LOG) 2>&1 & echo $$! > $(TICKER_PID); \
		echo "delta_ticker started (pid $$(cat $(TICKER_PID))), logging to $(TICKER_LOG)"; \
	fi

ticker-stop: ## Stop the background delta_ticker
	@if [ -f $(TICKER_PID) ] && kill -0 $$(cat $(TICKER_PID)) 2>/dev/null; then \
		kill $$(cat $(TICKER_PID)); \
		while kill -0 $$(cat $(TICKER_PID)) 2>/dev/null; do sleep 0.2; done; \
		echo "delta_ticker stopped"; \
	else \
		echo "delta_ticker not running"; \
	fi
	@rm -f $(TICKER_PID)

ticker-restart: ticker-stop ticker-start ## Restart delta_ticker

ticker-status: ## Show whether delta_ticker is running
	@if [ -f $(TICKER_PID) ] && kill -0 $$(cat $(TICKER_PID)) 2>/dev/null; then \
		echo "delta_ticker running (pid $$(cat $(TICKER_PID)))"; \
	else \
		echo "delta_ticker not running"; \
	fi

ticker-logs: ## Follow delta_ticker output
	@tail -f $(TICKER_LOG)

ticker-run: ## Run delta_ticker in the foreground (Ctrl+C to stop)
	$(PYTHON) -u delta_ticker.py
