# Incident-investigation agent. Runs locally in a venv, with ../rag installed editable next to it.
# It needs the observability stack (make obs-up from the repo root), the observability MCP server
# (make -C ../observability-mcp install), OpenSearch with the corpus ingested (make -C ../rag ingest),
# and ANTHROPIC_API_KEY.
VENV := .venv
PY   := $(VENV)/bin/python

.DEFAULT_GOAL := help
.PHONY: help install test run

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

$(PY): pyproject.toml ../rag/pyproject.toml
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -e ../rag -e '.[dev]'
	@touch $(PY)

install: $(PY) ## Create the venv and install the agent, incident-rag and test dependencies

test: $(PY) ## Unit tests (scripted model, no API calls); the MCP test runs if observability-mcp is installed
	$(PY) -m pytest -q -rs

run: $(PY) ## Investigate: make run Q="orders-api is returning 503s" [ARGS="--verbose --rag-backend memory"]
	@test -n "$(Q)" || { echo 'usage: make run Q="what is wrong"'; exit 1; }
	@$(VENV)/bin/incident-agent "$(Q)" $(ARGS)
