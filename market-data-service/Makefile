# docker-compose services, split so each group can be managed on its own.
TICKER       := delta-ticker
API          := symbols-api
API_URL      := http://localhost:$(or $(API_PORT),8000)
# Redis, kafka and postgres are shared with other services and live in ../infra.
INFRA        := $(MAKE) --no-print-directory -C ../infra
REDIS_CLI    := docker compose -f ../infra/docker-compose.yml exec -T redis redis-cli
# Must match CACHE_KEY_PREFIX, SYMBOLS_KEY and TICKER_TOPIC in src/delta_ticker/config.py.
CACHE_PREFIX := ticker:latest:
SYMBOLS_KEY  := ticker:symbols
TICKER_TOPIC := market-data.ticker

# Rancher Desktop exposes Docker on ~/.rd/docker.sock when it can't use /var/run/docker.sock.
ifeq ($(DOCKER_HOST),)
ifeq ($(wildcard /var/run/docker.sock),)
ifneq ($(wildcard $(HOME)/.rd/docker.sock),)
export DOCKER_HOST := unix://$(HOME)/.rd/docker.sock
endif
endif
endif

.DEFAULT_GOAL := help
.PHONY: help build up down restart status logs test coverage \
        deps-start deps-stop deps-restart deps-status deps-logs \
        ticker-build ticker-start ticker-stop ticker-restart ticker-status ticker-logs ticker-run \
        api-start api-stop api-restart api-status api-logs api-docs \
        kafka-tail kafka-describe \
        cache-show cache-get \
        symbols-show symbols-add symbols-remove symbols-set symbols-clear

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

## --- Everything ---

build: ticker-build ## Build all images

up: deps-start ## Start the shared infra if needed, build the image, start the ticker and the symbols API
	docker compose up -d --build --wait

down: ## Stop and remove the ticker and API containers (the shared infra keeps running)
	docker compose down

restart: down up ## Restart everything

status: ## Show all containers
	docker compose ps

logs: ## Follow logs from all containers
	docker compose logs -f

test: ## Run unit tests in docker
	docker build --target test .

coverage: ## Run the tests with a coverage report (terminal, plus htmlcov/index.html); fails under the floor in pyproject.toml
	docker build --target test -t market-data/delta-ticker-test .
	docker run --rm -v "$(CURDIR)/htmlcov:/app/htmlcov" market-data/delta-ticker-test \
		python -m pytest -q -p no:cacheprovider --cov --cov-report=term-missing --cov-report=html:htmlcov
	@echo "HTML report: $(CURDIR)/htmlcov/index.html"

## --- Dependencies (shared infra in ../infra: redis, kafka, postgres) ---

deps-start: ## Start the shared infra and wait until healthy (no-op if already up)
	@$(INFRA) up

deps-stop: ## Stop the shared infra (also affects other services; data volumes are kept)
	@$(INFRA) down

deps-restart: ## Restart the shared infra
	@$(INFRA) restart

deps-status: ## Show the shared infra containers
	@$(INFRA) status

deps-logs: ## Follow the shared infra logs
	@$(INFRA) logs

## --- Delta ticker ---

ticker-build: ## Build the delta_ticker image
	docker compose build $(TICKER)

ticker-start: deps-start ## Build if needed and start delta_ticker (starts the shared infra if needed)
	docker compose up -d --build --wait $(TICKER)

ticker-stop: ## Stop the delta_ticker container
	docker compose stop $(TICKER)

ticker-restart: deps-start ## Rebuild and restart delta_ticker (picks up code changes)
	docker compose up -d --build --force-recreate --wait $(TICKER)

ticker-status: ## Show the delta_ticker container
	docker compose ps $(TICKER)

ticker-logs: ## Follow delta_ticker output
	docker compose logs -f $(TICKER)

ticker-run: deps-start ## Run delta_ticker in the foreground in docker (Ctrl+C to stop)
	docker compose run --rm --build $(TICKER)

## --- Symbols API (FastAPI, same image as the ticker) ---

api-start: deps-start ## Build if needed and start the symbols API (starts the shared infra if needed)
	docker compose up -d --build --wait $(API)
	@echo "Symbols API: $(API_URL)/symbols   docs: $(API_URL)/docs"

api-stop: ## Stop the symbols API
	docker compose stop $(API)

api-restart: deps-start ## Rebuild and restart the symbols API (picks up code changes)
	docker compose up -d --build --force-recreate --wait $(API)

api-status: ## Show the symbols API container
	docker compose ps $(API)

api-logs: ## Follow symbols API logs
	docker compose logs -f $(API)

api-docs: ## Open the interactive API docs in a browser
	open $(API_URL)/docs

## --- Kafka (topic market-data.ticker, in the shared infra) ---

kafka-tail: ## Print ticker updates as they arrive on Kafka, key and value (Ctrl+C to stop)
	@$(INFRA) topic-tail TOPIC=$(TICKER_TOPIC)

kafka-describe: ## Show the ticker topic's partitions and config
	@$(INFRA) topic-describe TOPIC=$(TICKER_TOPIC)

## --- Cache (redis) ---

cache-show: ## List cached symbols with their remaining TTL
	@keys=$$($(REDIS_CLI) --scan --pattern '$(CACHE_PREFIX)*'); \
	if [ -z "$$keys" ]; then echo "cache empty"; fi; \
	for k in $$keys; do echo "$$k  ttl=$$($(REDIS_CLI) ttl $$k)s"; done

cache-get: ## Print the cached payload for SYMBOL=<symbol>
	@test -n "$(SYMBOL)" || { echo "usage: make cache-get SYMBOL=C-BTC-79500-250926"; exit 1; }
	@$(REDIS_CLI) get '$(CACHE_PREFIX)$(SYMBOL)'

## --- Subscribed symbols (redis set, no expiry; the ticker re-reads it every 30s) ---

symbols-show: ## List the symbols the ticker subscribes to
	@$(REDIS_CLI) smembers $(SYMBOLS_KEY) | sort

symbols-add: ## Add symbols: make symbols-add SYMBOLS="C-BTC-80000-091026 P-BTC-80000-091026"
	@test -n "$(SYMBOLS)" || { echo 'usage: make symbols-add SYMBOLS="<symbol> [<symbol> ...]"'; exit 1; }
	@$(REDIS_CLI) sadd $(SYMBOLS_KEY) $(SYMBOLS) >/dev/null
	@$(MAKE) --no-print-directory symbols-show

symbols-remove: ## Remove symbols: make symbols-remove SYMBOLS="C-BTC-79500-250926"
	@test -n "$(SYMBOLS)" || { echo 'usage: make symbols-remove SYMBOLS="<symbol> [<symbol> ...]"'; exit 1; }
	@$(REDIS_CLI) srem $(SYMBOLS_KEY) $(SYMBOLS) >/dev/null
	@$(MAKE) --no-print-directory symbols-show

symbols-set: ## Replace the whole list: make symbols-set SYMBOLS="..."
	@test -n "$(SYMBOLS)" || { echo 'usage: make symbols-set SYMBOLS="<symbol> [<symbol> ...]"'; exit 1; }
	@printf 'MULTI\nDEL $(SYMBOLS_KEY)\nSADD $(SYMBOLS_KEY) $(SYMBOLS)\nEXEC\n' | $(REDIS_CLI) >/dev/null
	@$(MAKE) --no-print-directory symbols-show

symbols-clear: ## Remove all symbols (the ticker unsubscribes on its next refresh)
	@$(REDIS_CLI) del $(SYMBOLS_KEY) >/dev/null
	@echo "symbols cleared"
