from typing import Protocol

import numpy as np

from incident_rag.chunking import Chunk

# Small, local and good at retrieval: runs on CPU through ONNX, no API key. bge models expect an
# instruction prefix on queries, which fastembed's query_embed adds.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    def passage_embed(self, texts: list[str]): ...
    def query_embed(self, query: list[str]): ...


def load_embedder(model: str = EMBEDDING_MODEL) -> Embedder:
    from fastembed import TextEmbedding

    return TextEmbedding(model)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def embed_queries(embedder: Embedder, queries: list[str]) -> np.ndarray:
    return _normalize(np.array(list(embedder.query_embed(queries))))


class VectorIndex:
    """Brute-force cosine search. The corpus is a few hundred chunks, so there is nothing to gain from
    an ANN index, and exact search keeps the comparison free of approximation noise."""

    def __init__(self, embedder: Embedder, chunks: list[Chunk]):
        self.chunks = chunks
        self.vectors = _normalize(np.array(list(embedder.passage_embed([c.text for c in chunks]))))

    def search(self, query_vector: np.ndarray, k: int) -> list[tuple[Chunk, float]]:
        scores = self.vectors @ query_vector
        top = np.argsort(-scores)[:k]
        return [(self.chunks[i], float(scores[i])) for i in top]
