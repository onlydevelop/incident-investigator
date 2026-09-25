# docker-compose services, split so each group can be managed on its own.
API          := orders-api
UPDATER      := position-updater
API_URL      := http://localhost:$(or $(ORDERS_API_PORT),8001)
# Postgres and kafka are shared with other services and live in ../infra.
INFRA        := $(MAKE) --no-print-directory -C ../infra
MARKET_DATA  := $(MAKE) --no-print-directory -C ../market-data-service
# PGTZ shows timestamps in IST, like the API and market-data-service.
PSQL         := docker compose -f ../infra/docker-compose.yml exec -T -e PGTZ=Asia/Kolkata postgres psql -U portfolio -d portfolio

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
        deps-start deps-status market-data-start \
        api-start api-stop api-restart api-status api-logs api-docs \
        updater-start updater-stop updater-restart updater-status updater-logs \
        positions-show db-shell

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

## --- Everything ---

build: ## Build the image (API and updater share it)
	docker compose build $(API)

up: deps-start market-data-start ## Start the infra and market-data-service if needed, then the API and the updater
	docker compose up -d --build --wait $(API) $(UPDATER)

down: ## Stop and remove the API and updater (infra and market-data-service keep running)
	docker compose down

restart: down up ## Restart the API and the updater

status: ## Show the API and updater containers
	docker compose ps

logs: ## Follow logs from the API and the updater
	docker compose logs -f

test: deps-start ## Run the tests in docker, against the shared Postgres (throwaway schema)
	docker compose run --rm --build test

## --- Dependencies ---

deps-start: ## Start the shared infra (postgres, kafka, redis) and wait until healthy
	@$(INFRA) up

deps-status: ## Show the shared infra containers
	@$(INFRA) status

market-data-start: ## Start market-data-service (ticker + symbols API) if it isn't running
	@$(MARKET_DATA) up

## --- Positions API ---

api-start: deps-start market-data-start ## Build if needed and start the API
	docker compose up -d --build --wait $(API)
	@echo "Positions API: $(API_URL)/positions   docs: $(API_URL)/docs"

api-stop: ## Stop the API
	docker compose stop $(API)

api-restart: deps-start ## Rebuild and restart the API (picks up code changes)
	docker compose up -d --build --force-recreate --wait $(API)

api-status: ## Show the API container
	docker compose ps $(API)

api-logs: ## Follow the API's logs
	docker compose logs -f $(API)

api-docs: ## Open the interactive API docs in a browser
	open $(API_URL)/docs

## --- Position updater (Kafka consumer, same image as the API) ---

updater-start: deps-start ## Build if needed and start the updater
	docker compose up -d --build --wait $(UPDATER)

updater-stop: ## Stop the updater
	docker compose stop $(UPDATER)

updater-restart: deps-start ## Rebuild and restart the updater (picks up code changes)
	docker compose up -d --build --force-recreate --wait $(UPDATER)

updater-status: ## Show the updater container
	docker compose ps $(UPDATER)

updater-logs: ## Follow the updater's logs
	docker compose logs -f $(UPDATER)

## --- Postgres ---

positions-show: ## Print the positions table
	@$(PSQL) -c "SELECT id, time, symbol, side, qty, status, entry_price, current_price, current_price_time FROM positions ORDER BY id"

db-shell: ## Open psql on the shared database
	docker compose -f ../infra/docker-compose.yml exec postgres psql -U portfolio -d portfolio
