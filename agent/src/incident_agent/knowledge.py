"""The incident corpus as two agent tools: search its chunks, and read one document whole.

Retrieval is chunk-level, but an answer often needs a runbook's Triage and Mitigation sections
together (rag/README.md, "cross-section" questions). So search results say which document they come
from, and `get_incident_doc` fetches that document in full.
"""

import json
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated, Literal, Optional

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import Field

from incident_rag.backends import Backends
from incident_rag.chunking import chunk_corpus
from incident_rag.corpus import Document, load_corpus
from incident_rag.index import Embedder, Filters, Retriever, embed_passages, embed_queries, load_embedder


class KnowledgeBase:
    def __init__(self, retriever: Retriever, embedder: Embedder, docs: list[Document]):
        self.retriever = retriever
        self.embedder = embedder
        self.docs = {doc.doc_id: doc for doc in docs}

    def search(self, query: str, doc_type: Optional[str] = None, k: int = 5) -> list[dict]:
        [vector] = embed_queries(self.embedder, [query])
        results = self.retriever.search(query, vector, k, Filters(doc_type=doc_type))
        return [
            {
                "doc_id": chunk.doc_id,
                "title": self.docs[chunk.doc_id].title if chunk.doc_id in self.docs else chunk.doc_id,
                "section": chunk.section,
                "score": round(score, 3),
                "content": chunk.content,
            }
            for chunk, score in results
        ]

    def document(self, doc_id: str) -> Document:
        doc = self.docs.get(doc_id.strip().upper())
        if doc is None:
            raise ToolException(f"No document {doc_id!r}. Known IDs: {', '.join(sorted(self.docs))}.")
        return doc

    def tools(self) -> list[BaseTool]:
        def search_incident_docs(
            query: Annotated[str, Field(description=(
                "What to look for, in the words of the symptom or error, e.g. `orders rejected as stale "
                "but md_ws_connected is 1` or `sorry, too many clients already`. Exact metric names, "
                "log events and error strings match well."))],
            doc_type: Annotated[Optional[Literal["runbook", "postmortem"]], Field(description=(
                "runbook for triage and mitigation steps, postmortem for past incidents and their "
                "causes. Omit to search both."))] = None,
            k: Annotated[int, Field(ge=1, le=10, description="How many sections to return.")] = 5,
        ) -> str:
            return json.dumps(self.search(query, doc_type, k), indent=1)

        def get_incident_doc(
            doc_id: Annotated[str, Field(description="A document ID such as RB-005 or PM-003.")],
        ) -> str:
            doc = self.document(doc_id)
            return f"# {doc.title}\n\n{_frontmatter(doc)}\n\n{doc.body}"

        doc_list = ", ".join(sorted(self.docs))
        return [
            StructuredTool.from_function(
                search_incident_docs,
                description=(
                    "Searches this stack's runbooks and postmortems (hybrid keyword and semantic "
                    "search) and returns the best-matching sections with their document ID, title and "
                    "score. Use it to find how a symptom was diagnosed and fixed before, and which "
                    "metrics, logs and commands confirm it. A section is only part of its document; "
                    "read the whole document with get_incident_doc when you need the full procedure."
                ),
            ),
            StructuredTool.from_function(
                get_incident_doc,
                description=(
                    "Returns one runbook or postmortem in full: its metadata (services, components, "
                    f"alerts, symptoms, severity) and the whole text. Documents: {doc_list}."
                ),
                handle_tool_error=True,
            ),
        ]


def _frontmatter(doc: Document) -> str:
    keys = ("type", "services", "components", "alerts", "symptoms", "severity", "basis", "date")
    return "\n".join(f"- {key}: {doc.meta[key]}" for key in keys if key in doc.meta)


def open_knowledge_base(stack: ExitStack, backend: str, strategy: str, corpus: Path) -> KnowledgeBase:
    """A knowledge base on `backend`. The store connections stay open until `stack` closes.

    Store backends must already hold `strategy`'s chunks (`make -C rag ingest`); `memory` chunks and
    embeds the corpus here instead.
    """
    docs = load_corpus(corpus)
    embedder = load_embedder()
    backends = stack.enter_context(Backends())
    if backend.split("+")[0] == "memory":
        chunks = chunk_corpus(docs, strategy)
        retriever = backends.retriever(backend, strategy, chunks, embed_passages(embedder, chunks))
    else:
        retriever = backends.retriever(backend, strategy)
    return KnowledgeBase(retriever, embedder, docs)
