"""Named retrieval backends, so the eval and the CLIs pick one with a string.

    memory                 exact cosine search in numpy (no infrastructure; the reference)
    pgvector               cosine search over an HNSW index in Postgres
    opensearch-bm25        keyword search only
    opensearch-knn         vector search only
    opensearch-hybrid      BM25 + k-NN, min-max normalized and averaged
    opensearch-hybrid-rrf  BM25 + k-NN, reciprocal rank fusion
"""

import numpy as np

from incident_rag.chunking import Chunk
from incident_rag.index import MemoryIndex, Retriever

BACKENDS = (
    "memory",
    "pgvector",
    "opensearch-bm25",
    "opensearch-knn",
    "opensearch-hybrid",
    "opensearch-hybrid-rrf",
)
STORES = ("pgvector", "opensearch")


def stores_for(backends: list[str]) -> list[str]:
    """The stores these backends read from."""
    return [store for store in STORES if any(b == store or b.startswith(f"{store}-") for b in backends)]


class Backends:
    """Opens each store the first time it is needed, so backends that are not used need no running
    infrastructure (and their client libraries are only imported then)."""

    def __init__(self):
        self._pgvector = None
        self._opensearch = None

    def __enter__(self) -> "Backends":
        return self

    def __exit__(self, *exc) -> None:
        for store in (self._pgvector, self._opensearch):
            if store is not None:
                store.close()

    @property
    def pgvector(self):
        if self._pgvector is None:
            from incident_rag.pgvector_store import PgVectorStore

            self._pgvector = PgVectorStore()
        return self._pgvector

    @property
    def opensearch(self):
        if self._opensearch is None:
            from incident_rag.opensearch_store import OpenSearchStore

            self._opensearch = OpenSearchStore()
        return self._opensearch

    def ingest(self, stores: list[str], strategy: str, chunks: list[Chunk], vectors: np.ndarray) -> None:
        """Replace `strategy`'s chunks in each of `stores`."""
        if "pgvector" in stores:
            self.pgvector.replace(strategy, chunks, vectors)
        if "opensearch" in stores:
            from incident_rag.opensearch_store import index_name

            self.opensearch.replace(index_name(strategy), chunks, vectors)

    def retriever(
        self, backend: str, strategy: str, chunks: list[Chunk] | None = None, vectors: np.ndarray | None = None
    ) -> Retriever:
        """A retriever over `strategy`'s chunks. The stores must already hold them (see `ingest`); the
        memory backend has no store and is built from `chunks` and `vectors` directly."""
        if backend == "memory":
            if chunks is None or vectors is None:
                raise ValueError("the memory backend needs the chunks and their vectors")
            return MemoryIndex(chunks, vectors)
        if backend == "pgvector":
            return self.pgvector.retriever(strategy)
        if backend.startswith("opensearch-"):
            from incident_rag.opensearch_store import index_name

            return self.opensearch.retriever(index_name(strategy), backend.removeprefix("opensearch-"))
        raise ValueError(f"unknown backend {backend!r}, expected one of {BACKENDS}")
