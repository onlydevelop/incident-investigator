import numpy as np
import pytest

from incident_rag.backends import Backends, split_backend, stores_for
from incident_rag.chunking import Chunk
from incident_rag.index import Filters, MemoryIndex
from incident_rag.rerank import RerankedRetriever


def _chunk(chunk_id: str, text: str, doc_type: str = "runbook") -> Chunk:
    return Chunk(chunk_id, chunk_id.split("#")[0], text, text, "", doc_type=doc_type)


class KeywordReranker:
    """Stands in for a cross-encoder: scores a text by how many query words it contains."""

    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, texts: list[str]) -> list[float]:
        self.calls.append((query, texts))
        words = set(query.split())
        return [float(len(words & set(text.split()))) for text in texts]


# First-stage order by vector: a, b, c, d. By keywords for "disk full", d is best, then b.
CHUNKS = [
    _chunk("A#h0", "consumer lag"),
    _chunk("B#h0", "disk usage", "postmortem"),
    _chunk("C#h0", "stale price"),
    _chunk("D#h0", "broker disk full", "postmortem"),
]
VECTORS = np.array([[1.0, 0.0], [0.9, 0.44], [0.8, 0.6], [0.6, 0.8]])
QUERY_VECTOR = np.array([1.0, 0.0])


def _ids(results) -> list[str]:
    return [chunk.chunk_id for chunk, _ in results]


def test_reranker_reorders_the_first_stage_candidates():
    retriever = RerankedRetriever(MemoryIndex(CHUNKS, VECTORS), KeywordReranker(), candidates=4)
    results = retriever.search("disk full", QUERY_VECTOR, 2)
    assert _ids(results) == ["D#h0", "B#h0"]
    assert [score for _, score in results] == [2.0, 1.0]


def test_reranker_only_sees_the_candidates_and_cannot_recover_the_rest():
    reranker = KeywordReranker()
    retriever = RerankedRetriever(MemoryIndex(CHUNKS, VECTORS), reranker, candidates=3)
    results = retriever.search("disk full", QUERY_VECTOR, 3)
    # D is the best match but ranks 4th in the first stage, outside the 3 candidates.
    assert "D#h0" not in _ids(results)
    assert len(reranker.calls[0][1]) == 3


def test_reranker_fetches_at_least_k_candidates():
    reranker = KeywordReranker()
    RerankedRetriever(MemoryIndex(CHUNKS, VECTORS), reranker, candidates=1).search("disk", QUERY_VECTOR, 3)
    assert len(reranker.calls[0][1]) == 3


def test_reranker_keeps_first_stage_order_on_ties():
    retriever = RerankedRetriever(MemoryIndex(CHUNKS, VECTORS), KeywordReranker(), candidates=4)
    assert _ids(retriever.search("nothing matches", QUERY_VECTOR, 4)) == ["A#h0", "B#h0", "C#h0", "D#h0"]


def test_reranker_passes_filters_to_the_first_stage():
    retriever = RerankedRetriever(MemoryIndex(CHUNKS, VECTORS), KeywordReranker(), candidates=4)
    results = retriever.search("stale price", QUERY_VECTOR, 4, Filters(doc_type="postmortem"))
    assert set(_ids(results)) == {"B#h0", "D#h0"}


def test_reranker_with_no_candidates_returns_nothing():
    reranker = KeywordReranker()
    retriever = RerankedRetriever(MemoryIndex(CHUNKS, VECTORS), reranker)
    assert retriever.search("disk", QUERY_VECTOR, 3, Filters(doc_type="nonexistent")) == []
    assert reranker.calls == []


def test_split_backend_and_stores_understand_the_rerank_suffix():
    assert split_backend("opensearch-hybrid+rerank-bge") == ("opensearch-hybrid", "bge")
    assert split_backend("pgvector") == ("pgvector", None)
    assert stores_for(["memory+rerank-minilm"]) == []
    assert stores_for(["opensearch-bm25+rerank-bge", "pgvector"]) == ["pgvector", "opensearch"]


def test_unknown_reranker_is_rejected():
    with Backends() as backends, pytest.raises(ValueError, match="unknown reranker"):
        backends.retriever("memory+rerank-nope", "heading+ctx", CHUNKS, VECTORS)
