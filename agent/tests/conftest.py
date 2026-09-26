import hashlib
import re
from pathlib import Path

import numpy as np
import pytest

from incident_agent.knowledge import KnowledgeBase
from incident_rag.chunking import chunk_corpus
from incident_rag.corpus import load_corpus
from incident_rag.index import MemoryIndex, embed_passages
from incident_rag.ingest import DEFAULT_STRATEGY

CORPUS = Path(__file__).resolve().parents[2] / "incident-corpus"
DIM = 256


class HashingEmbedder:
    """Bag of words hashed into a fixed-size vector: deterministic, and no model download. Good enough
    for a query that shares distinctive words with one section to rank it first."""

    def _embed(self, text: str) -> np.ndarray:
        vector = np.zeros(DIM)
        for word in re.findall(r"[a-z0-9_]+", text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1
        return vector + 1e-9

    def passage_embed(self, texts):
        return [self._embed(t) for t in texts]

    def query_embed(self, query):
        return [self._embed(q) for q in query]


@pytest.fixture(scope="session")
def knowledge() -> KnowledgeBase:
    docs = load_corpus(CORPUS)
    embedder = HashingEmbedder()
    chunks = chunk_corpus(docs, DEFAULT_STRATEGY)
    return KnowledgeBase(MemoryIndex(chunks, embed_passages(embedder, chunks)), embedder, docs)
