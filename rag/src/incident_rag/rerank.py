"""Cross-encoder reranking on top of any retriever.

A first-stage retriever (vector, BM25 or hybrid) scores the query and each chunk independently, so it is
fast but only approximates relevance. A cross-encoder reads the query and one chunk together and scores
the pair, which is far more accurate and far too slow to run over the whole corpus. So the first stage
fetches RERANK_CANDIDATES chunks and the cross-encoder reorders them. Reranking can only reorder: an
answer the first stage leaves out of its candidates stays out.
"""

import numpy as np

from incident_rag.chunking import Chunk
from incident_rag.index import NO_FILTERS, Filters, Retriever

# Short name (used in backend names like opensearch-hybrid+rerank-bge) -> fastembed model.
RERANKERS = {
    # 22M parameters, trained on MS MARCO web search. The usual fast baseline.
    "minilm": "Xenova/ms-marco-MiniLM-L-6-v2",
    # 278M parameters, same family as the embedding model; slower, generally stronger.
    "bge": "BAAI/bge-reranker-base",
}
# Candidates the first stage returns for the cross-encoder to reorder. More candidates can only raise
# recall, but reranking cost grows linearly with them.
RERANK_CANDIDATES = 30


class Reranker:
    def __init__(self, name: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.name = name
        self.model = TextCrossEncoder(RERANKERS[name])

    def score(self, query: str, texts: list[str]) -> list[float]:
        """Relevance of each text to the query; higher is more relevant. Only the order is meaningful."""
        return [float(s) for s in self.model.rerank(query, texts)]


class RerankedRetriever:
    def __init__(self, base: Retriever, reranker: Reranker, candidates: int = RERANK_CANDIDATES):
        self.base = base
        self.reranker = reranker
        self.candidates = candidates

    def search(
        self, query: str, vector: np.ndarray, k: int, filters: Filters = NO_FILTERS
    ) -> list[tuple[Chunk, float]]:
        candidates = [chunk for chunk, _ in self.base.search(query, vector, max(k, self.candidates), filters)]
        if not candidates:
            return []
        # Score the embedded text, context line included: the breadcrumb tells the cross-encoder which
        # runbook or postmortem a section belongs to, just as it does for the embedding model.
        scores = self.reranker.score(query, [chunk.text for chunk in candidates])
        # Stable, so ties keep the first stage's order.
        order = np.argsort(-np.array(scores), kind="stable")[:k]
        return [(candidates[i], scores[i]) for i in order]
