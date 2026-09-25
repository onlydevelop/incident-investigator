# Chunking and retrieval over ../incident-corpus. Runs locally in a venv; the embedding model
# (BAAI/bge-small-en-v1.5, ~130 MB) is downloaded by fastembed on first use. pgvector and OpenSearch
# come from ../infra (`make infra`).
VENV       := .venv
PY         := $(VENV)/bin/python
STRATEGY   ?= heading-merged+ctx
BACKEND    ?= opensearch-hybrid
# What `make eval-backends` compares: the three strongest chunkings from `make eval`, every backend.
EVAL_STRATEGIES := heading-merged+ctx heading+ctx fixed-256+ctx
EVAL_BACKENDS   := memory pgvector opensearch-bm25 opensearch-knn opensearch-hybrid opensearch-hybrid-rrf

.DEFAULT_GOAL := help
.PHONY: help install infra test eval eval-backends misses chunks ingest search

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

$(PY):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -e '.[dev]'

install: $(PY) ## Create the venv and install the package with test dependencies

infra: ## Start pgvector (Postgres) and OpenSearch from ../infra, wait until healthy
	@$(MAKE) --no-print-directory -C ../infra search-up

test: $(PY) ## Run the tests; store tests skip unless `make infra` is up
	$(PY) -m pytest -q -rs

## --- Evaluation ---

eval: $(PY) ## Compare every chunking strategy (in-memory search, no infra needed)
	$(PY) -m incident_rag.evaluate

eval-backends: infra $(PY) ## Compare pgvector, BM25, k-NN and hybrid search on the best chunkings
	$(PY) -m incident_rag.evaluate --strategies $(EVAL_STRATEGIES) --backends $(EVAL_BACKENDS)

misses: $(PY) ## Questions STRATEGY misses at k=3 on BACKEND, with what it retrieved instead
	$(PY) -m incident_rag.evaluate --misses $(STRATEGY) --backends $(BACKEND)

chunks: $(PY) ## Print STRATEGY's chunks
	$(PY) -m incident_rag.evaluate --dump $(STRATEGY)

## --- Using it ---

ingest: infra $(PY) ## Chunk, embed and load STRATEGY into pgvector and OpenSearch
	$(PY) -m incident_rag.ingest --strategy $(STRATEGY)

search: $(PY) ## Search: make search Q="orders rejected as stale" [BACKEND=pgvector] [ARGS="--type runbook -k 3 --full"]
	@test -n "$(Q)" || { echo 'usage: make search Q="your question"'; exit 1; }
	@$(PY) -m incident_rag.search "$(Q)" --backend $(BACKEND) --strategy $(STRATEGY) $(ARGS)
