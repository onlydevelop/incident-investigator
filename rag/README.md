# incident-rag

Chunking and retrieval over [`incident-corpus`](../incident-corpus), with an eval that compares fixed-size chunking against heading-aware chunking.

```bash
make test     # unit tests
make eval     # compare all strategies
make misses STRATEGY=heading+ctx   # what a strategy gets wrong, and what it retrieved instead
make chunks STRATEGY=heading+ctx   # inspect a strategy's chunks
```

## Strategies

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

## Results

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

## What the numbers say

1. **Heading-aware wins once you add context and compare at equal token cost.** `heading-merged+ctx` has the best recall at both budgets and the best Recall@3. On single-section questions at a 500-token budget, plain `heading+ctx` gets 90% against 78–82% for every fixed-size strategy. Small, self-contained sections let more distinct answers fit in the same budget.
2. **The context line matters more than the boundaries.** Heading chunks without a breadcrumb do worst of all (68% at 500 tokens). A section like "## Verify / Lag trending to zero" means nothing without knowing which runbook it belongs to. Fixed-size chunks gain less from a title line because their problem is cut-off content, not missing context.
3. **Heading chunks lose on cross-section questions.** "What's wrong and how do I fix it" needs the Triage and Mitigation sections together, and one-section chunks can never hold both (0%). Merging sections recovers some of this. For the agent, the better fix is at retrieval time: once one chunk of a runbook hits, pull in its sibling sections, or fetch the whole runbook by ID.
4. **Whole-document chunks look best at k=3 only because they bring in 3× the tokens.** At equal budget they're in the middle. They also don't scale:
   - 3 of the 20 documents already exceed bge-small's 512-token input, so the end of those documents isn't embedded at all.
   - Longer post-mortems (real ones often run to thousands of tokens) would make this worse.
5. **Small fixed chunks are the worst option.** `fixed-128` splits the evidence for 11% of questions, and many of its chunks start mid-sentence (`-004.`, `.`), which wastes retrieval slots.

**Recommendation:** use `heading-merged+ctx` for indexing. At query time, expand a hit to its sibling sections or to the whole document when the question asks for a procedure.

## Caveats

- **Small sample:** 57 questions, so one question is about 2 points. Treat gaps under about 5 points as noise. The conclusions above rest on larger gaps.
- **Built with the corpus in view:** the questions and the heading chunker were written after reading the corpus, and the corpus follows strict templates. Expect smaller gains on messier, less structured documents.
- **Fixed model and scoring:** results are for one small embedding model and pure dense retrieval, with no reranker and no BM25. The harness makes it cheap to rerun with others.
- **Not the agent's golden set:** these questions test retrieval over what the corpus says. The agent's golden-set incidents stay out of the corpus (see its README).
