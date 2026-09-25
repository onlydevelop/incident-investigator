from dataclasses import dataclass
from typing import Protocol

import numpy as np

from incident_rag.chunking import Chunk

# Small, local and good at retrieval: runs on CPU through ONNX, no API key. bge models expect an
# instruction prefix on queries, which fastembed's query_embed adds.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384


class Embedder(Protocol):
    def passage_embed(self, texts: list[str]): ...
    def query_embed(self, query: list[str]): ...


def load_embedder(model: str = EMBEDDING_MODEL) -> Embedder:
    from fastembed import TextEmbedding

    return TextEmbedding(model)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def embed_passages(embedder: Embedder, chunks: list[Chunk]) -> np.ndarray:
    """Unit-length embeddings of the chunks' `text`, one row per chunk."""
    return _normalize(np.array(list(embedder.passage_embed([c.text for c in chunks]))))


def embed_queries(embedder: Embedder, queries: list[str]) -> np.ndarray:
    return _normalize(np.array(list(embedder.query_embed(queries))))


@dataclass(frozen=True)
class Filters:
    """Metadata filters every store supports. None means no filter."""

    doc_type: str | None = None  # "runbook" or "postmortem"
    service: str | None = None  # matches if it is one of the document's services

    def matches(self, chunk: Chunk) -> bool:
        return (self.doc_type is None or chunk.doc_type == self.doc_type) and (
            self.service is None or self.service in chunk.services
        )


NO_FILTERS = Filters()


class Retriever(Protocol):
    """Anything that ranks chunks for a query. Vector retrievers use `vector`, keyword ones `query`,
    hybrid ones both; every retriever gets both so callers don't need to know which kind it is."""

    def search(
        self, query: str, vector: np.ndarray, k: int, filters: Filters = NO_FILTERS
    ) -> list[tuple[Chunk, float]]: ...


class MemoryIndex:
    """Brute-force cosine search in numpy. The corpus is a few hundred chunks, so exact search is fast,
    and it is the reference the database stores should reproduce."""

    def __init__(self, chunks: list[Chunk], vectors: np.ndarray):
        self.chunks = chunks
        self.vectors = vectors

    def search(
        self, query: str, vector: np.ndarray, k: int, filters: Filters = NO_FILTERS
    ) -> list[tuple[Chunk, float]]:
        scores = self.vectors @ vector
        ranked = (i for i in np.argsort(-scores) if filters.matches(self.chunks[i]))
        return [(self.chunks[i], float(scores[i])) for i, _ in zip(ranked, range(k), strict=False)]
