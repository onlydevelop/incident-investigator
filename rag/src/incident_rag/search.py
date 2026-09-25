"""Search the ingested incident corpus.

    python -m incident_rag.search "orders rejected as stale but the feed looks healthy"
    python -m incident_rag.search "consumer lag" --backend pgvector --type runbook
    python -m incident_rag.search "pool exhausted" --service order-risk-service -k 3 --full

Run `python -m incident_rag.ingest` first; the strategy must match what was ingested.
"""

import argparse

from incident_rag.backends import BACKENDS, Backends, split_backend
from incident_rag.chunking import STRATEGIES
from incident_rag.index import Filters, embed_queries, load_embedder
from incident_rag.ingest import DEFAULT_STRATEGY


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query")
    # memory needs the chunks in hand, which only the eval has; searching is for the stores.
    parser.add_argument(
        "--backend", choices=[b for b in BACKENDS if split_backend(b)[0] != "memory"], default="opensearch-hybrid"
    )
    parser.add_argument("--strategy", choices=STRATEGIES, default=DEFAULT_STRATEGY)
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--type", dest="doc_type", choices=["runbook", "postmortem"])
    parser.add_argument("--service", help="only documents that list this service, e.g. market-data-service")
    parser.add_argument("--full", action="store_true", help="print each chunk's whole content")
    args = parser.parse_args()

    [vector] = embed_queries(load_embedder(), [args.query])
    filters = Filters(doc_type=args.doc_type, service=args.service)
    with Backends() as backends:
        results = backends.retriever(args.backend, args.strategy).search(args.query, vector, args.k, filters)

    if not results:
        print("No results. Has this strategy been ingested? python -m incident_rag.ingest --strategy ...")
    for rank, (chunk, score) in enumerate(results, start=1):
        print(f"{rank}. {chunk.chunk_id:<10} {score:.3f}  {chunk.section or '-'}")
        if args.full:
            print("   " + chunk.content.replace("\n", "\n   ") + "\n")


if __name__ == "__main__":
    main()
