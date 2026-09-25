# incident-rag

Chunking and retrieval over [`incident-corpus`](../incident-corpus). Embeddings are stored in pgvector, and OpenSearch provides hybrid search (BM25 plus vectors). Two evals back the design choices: one compares chunking strategies, the other compares retrieval backends.

```bash
make infra    # start pgvector (Postgres) and OpenSearch from ../infra
make ingest   # chunk, embed and load the corpus into both stores
make search Q="orders rejected as stale but the feed looks healthy"
make search Q="consumer lag" BACKEND=pgvector ARGS="--type runbook -k 3 --full"

make test            # unit tests; store tests skip unless `make infra` is up
make eval            # compare chunking strategies (in-memory, no infra needed)
make eval-backends   # compare pgvector, BM25, k-NN and hybrid search
make misses STRATEGY=heading+ctx BACKEND=memory   # what a combination gets wrong, and what it retrieved instead
make chunks STRATEGY=heading+ctx                  # inspect a strategy's chunks
```

Recommended setup, based on both evals: **`heading-merged+ctx` chunks, searched with `opensearch-hybrid`**. `ingest` and `search` use these by default.

## Layout

| Module | What it does |
|---|---|
| [`corpus.py`](src/incident_rag/corpus.py) | Loads the markdown documents and their YAML frontmatter |
| [`chunking.py`](src/incident_rag/chunking.py) | Fixed-size and heading-aware chunkers; each chunk carries the document's type, services and severity for filtering |
| [`index.py`](src/incident_rag/index.py) | Embedding (fastembed, `bge-small-en-v1.5`, 384 dimensions), the `Retriever` interface, metadata `Filters`, and the in-memory reference index |
| [`pgvector_store.py`](src/incident_rag/pgvector_store.py) | `rag.chunks` table with an HNSW cosine index. Every strategy is stored side by side, keyed by `index_name` |
| [`opensearch_store.py`](src/incident_rag/opensearch_store.py) | One index per strategy, holding a BM25 `text` field (english analyzer) and a Lucene HNSW `knn_vector`, plus the two hybrid search pipelines |
| [`backends.py`](src/incident_rag/backends.py) | Maps backend names to retrievers and opens stores only when they are used |
| [`ingest.py`](src/incident_rag/ingest.py), [`search.py`](src/incident_rag/search.py) | Command-line tools for loading and querying the stores |
| [`evaluate.py`](src/incident_rag/evaluate.py) | The eval harness |

Connection settings come from `RAG_DATABASE_URL` (default `postgresql://portfolio:portfolio@localhost:5432/portfolio`) and `RAG_OPENSEARCH_URL` (default `http://localhost:9200`).

Embeddings are computed once, in Python, and the same vectors go to every store. That's why the vector-only backends can be checked against each other exactly.

## Chunking strategies

| Strategy | How it splits |
|---|---|
| `fixed-128`, `fixed-256` | Token windows (cl100k) with 1/8 overlap. Ignores markdown. |
| `fixed-512` | Same, but every document fits in one window, so this is "don't chunk". |
| `heading` | One chunk per markdown section. Sections under 40 tokens merge into a neighbour, and sections over 300 tokens split at paragraphs or top-level list items. Fenced code is never split, and `#` inside code is not a heading. |
| `heading-merged` | Same, but sections merge until they reach 150 tokens, so chunk sizes are close to `fixed-256`. |
| `+ctx` | Prepends a context line to the embedded text: the document title for fixed chunks, the breadcrumb (`RB-005: PostgreSQL connection pool exhaustion > Triage`) for heading chunks. |

Everything else is held constant: the embedding model (`BAAI/bge-small-en-v1.5` via fastembed, local), the exact cosine search, and the questions.

## Eval set

[`eval/questions.yaml`](eval/questions.yaml) has 57 questions written the way an on-call engineer would ask them, covering all 20 documents.

- **How a hit is scored:** each question lists answers as (document, evidence snippets). A chunk counts as a hit only if it comes from that document and contains every snippet. A chunk that cuts the evidence in half is a miss.
- **Validation:** the loader rejects any snippet that isn't in the corpus.
- **Question kinds:**
  - 50 `single-section` questions: the answer sits in one section.
  - 7 `cross-section` questions: the answer needs a diagnosis from one section and an action from another.

## Chunking results

| Strategy | Chunks | Tokens/chunk | Answerable¹ | Recall@1 | Recall@3 | MRR@10 | Context tok@3 | **Recall@500 tok²** | **Recall@1000 tok²** |
|---|---|---|---|---|---|---|---|---|---|
| fixed-128 | 81 | 112 | 89% | 46% | 68% | 0.58 | 351 | 72% | 81% |
| fixed-128+ctx | 81 | 123 | 89% | 46% | 74% | 0.62 | 384 | 74% | 88% |
| fixed-256 | 41 | 213 | 95% | 60% | 82% | 0.72 | 645 | 74% | 86% |
| fixed-256+ctx | 41 | 225 | 95% | 60% | 84% | 0.73 | 678 | 75% | 86% |
| fixed-512 (whole doc) | 20 | 403 | 100% | 79% | 95% | 0.87 | 1240 | 79% | 91% |
| heading | 78 | 103 | 88% | 40% | 65% | 0.55 | 336 | 68% | 81% |
| heading+ctx | 78 | 119 | 88% | 51% | 75% | 0.64 | 402 | 79% | 86% |
| **heading-merged+ctx** | 45 | 198 | 93% | 56% | **89%** | 0.73 | 608 | **81%** | **93%** |

Recall@3 / Recall@500 tok by question kind:

| Strategy | single-section (n=50) | cross-section (n=7) |
|---|---|---|
| fixed-128+ctx | 82% / 82% | 14% / 14% |
| fixed-256+ctx | 88% / 78% | 57% / 57% |
| fixed-512 | 96% / 78% | 86% / 86% |
| heading+ctx | 86% / **90%** | 0% / 0% |
| heading-merged+ctx | **96%** / 88% | 43% / 29% |

¹ Share of questions that some chunk in the index can answer at all. Below 100% means the chunker split the evidence.
² Chunks are added in rank order until the next one would go over the budget. Chunk sizes differ between strategies, so this is the fair comparison: how often the answer is in the context the LLM would get.

## What the chunking numbers say

1. **Heading-aware wins once you add context and compare at equal token cost.** `heading-merged+ctx` has the best recall at both budgets and the best Recall@3. On single-section questions at a 500-token budget, plain `heading+ctx` gets 90% against 78–82% for every fixed-size strategy. Small, self-contained sections let more distinct answers fit in the same budget.
2. **The context line matters more than the boundaries.** Heading chunks without a breadcrumb do worst of all (68% at 500 tokens). A section like "## Verify / Lag trending to zero" means nothing without knowing which runbook it belongs to. Fixed-size chunks gain less from a title line because their problem is cut-off content, not missing context.
3. **Heading chunks lose on cross-section questions.** "What's wrong and how do I fix it" needs the Triage and Mitigation sections together, and one-section chunks can never hold both (0%). Merging sections recovers some of this. For the agent, the better fix is at retrieval time: once one chunk of a runbook hits, pull in its sibling sections, or fetch the whole runbook by ID.
4. **Whole-document chunks look best at k=3 only because they bring in 3× the tokens.** At equal budget they're in the middle. They also don't scale:
   - 3 of the 20 documents already exceed bge-small's 512-token input, so the end of those documents isn't embedded at all.
   - Longer post-mortems (real ones often run to thousands of tokens) would make this worse.
5. **Small fixed chunks are the worst option.** `fixed-128` splits the evidence for 11% of questions, and many of its chunks start mid-sentence (`-004.`, `.`), which wastes retrieval slots.

**Recommendation:** use `heading-merged+ctx` for indexing. At query time, expand a hit to its sibling sections or to the whole document when the question asks for a procedure.

## Retrieval backends

`make eval-backends` runs the same chunks, the same vectors and the same 57 questions through each backend:

| Backend | How it ranks |
|---|---|
| `memory` | Exact cosine similarity in numpy. The reference result |
| `pgvector` | Cosine distance over an HNSW index, with `hnsw.iterative_scan = strict_order` so filtered queries still return k rows |
| `opensearch-knn` | Lucene HNSW, cosine |
| `opensearch-bm25` | BM25 over the embedded text (context line included), english analyzer |
| `opensearch-hybrid` | BM25 and k-NN (50 candidates each), each list's scores min–max normalized to 0..1, then averaged |
| `opensearch-hybrid-rrf` | The same two lists fused by reciprocal rank (rank constant 60) |

In both hybrid modes BM25 and k-NN are weighted equally. The weights were not tuned on this eval set, because tuning on the same 57 questions would overfit them.

Results for `heading-merged+ctx`, the recommended chunking:

| Backend | Recall@1 | Recall@3 | MRR@10 | Right doc@3 | Recall@500 tok | Recall@1000 tok |
|---|---|---|---|---|---|---|
| memory / pgvector / opensearch-knn | 56% | 89% | 0.73 | 98% | 81% | 93% |
| opensearch-bm25 | 61% | 82% | 0.73 | 93% | 74% | 84% |
| **opensearch-hybrid** | **72%** | 88% | **0.81** | 98% | **84%** | 89% |
| opensearch-hybrid-rrf | 67% | 89% | 0.78 | 96% | 81% | 91% |

The same pattern holds for the other chunkings. With `heading+ctx`, Recall@1 is 51% for k-NN and 58% for hybrid. With `fixed-256+ctx` it's 60% for k-NN and 68% for hybrid.

What the numbers say:

1. **The vector stores are correct.** pgvector and OpenSearch k-NN return exactly the in-memory ranking on every question. With a few hundred chunks HNSW is effectively exact, so any difference would have pointed to a bug in indexing or querying. The store tests check this directly, with filters applied too.
2. **Hybrid search mainly improves the top result.** Recall@1 goes from 56% to 72%, and MRR from 0.73 to 0.81. Recall@3 stays flat. For an agent that reads only the first few chunks, getting the best one to the top is what counts.
3. **The two halves fix different questions.**
   - BM25 wins when the question contains an exact identifier: `NOT_ENOUGH_REPLICAS`, `KafkaStorageException`, `exit code 137`, `order-risk-marker`. Vectors rank those answers second or third.
   - Vectors win on paraphrase: "stops sending telemetry altogether" finds the dead-man's-switch alert, which BM25 ranks 7th.
   - Hybrid moves 11 answers up to rank 1 and knocks 2 down from it. Both of those contain a number or phrase BM25 matches in the wrong document, e.g. `+241 s`.
4. **BM25 alone is weaker than vectors,** except at Recall@1. On-call questions are paraphrases more often than exact keyword matches.
5. **Min–max and RRF are close.** Min–max has the better Recall@1 and MRR; RRF is slightly better at 1000 tokens. The gaps are within noise (a few questions). Min–max has one known quirk: a chunk that tops one list and is missing from the other scores exactly 0.5, so ties are common. You'll see several 0.500 scores in `make search` output.

## Caveats

- **Small sample:** 57 questions, so one question is about 2 points. Treat gaps under about 5 points as noise. The conclusions above rest on larger gaps.
- **Built with the corpus in view:** the questions and the heading chunker were written after reading the corpus, and the corpus follows strict templates. Expect smaller gains on messier, less structured documents.
- **Fixed model and scoring:** results are for one small embedding model and pure dense retrieval, with no reranker and no BM25. The harness makes it cheap to rerun with others.
- **Hybrid results are specific to this corpus:** the corpus is full of exact identifiers (metric names, error codes, alert names), which is where BM25 shines. A corpus written in plainer prose would gain less from hybrid search.
- **Not the agent's golden set:** these questions test retrieval over what the corpus says. The agent's golden-set incidents stay out of the corpus (see its README).
