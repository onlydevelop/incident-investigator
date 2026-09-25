# Chunking and retrieval over ../incident-corpus. Runs locally in a venv; the embedding model
# (BAAI/bge-small-en-v1.5, ~130 MB) is downloaded by fastembed on first use.
VENV     := .venv
PY       := $(VENV)/bin/python
STRATEGY ?= heading-merged+ctx

.DEFAULT_GOAL := help
.PHONY: help install test eval misses chunks

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

$(PY):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q -e '.[dev]'

install: $(PY) ## Create the venv and install the package with test dependencies

test: $(PY) ## Run the unit tests (chunkers, corpus loading, eval-set validation)
	$(PY) -m pytest -q

eval: $(PY) ## Compare every chunking strategy on eval/questions.yaml
	$(PY) -m incident_rag.evaluate

misses: $(PY) ## Questions STRATEGY misses at k=3, with what it retrieved instead
	$(PY) -m incident_rag.evaluate --misses $(STRATEGY)

chunks: $(PY) ## Print STRATEGY's chunks
	$(PY) -m incident_rag.evaluate --dump $(STRATEGY)
