"""Chunk and embed the incident corpus, then load it into pgvector and OpenSearch.

    python -m incident_rag.ingest                         # heading-merged+ctx into both stores
    python -m incident_rag.ingest --stores pgvector       # one store only
    python -m incident_rag.ingest --strategy fixed-256+ctx

Re-running replaces the strategy's chunks, so it is safe after editing the corpus.
"""

import argparse
from pathlib import Path

from incident_rag.backends import STORES, Backends
from incident_rag.chunking import STRATEGIES, chunk_corpus
from incident_rag.corpus import load_corpus
from incident_rag.evaluate import DEFAULT_CORPUS
from incident_rag.index import embed_passages, load_embedder

# The strategy the chunking eval recommends (see README).
DEFAULT_STRATEGY = "heading-merged+ctx"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--strategy", choices=STRATEGIES, default=DEFAULT_STRATEGY)
    parser.add_argument("--stores", nargs="+", choices=STORES, default=list(STORES))
    args = parser.parse_args()

    docs = load_corpus(args.corpus)
    chunks = chunk_corpus(docs, args.strategy)
    vectors = embed_passages(load_embedder(), chunks)
    with Backends() as backends:
        backends.ingest(args.stores, args.strategy, chunks, vectors)
    print(f"{args.strategy}: {len(chunks)} chunks from {len(docs)} documents -> {', '.join(args.stores)}")


if __name__ == "__main__":
    main()
