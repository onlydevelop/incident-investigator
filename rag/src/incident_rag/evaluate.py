"""Compare chunking strategies on the incident corpus.

    python -m incident_rag.evaluate                       # every strategy, in-memory cosine search
    python -m incident_rag.evaluate --strategies heading-merged+ctx \
        --backends pgvector opensearch-bm25 opensearch-hybrid   # compare retrieval backends
    python -m incident_rag.evaluate --misses heading+ctx  # questions a strategy got wrong at k=3
    python -m incident_rag.evaluate --dump heading+ctx    # print a strategy's chunks

Every strategy is embedded once with the same model, and every backend gets the same chunks and the same
vectors, so each row differs from another only in its chunking or its retrieval backend.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import yaml

from incident_rag.backends import BACKENDS, Backends, stores_for
from incident_rag.chunking import STRATEGIES, Chunk, chunk_corpus, count_tokens
from incident_rag.corpus import Document, load_corpus
from incident_rag.index import Retriever, embed_passages, embed_queries, load_embedder

RAG_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = RAG_DIR.parent / "incident-corpus"
DEFAULT_QUESTIONS = RAG_DIR / "eval" / "questions.yaml"
KS = (1, 3, 5)
# Context budgets in tokens. Chunk sizes differ between strategies, so recall at a token budget is the
# fairer comparison: it asks what fraction of questions are answered by the context the LLM would get.
BUDGETS = (500, 1000)
# Reciprocal rank counts hits down to this depth.
MRR_DEPTH = 10
# Chunks retrieved per question; enough to fill the largest budget with the smallest chunks.
SEARCH_DEPTH = 20


@dataclass(frozen=True)
class Answer:
    doc: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Question:
    qid: str
    question: str
    kind: str
    answers: tuple[Answer, ...]


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def load_questions(path: Path, docs: list[Document]) -> list[Question]:
    """Questions from `path`, after checking that every evidence snippet is really in its document."""
    bodies = {doc.doc_id: _normalize(doc.body) for doc in docs}
    questions = []
    for raw in yaml.safe_load(path.read_text()):
        answers = tuple(Answer(a["doc"], tuple(a["evidence"])) for a in raw["answers"])
        for answer in answers:
            if answer.doc not in bodies:
                raise ValueError(f"{raw['id']}: unknown document {answer.doc}")
            for snippet in answer.evidence:
                if _normalize(snippet) not in bodies[answer.doc]:
                    raise ValueError(f"{raw['id']}: evidence not found in {answer.doc}: {snippet!r}")
        questions.append(Question(raw["id"], raw["question"], raw["kind"], answers))
    return questions


def answers(chunk: Chunk, question: Question) -> bool:
    """Whether the chunk holds all the evidence for at least one of the question's answers."""
    content = _normalize(chunk.content)
    return any(
        chunk.doc_id == answer.doc and all(_normalize(snippet) in content for snippet in answer.evidence)
        for answer in question.answers
    )


@dataclass(frozen=True)
class QuestionResult:
    question: Question
    ranked: list[Chunk]
    # 1-based rank of the first chunk that answers the question, None if none in the top SEARCH_DEPTH.
    answer_rank: int | None
    # Whether any chunk in the whole index answers it: fixed-size windows can cut evidence in half.
    answerable: bool

    def hit(self, k: int) -> bool:
        return self.answer_rank is not None and self.answer_rank <= k

    def hit_within(self, budget: int) -> bool:
        """Whether the answer is among the top chunks that fit, in rank order, within `budget` tokens."""
        used = 0
        for rank, chunk in enumerate(self.ranked, start=1):
            used += chunk.tokens
            if used > budget:
                return False
            if rank == self.answer_rank:
                return True
        return False

    def reciprocal_rank(self) -> float:
        return 1 / self.answer_rank if self.answer_rank and self.answer_rank <= MRR_DEPTH else 0.0

    def doc_hit(self, k: int) -> bool:
        wanted = {answer.doc for answer in self.question.answers}
        return any(chunk.doc_id in wanted for chunk in self.ranked[:k])

    def context_tokens(self, k: int) -> int:
        return sum(chunk.tokens for chunk in self.ranked[:k])


def run_questions(
    retriever: Retriever, chunks: list[Chunk], questions: list[Question], query_vectors
) -> list[QuestionResult]:
    results = []
    for question, vector in zip(questions, query_vectors, strict=True):
        ranked = [chunk for chunk, _ in retriever.search(question.question, vector, SEARCH_DEPTH)]
        rank = next((i for i, chunk in enumerate(ranked, start=1) if answers(chunk, question)), None)
        answerable = any(answers(chunk, question) for chunk in chunks)
        results.append(QuestionResult(question, ranked, rank, answerable))
    return results


def _pct(values) -> str:
    values = list(values)
    return f"{100 * sum(values) / len(values):.0f}%" if values else "-"


def print_summary(rows: dict[str, tuple[list[Chunk], list[QuestionResult]]]) -> None:
    print("## Retrieval quality\n")
    print(
        "| Strategy | Chunks | Tokens/chunk (mean / max) | Answerable | Recall@1 | Recall@3 | Recall@5 "
        "| MRR@10 | Right doc@3 | Context tokens@3 | Recall@500 tok | Recall@1000 tok |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, (chunks, results) in rows.items():
        sizes = [chunk.tokens for chunk in chunks]
        mrr = mean(r.reciprocal_rank() for r in results)
        print(
            f"| {name} | {len(chunks)} | {mean(sizes):.0f} / {max(sizes)} "
            f"| {_pct(r.answerable for r in results)} "
            + "".join(f"| {_pct(r.hit(k) for r in results)} " for k in KS)
            + f"| {mrr:.2f} | {_pct(r.doc_hit(3) for r in results)} "
            f"| {mean(r.context_tokens(3) for r in results):.0f} "
            + "".join(f"| {_pct(r.hit_within(budget) for r in results)} " for budget in BUDGETS)
            + "|"
        )

    kinds = sorted({r.question.kind for _, results in rows.values() for r in results})
    print("\n## Recall@3 and Recall@500 tok by question kind\n")
    counts = {kind: sum(r.question.kind == kind for r in next(iter(rows.values()))[1]) for kind in kinds}
    print("| Strategy | " + " | ".join(f"{kind} (n={counts[kind]})" for kind in kinds) + " |")
    print("|---|" + "---|" * len(kinds))
    for name, (_, results) in rows.items():
        cells = [
            f"{_pct(r.hit(3) for r in results if r.question.kind == kind)} / "
            f"{_pct(r.hit_within(500) for r in results if r.question.kind == kind)}"
            for kind in kinds
        ]
        print(f"| {name} | " + " | ".join(cells) + " |")


def print_misses(results: list[QuestionResult], k: int = 3) -> None:
    for r in results:
        if r.hit(k):
            continue
        reason = "evidence split across chunks" if not r.answerable else f"answer rank {r.answer_rank or '>20'}"
        wanted = "/".join(sorted({a.doc for a in r.question.answers}))
        print(f"{r.question.qid} [{wanted}, {reason}] {r.question.question}")
        for chunk in r.ranked[:k]:
            first_line = chunk.content.strip().splitlines()[0][:80]
            print(f"    {chunk.chunk_id:<10} {chunk.section or '-':<28} {first_line}")


def dump_chunks(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        print(f"===== {chunk.chunk_id} ({count_tokens(chunk.text)} tokens) {chunk.section}")
        print(chunk.text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    parser.add_argument(
        "--backends", nargs="+", choices=BACKENDS, default=["memory"], help="retrieval backends to compare"
    )
    parser.add_argument("--misses", choices=STRATEGIES, help="list questions this strategy misses at k=3")
    parser.add_argument("--dump", choices=STRATEGIES, help="print this strategy's chunks and exit")
    args = parser.parse_args()

    docs = load_corpus(args.corpus)
    if args.dump:
        dump_chunks(chunk_corpus(docs, args.dump))
        return

    questions = load_questions(args.questions, docs)
    embedder = load_embedder()
    query_vectors = embed_queries(embedder, [q.question for q in questions])

    strategies = [args.misses] if args.misses else args.strategies
    rows = {}
    with Backends() as backends:
        for strategy in strategies:
            chunks = chunk_corpus(docs, strategy)
            vectors = embed_passages(embedder, chunks)
            backends.ingest(stores_for(args.backends), strategy, chunks, vectors)
            for backend in args.backends:
                retriever = backends.retriever(backend, strategy, chunks, vectors)
                # One backend keeps the table as it was: rows named by strategy alone.
                name = strategy if len(args.backends) == 1 else f"{strategy} · {backend}"
                rows[name] = (chunks, run_questions(retriever, chunks, questions, query_vectors))

    if args.misses:
        for name, (_, results) in rows.items():
            if len(rows) > 1:
                print(f"## {name}")
            print_misses(results)
    else:
        print(f"{len(docs)} documents, {len(questions)} questions\n")
        print_summary(rows)


if __name__ == "__main__":
    main()
