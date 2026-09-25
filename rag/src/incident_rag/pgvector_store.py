"""Chunks and their embeddings in Postgres with pgvector.

Every chunking strategy is stored side by side in one table, keyed by `index_name`, so the eval can
compare strategies without separate tables. Search is cosine distance over an HNSW index.
"""

import os

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from incident_rag.chunking import Chunk
from incident_rag.index import EMBEDDING_DIM, NO_FILTERS, Filters

# The shared infra Postgres (../infra), which runs the pgvector image. The rag schema keeps these
# tables apart from order-service's.
DATABASE_URL = os.environ.get("RAG_DATABASE_URL", "postgresql://portfolio:portfolio@localhost:5432/portfolio")

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag;
CREATE TABLE IF NOT EXISTS rag.chunks (
    index_name  text NOT NULL,
    chunk_id    text NOT NULL,
    doc_id      text NOT NULL,
    doc_type    text NOT NULL,
    services    text[] NOT NULL,
    severity    text NOT NULL,
    section     text NOT NULL,
    content     text NOT NULL,
    text        text NOT NULL,
    embedding   vector({EMBEDDING_DIM}) NOT NULL,
    PRIMARY KEY (index_name, chunk_id)
);
-- Embeddings are unit length, so cosine and inner product rank the same; cosine is the safer default
-- if a model without normalized output is swapped in.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON rag.chunks USING hnsw (embedding vector_cosine_ops);
"""

COLUMNS = "chunk_id, doc_id, doc_type, services, severity, section, content, text"


class PgVectorStore:
    def __init__(self, url: str = DATABASE_URL):
        self.conn = psycopg.connect(url, autocommit=True)
        self.conn.execute(SCHEMA_SQL)
        register_vector(self.conn)
        # With a WHERE clause (index_name, filters), HNSW otherwise returns only the matches among its
        # first ef_search candidates, which can be fewer than k. Iterative scans keep going until k rows
        # match (pgvector 0.8+).
        self.conn.execute("SET hnsw.iterative_scan = strict_order")

    def close(self) -> None:
        self.conn.close()

    def replace(self, index_name: str, chunks: list[Chunk], vectors: np.ndarray) -> None:
        """Make `index_name` hold exactly these chunks."""
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("DELETE FROM rag.chunks WHERE index_name = %s", (index_name,))
            cur.executemany(
                f"INSERT INTO rag.chunks (index_name, {COLUMNS}, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (index_name, c.chunk_id, c.doc_id, c.doc_type, list(c.services), c.severity, c.section,
                     c.content, c.text, vector.astype(np.float32))
                    for c, vector in zip(chunks, vectors, strict=True)
                ],
            )

    def retriever(self, index_name: str) -> "PgVectorRetriever":
        return PgVectorRetriever(self, index_name)


class PgVectorRetriever:
    def __init__(self, store: PgVectorStore, index_name: str):
        self.store = store
        self.index_name = index_name

    def search(
        self, query: str, vector: np.ndarray, k: int, filters: Filters = NO_FILTERS
    ) -> list[tuple[Chunk, float]]:
        where = ["index_name = %(index)s"]
        if filters.doc_type is not None:
            where.append("doc_type = %(doc_type)s")
        if filters.service is not None:
            where.append("%(service)s = ANY(services)")
        rows = self.store.conn.execute(
            f"SELECT {COLUMNS}, 1 - (embedding <=> %(vector)s) AS score FROM rag.chunks "
            f"WHERE {' AND '.join(where)} ORDER BY embedding <=> %(vector)s LIMIT %(k)s",
            {
                "index": self.index_name,
                "doc_type": filters.doc_type,
                "service": filters.service,
                "vector": vector.astype(np.float32),
                "k": k,
            },
        ).fetchall()
        return [
            (
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    doc_type=doc_type,
                    services=tuple(services),
                    severity=severity,
                    section=section,
                    content=content,
                    text=text,
                ),
                float(score),
            )
            for chunk_id, doc_id, doc_type, services, severity, section, content, text, score in rows
        ]
