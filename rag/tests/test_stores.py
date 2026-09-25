"""The retrieval backends.

Store tests run against the real pgvector and OpenSearch from ../infra (`make -C ../infra search-up`),
each under a throwaway index name that is removed afterwards, so ingested data is never touched. They are
marked `stores` and skip when the store is not reachable; `pytest -m "not stores"` runs only the rest.
"""

import uuid

import numpy as np
import pytest

from incident_rag.backends import Backends, stores_for
from incident_rag.chunking import Chunk, chunk_corpus
from incident_rag.corpus import load_corpus
from incident_rag.evaluate import DEFAULT_CORPUS
from incident_rag.index import Filters, MemoryIndex


def _chunk(chunk_id: str, doc_type: str, services: tuple[str, ...]) -> Chunk:
    return Chunk(chunk_id, chunk_id.split("#")[0], "c", "t", "", doc_type=doc_type, services=services)


RUNBOOK = _chunk("RB-1#h0", "runbook", ("market-data-service",))
POSTMORTEM = _chunk("PM-1#h0", "postmortem", ("order-risk-service", "market-data-service"))


def test_filters_match_on_type_and_any_listed_service():
    assert Filters().matches(RUNBOOK)
    assert Filters(doc_type="runbook").matches(RUNBOOK)
    assert not Filters(doc_type="runbook").matches(POSTMORTEM)
    assert Filters(service="market-data-service").matches(POSTMORTEM)
    assert not Filters(service="order-risk-service").matches(RUNBOOK)
    assert not Filters(doc_type="runbook", service="order-risk-service").matches(POSTMORTEM)


def test_memory_index_filters_before_taking_k():
    # The best match is a runbook; filtering to postmortems must still return k results.
    vectors = np.array([[1.0, 0.0], [0.8, 0.6], [0.6, 0.8]])
    chunks = [RUNBOOK, POSTMORTEM, _chunk("PM-2#h0", "postmortem", ())]
    results = MemoryIndex(chunks, vectors).search("", np.array([1.0, 0.0]), 2, Filters(doc_type="postmortem"))
    assert [c.chunk_id for c, _ in results] == ["PM-1#h0", "PM-2#h0"]


def test_stores_for_maps_backends_to_the_stores_they_read():
    assert stores_for(["memory"]) == []
    assert stores_for(["pgvector", "memory"]) == ["pgvector"]
    assert stores_for(["opensearch-bm25", "opensearch-hybrid"]) == ["opensearch"]
    assert stores_for(["opensearch-knn", "pgvector"]) == ["pgvector", "opensearch"]


def test_memory_backend_needs_chunks_and_vectors():
    with Backends() as backends, pytest.raises(ValueError):
        backends.retriever("memory", "heading+ctx")


# --- Against the real stores ---


@pytest.fixture(scope="module")
def corpus_chunks():
    """The heading+ctx chunks with deterministic random unit vectors: the stores are under test, not
    the embedding model, and this keeps the tests fast and offline."""
    chunks = chunk_corpus(load_corpus(DEFAULT_CORPUS), "heading+ctx")
    vectors = np.random.default_rng(7).normal(size=(len(chunks), 384))
    return chunks, vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


@pytest.fixture(scope="module")
def backends():
    with Backends() as b:
        yield b


def _store_or_skip(backends: Backends, store: str):
    try:
        return getattr(backends, store)
    except Exception as exc:  # connection refused, DNS, auth: anything means "not available here"
        pytest.skip(f"{store} not reachable ({type(exc).__name__}); run make -C ../infra search-up")


@pytest.fixture(scope="module")
def strategy(backends, corpus_chunks):
    """A throwaway strategy name, ingested into both stores and removed afterwards."""
    name = f"test-{uuid.uuid4().hex[:8]}"
    chunks, vectors = corpus_chunks
    pg = _store_or_skip(backends, "pgvector")
    search = _store_or_skip(backends, "opensearch")
    backends.ingest(["pgvector", "opensearch"], name, chunks, vectors)
    yield name
    from incident_rag.opensearch_store import index_name

    pg.conn.execute("DELETE FROM rag.chunks WHERE index_name = %s", (name,))
    search.client.indices.delete(index=index_name(name), ignore_unavailable=True)


def _ids(results) -> list[str]:
    return [chunk.chunk_id for chunk, _ in results]


@pytest.mark.stores
@pytest.mark.parametrize("backend", ["pgvector", "opensearch-knn"])
def test_vector_stores_reproduce_exact_search(backends, strategy, corpus_chunks, backend):
    chunks, vectors = corpus_chunks
    memory = MemoryIndex(chunks, vectors)
    retriever = backends.retriever(backend, strategy)
    for query_vector in vectors[:5]:
        assert _ids(retriever.search("", query_vector, 10)) == _ids(memory.search("", query_vector, 10))


@pytest.mark.stores
@pytest.mark.parametrize("backend", ["pgvector", "opensearch-knn", "opensearch-hybrid", "opensearch-hybrid-rrf"])
def test_stores_apply_filters_and_still_return_k(backends, strategy, corpus_chunks, backend):
    chunks, vectors = corpus_chunks
    filters = Filters(doc_type="postmortem", service="market-data-service")
    expected = MemoryIndex(chunks, vectors).search("", vectors[0], 5, filters)
    results = backends.retriever(backend, strategy).search("reconnect rate limit", vectors[0], 5, filters)
    assert len(results) == 5
    assert all(filters.matches(chunk) for chunk, _ in results)
    if backend in ("pgvector", "opensearch-knn"):
        assert _ids(results) == _ids(expected)


@pytest.mark.stores
def test_stored_chunks_round_trip(backends, strategy, corpus_chunks):
    chunks, vectors = corpus_chunks
    [(chunk, _)] = backends.retriever("pgvector", strategy).search("", vectors[3], 1)
    assert chunk == chunks[3]
    [(chunk, _)] = backends.retriever("opensearch-knn", strategy).search("", vectors[3], 1)
    assert chunk == chunks[3]


@pytest.mark.stores
def test_bm25_ranks_by_keywords(backends, strategy):
    [(chunk, _)] = backends.retriever("opensearch-bm25", strategy).search("chronyc timedatectl", np.zeros(384), 1)
    assert chunk.doc_id == "RB-012"
