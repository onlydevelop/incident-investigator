# Shared infrastructure: redis, kafka, postgres (and opensearch, for ../rag) on the `portfolio` docker network.
DEPS        := redis kafka postgres
REDIS_CLI   := docker compose exec -T redis redis-cli
KAFKA_BIN   := docker compose exec -T kafka /opt/kafka/bin
BOOTSTRAP   := --bootstrap-server localhost:9092
PSQL        := docker compose exec postgres psql -U $(or $(POSTGRES_USER),portfolio) -d $(or $(POSTGRES_DB),portfolio)

# Topics created by `make up` / `make topics-create`: <name>:<partitions>
TOPICS      := market-data.ticker:3

# Rancher Desktop exposes Docker on ~/.rd/docker.sock when it can't use /var/run/docker.sock.
ifeq ($(DOCKER_HOST),)
ifeq ($(wildcard /var/run/docker.sock),)
ifneq ($(wildcard $(HOME)/.rd/docker.sock),)
export DOCKER_HOST := unix://$(HOME)/.rd/docker.sock
endif
endif
endif

.DEFAULT_GOAL := help
.PHONY: help up down restart status logs search-up search-stop \
        redis-cli psql \
        topics-create topics-list topic-describe topic-tail

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

## --- Everything ---

up: ## Start redis, kafka and postgres, wait until healthy, create topics
	docker compose up -d --wait $(DEPS)
	@$(MAKE) --no-print-directory topics-create

down: ## Stop and remove the infra containers (data volumes are kept)
	docker compose down

restart: ## Restart the infra containers
	docker compose restart $(DEPS)

status: ## Show the infra containers
	docker compose ps

logs: ## Follow logs from all infra containers (SERVICE=<name> for one)
	docker compose logs -f $(SERVICE)

## --- OpenSearch (only the RAG layer uses it) ---

search-up: ## Start postgres and opensearch for ../rag, wait until healthy
	docker compose up -d --wait postgres opensearch

search-stop: ## Stop opensearch (about 1 GB of memory) without touching the rest
	docker compose stop opensearch

## --- Clients ---

redis-cli: ## Open redis-cli
	docker compose exec redis redis-cli

psql: ## Open psql on the portfolio database
	$(PSQL)

## --- Kafka topics ---

topics-create: ## Create the topics in TOPICS (existing ones are left alone)
	@for t in $(TOPICS); do \
		$(KAFKA_BIN)/kafka-topics.sh $(BOOTSTRAP) --create --if-not-exists \
			--topic $${t%%:*} --partitions $${t##*:} --replication-factor 1 >/dev/null && echo "topic $${t%%:*}"; \
	done

topics-list: ## List topics
	@$(KAFKA_BIN)/kafka-topics.sh $(BOOTSTRAP) --list

topic-describe: ## Describe TOPIC=<topic>
	@test -n "$(TOPIC)" || { echo "usage: make topic-describe TOPIC=market-data.ticker"; exit 1; }
	@$(KAFKA_BIN)/kafka-topics.sh $(BOOTSTRAP) --describe --topic $(TOPIC)

topic-tail: ## Print messages on TOPIC=<topic> with their keys, from now on (Ctrl+C to stop)
	@test -n "$(TOPIC)" || { echo "usage: make topic-tail TOPIC=market-data.ticker"; exit 1; }
	docker compose exec kafka /opt/kafka/bin/kafka-console-consumer.sh $(BOOTSTRAP) \
		--topic $(TOPIC) --property print.key=true --property key.separator='  '
