"""Chunks in OpenSearch, searchable by BM25, by k-NN, or by both at once (hybrid).

One index per chunking strategy. Hybrid search runs the BM25 and k-NN queries separately and fuses the
two result lists in a search pipeline:

- `hybrid`: min-max normalizes each list's scores to 0..1, then takes their arithmetic mean.
- `hybrid-rrf`: reciprocal rank fusion, which ignores scores and sums 1 / (60 + rank).

Both weigh BM25 and k-NN equally. The weights are deliberately not tuned on the eval set.
"""

import os
import re

import numpy as np
from opensearchpy import OpenSearch, helpers

from incident_rag.chunking import Chunk
from incident_rag.index import EMBEDDING_DIM, NO_FILTERS, Filters

# The shared infra OpenSearch (../infra, `make search-up`). Security is off for local development.
OPENSEARCH_URL = os.environ.get("RAG_OPENSEARCH_URL", "http://localhost:9200")
INDEX_PREFIX = "incident-chunks-"
MODES = ("bm25", "knn", "hybrid", "hybrid-rrf")
# Each sub-query of a hybrid search returns this many candidates before fusion. Fusing only the top k of
# each would drop chunks that rank moderately in both lists, which is where hybrid search helps most.
HYBRID_CANDIDATES = 50

PIPELINES = {
    "hybrid": {
        "description": "Min-max normalize BM25 and k-NN scores, then average them",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {"technique": "arithmetic_mean"},
                }
            }
        ],
    },
    "hybrid-rrf": {
        "description": "Reciprocal rank fusion of BM25 and k-NN results",
        "phase_results_processors": [
            {"score-ranker-processor": {"combination": {"technique": "rrf", "rank_constant": 60}}}
        ],
    },
}

INDEX_BODY = {
    "settings": {"index": {"knn": True, "number_of_shards": 1, "number_of_replicas": 0}},
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "doc_id": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "services": {"type": "keyword"},
            "severity": {"type": "keyword"},
            "section": {"type": "keyword"},
            # BM25 runs over `text`, the same string that is embedded, so both halves of a hybrid search
            # see the same context line. The english analyzer stems ("rebalances" matches "rebalance").
            "text": {"type": "text", "analyzer": "english"},
            "content": {"type": "text", "index": False},
            "embedding": {
                "type": "knn_vector",
                "dimension": EMBEDDING_DIM,
                # Lucene's HNSW supports cosine and filters applied during the graph search.
                "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
            },
        }
    },
}


def index_name(strategy: str) -> str:
    """OpenSearch index names are lowercase with no "+": heading-merged+ctx -> incident-chunks-heading-merged-ctx."""
    return INDEX_PREFIX + re.sub(r"[^a-z0-9]+", "-", strategy.lower()).strip("-")


class OpenSearchStore:
    def __init__(self, url: str = OPENSEARCH_URL):
        self.client = OpenSearch(url, timeout=30)
        for name, body in PIPELINES.items():
            self.client.transport.perform_request("PUT", f"/_search/pipeline/rag-{name}", body=body)

    def close(self) -> None:
        self.client.close()

    def replace(self, index: str, chunks: list[Chunk], vectors: np.ndarray) -> None:
        """Recreate `index` with exactly these chunks."""
        self.client.indices.delete(index=index, ignore_unavailable=True)
        self.client.indices.create(index=index, body=INDEX_BODY)
        helpers.bulk(
            self.client,
            (
                {
                    "_index": index,
                    "_id": c.chunk_id,
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "doc_type": c.doc_type,
                    "services": list(c.services),
                    "severity": c.severity,
                    "section": c.section,
                    "text": c.text,
                    "content": c.content,
                    "embedding": vector.tolist(),
                }
                for c, vector in zip(chunks, vectors, strict=True)
            ),
            refresh=True,
        )

    def retriever(self, index: str, mode: str) -> "OpenSearchRetriever":
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}, expected one of {MODES}")
        return OpenSearchRetriever(self, index, mode)


def _filter_clauses(filters: Filters) -> list[dict]:
    clauses = []
    if filters.doc_type is not None:
        clauses.append({"term": {"doc_type": filters.doc_type}})
    if filters.service is not None:
        clauses.append({"term": {"services": filters.service}})
    return clauses


class OpenSearchRetriever:
    def __init__(self, store: OpenSearchStore, index: str, mode: str):
        self.store = store
        self.index = index
        self.mode = mode

    def _bm25(self, query: str, filters: Filters) -> dict:
        return {"bool": {"must": [{"match": {"text": query}}], "filter": _filter_clauses(filters)}}

    def _knn(self, vector: np.ndarray, k: int, filters: Filters) -> dict:
        knn = {"vector": vector.tolist(), "k": k}
        if clauses := _filter_clauses(filters):
            knn["filter"] = {"bool": {"filter": clauses}}
        return {"knn": {"embedding": knn}}

    def search(
        self, query: str, vector: np.ndarray, k: int, filters: Filters = NO_FILTERS
    ) -> list[tuple[Chunk, float]]:
        params = {}
        if self.mode == "bm25":
            body_query = self._bm25(query, filters)
        elif self.mode == "knn":
            body_query = self._knn(vector, k, filters)
        else:
            candidates = max(k, HYBRID_CANDIDATES)
            body_query = {"hybrid": {"queries": [self._bm25(query, filters), self._knn(vector, candidates, filters)]}}
            params["search_pipeline"] = f"rag-{self.mode}"
        response = self.store.client.search(
            index=self.index,
            body={"size": k, "query": body_query, "_source": {"excludes": ["embedding"]}},
            params=params,
        )
        return [(_chunk_from_source(hit["_source"]), hit["_score"]) for hit in response["hits"]["hits"]]


def _chunk_from_source(source: dict) -> Chunk:
    return Chunk(
        chunk_id=source["chunk_id"],
        doc_id=source["doc_id"],
        doc_type=source["doc_type"],
        services=tuple(source["services"]),
        severity=source["severity"],
        section=source["section"],
        content=source["content"],
        text=source["text"],
    )
