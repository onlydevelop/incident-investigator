# docker-compose services, split so each group can be managed on its own.
DEPS         := redis
TICKER       := delta-ticker
REDIS_CLI    := docker compose exec -T $(DEPS) redis-cli
CACHE_PREFIX := ticker:latest:
SYMBOLS_KEY  := ticker:symbols

# Rancher Desktop exposes Docker on ~/.rd/docker.sock when it can't use /var/run/docker.sock.
ifeq ($(DOCKER_HOST),)
ifeq ($(wildcard /var/run/docker.sock),)
ifneq ($(wildcard $(HOME)/.rd/docker.sock),)
export DOCKER_HOST := unix://$(HOME)/.rd/docker.sock
endif
endif
endif

.DEFAULT_GOAL := help
.PHONY: help build up down restart status logs test \
        deps-start deps-stop deps-restart deps-status deps-logs \
        ticker-build ticker-start ticker-stop ticker-restart ticker-status ticker-logs ticker-run \
        cache-show cache-get \
        symbols-show symbols-add symbols-remove symbols-set symbols-clear

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

## --- Everything ---

build: ticker-build ## Build all images

up: ## Build the ticker, start redis, then stream ticks into the cache
	docker compose up -d --build --wait

down: ## Stop and remove all containers (data volumes are kept)
	docker compose down

restart: down up ## Restart everything

status: ## Show all containers
	docker compose ps

logs: ## Follow logs from all containers
	docker compose logs -f

test: ## Run unit tests in docker
	docker build --target test .

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

## --- Delta ticker ---

ticker-build: ## Build the delta_ticker image
	docker compose build $(TICKER)

ticker-start: ## Build if needed and start delta_ticker (starts redis if needed)
	docker compose up -d --build --wait $(TICKER)

ticker-stop: ## Stop the delta_ticker container
	docker compose stop $(TICKER)

ticker-restart: ## Rebuild and restart delta_ticker (picks up code changes)
	docker compose up -d --build --force-recreate --wait $(TICKER)

ticker-status: ## Show the delta_ticker container
	docker compose ps $(TICKER)

ticker-logs: ## Follow delta_ticker output
	docker compose logs -f $(TICKER)

ticker-run: ## Run delta_ticker in the foreground in docker (Ctrl+C to stop)
	docker compose run --rm --build $(TICKER)

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
