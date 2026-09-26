# MCP server for Prometheus, Loki and Tempo. Runs locally in a venv; the backends are the
# observability stack on k3s (make obs-up from the repo root), reached through their *.localhost ingresses.
VENV := .venv
PY   := $(VENV)/bin/python

.DEFAULT_GOAL := help
.PHONY: help install test test-live coverage run run-http inspect

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

$(PY): pyproject.toml
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -e '.[dev]'
	@touch $(PY)

install: $(PY) ## Create the venv and install the server with test dependencies

test: $(PY) ## Unit tests (fake backends, no cluster needed); the live tests skip unless the stack is up
	$(PY) -m pytest -q -rs

test-live: $(PY) ## Only the live tests: the real server over stdio against the running stack
	$(PY) -m pytest -q -rs -m live

coverage: $(PY) ## Unit tests with branch coverage (fails below 90%)
	$(PY) -m pytest -q -m "not live" --cov --cov-report=term-missing

run: $(PY) ## Run the server on stdio (what an MCP client does)
	$(VENV)/bin/observability-mcp

run-http: $(PY) ## Run the server over streamable HTTP on http://127.0.0.1:8765/mcp
	$(VENV)/bin/observability-mcp --transport streamable-http --port 8765

inspect: $(PY) ## Open the MCP Inspector on the server (needs Node.js)
	npx -y @modelcontextprotocol/inspector $(abspath $(VENV))/bin/observability-mcp
