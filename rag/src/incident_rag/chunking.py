"""The two chunking strategies under comparison.

Both work on a document's body (frontmatter stripped) and can prepend a context line to the text that
gets embedded. The chunk's `content` is the document text it covers (a heading chunk split into pieces
repeats the section's heading line), so the evaluation judges what a chunk contains, not the added
context line.
"""

import re
from dataclasses import dataclass
from functools import partial

import tiktoken

from incident_rag.corpus import Document

# Chunk sizes are counted in cl100k tokens, the unit most LLM context budgets use.
ENCODING = tiktoken.get_encoding("cl100k_base")

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
# A numbered or bulleted item at column 0; nested items and code under it stay with it.
TOP_LEVEL_ITEM = re.compile(r"^(\d+\.|[-*])\s")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    # The document text this chunk covers.
    content: str
    # What gets embedded: `content`, preceded by a context line when the strategy adds one.
    text: str
    # Heading path below the document title, e.g. "Triage"; "" for fixed-size chunks.
    section: str

    @property
    def tokens(self) -> int:
        return count_tokens(self.text)


def count_tokens(text: str) -> int:
    return len(ENCODING.encode(text))


def _embedded_text(context: str, content: str, add_context: bool) -> str:
    return f"{context}\n\n{content}" if add_context else content


# --- Fixed-size ---


def fixed_size_chunks(doc: Document, size: int, overlap: int, add_context: bool = False) -> list[Chunk]:
    """Windows of `size` tokens, each starting `size - overlap` tokens after the previous one.

    Boundaries ignore the markdown entirely, so a window can end mid-step or mid-code-block. With
    `add_context`, each chunk is prefixed with the document title (fixed-size chunks have no section).
    """
    if not 0 <= overlap < size:
        raise ValueError("overlap must be at least 0 and smaller than size")
    tokens = ENCODING.encode(doc.body)
    starts = range(0, max(len(tokens) - overlap, 1), size - overlap)
    chunks = []
    for i, start in enumerate(starts):
        content = ENCODING.decode(tokens[start : start + size])
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}#f{i}",
                doc_id=doc.doc_id,
                content=content,
                text=_embedded_text(doc.title, content, add_context),
                section="",
            )
        )
    return chunks


# --- Heading-aware ---


@dataclass(frozen=True)
class _Section:
    # Heading titles below the H1, e.g. ("Triage",); several when small sections were merged.
    labels: tuple[str, ...]
    text: str


def _split_sections(body: str) -> list[_Section]:
    """One section per heading, each starting with its heading line. `#` lines inside fenced code
    (shell comments, for example) are not headings."""
    sections: list[tuple[tuple[str, ...], list[str]]] = [((), [])]
    path: list[str] = []
    in_fence = False
    for line in body.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        heading = None if in_fence else HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            path = path[: level - 1] + [heading.group(2)]
            sections.append((tuple(path), []))
        sections[-1][1].append(line)

    result = []
    for heading_path, lines in sections:
        text = "".join(lines).strip()
        if text:
            # The H1 is the document title, which the context line already carries.
            label = " > ".join(heading_path[1:])
            result.append(_Section((label,) if label else (), text))
    return result


def _merge_small(sections: list[_Section], min_tokens: int, max_tokens: int) -> list[_Section]:
    """Fold sections under `min_tokens` (a bare H1, a one-line "Alert") into the next section, or into
    the previous one for the last section, as long as the result stays within `max_tokens`."""

    def join(a: _Section, b: _Section) -> _Section:
        return _Section(a.labels + b.labels, f"{a.text}\n\n{b.text}")

    def fits(a: _Section, b: _Section) -> bool:
        return count_tokens(join(a, b).text) <= max_tokens

    merged: list[_Section] = []
    for section in sections:
        if merged and count_tokens(merged[-1].text) < min_tokens and fits(merged[-1], section):
            section = join(merged.pop(), section)
        merged.append(section)
    if len(merged) > 1 and count_tokens(merged[-1].text) < min_tokens and fits(merged[-2], merged[-1]):
        last = merged.pop()
        merged.append(join(merged.pop(), last))
    return merged


def _blocks(text: str) -> list[tuple[int, int]]:
    """(start, end) offsets of the paragraphs and top-level list items in `text`. A fenced code block
    stays in the block it appears in, blank lines and all."""
    bounds = []
    start = None
    pos = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not in_fence and start is not None and (not stripped or TOP_LEVEL_ITEM.match(line)):
            bounds.append((start, pos))
            start = None
        if stripped and start is None:
            start = pos
        if stripped.startswith("```"):
            in_fence = not in_fence
        pos += len(line)
    if start is not None:
        bounds.append((start, pos))
    return bounds


def _split_large(text: str, max_tokens: int) -> list[str]:
    """Pack whole paragraphs and list items into pieces of at most `max_tokens`. A single block larger
    than that (a long log excerpt, say) is cut into token windows as a last resort."""
    pieces = []
    start = end = None
    for block_start, block_end in _blocks(text):
        if start is not None and count_tokens(text[start:block_end]) > max_tokens:
            pieces.append(text[start:end].strip())
            start = None
        if start is None:
            start = block_start
        end = block_end
    if start is not None:
        pieces.append(text[start:end].strip())

    result = []
    for piece in pieces:
        tokens = ENCODING.encode(piece)
        if len(tokens) <= max_tokens:
            result.append(piece)
        else:
            result.extend(ENCODING.decode(tokens[i : i + max_tokens]) for i in range(0, len(tokens), max_tokens))
    return result


def heading_chunks(doc: Document, min_tokens: int = 40, max_tokens: int = 300, add_context: bool = False) -> list[Chunk]:
    """One chunk per markdown section, with small sections merged into a neighbour and large ones split
    at paragraph or list-item boundaries. With `add_context`, each chunk is prefixed with its
    breadcrumb, e.g. "RB-001: Stale market data / WebSocket feed down > Triage"."""
    sections = _merge_small(_split_sections(doc.body), min_tokens, max_tokens)
    chunks = []
    for section in sections:
        label = " / ".join(section.labels)
        breadcrumb = f"{doc.title} > {label}" if label else doc.title
        # Split the body without its heading line and repeat the heading on every piece, so no piece is
        # a bare heading and continuation pieces still say which section they belong to.
        first_line, _, rest = section.text.partition("\n")
        heading, body = (first_line, rest.strip()) if HEADING.match(first_line) else ("", section.text)
        pieces = _split_large(body, max_tokens - count_tokens(f"{heading}\n")) if body else [""]
        for piece in pieces:
            content = f"{heading}\n{piece}".strip() if heading else piece
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#h{len(chunks)}",
                    doc_id=doc.doc_id,
                    content=content,
                    text=_embedded_text(breadcrumb, content, add_context),
                    section=label,
                )
            )
    return chunks


# Strategies the evaluation compares. "+ctx" variants prepend the context line: the document title for
# fixed-size chunks, the full breadcrumb for heading chunks. fixed-512 holds a whole document in one
# chunk here (documents are 300 to 500 tokens), so it is the "don't chunk" reference point.
# heading-merged keeps section boundaries but packs neighbouring sections to at least 150 tokens, which
# puts its chunk sizes near fixed-256's.
STRATEGIES = {
    "fixed-128": partial(fixed_size_chunks, size=128, overlap=16),
    "fixed-128+ctx": partial(fixed_size_chunks, size=128, overlap=16, add_context=True),
    "fixed-256": partial(fixed_size_chunks, size=256, overlap=32),
    "fixed-256+ctx": partial(fixed_size_chunks, size=256, overlap=32, add_context=True),
    "fixed-512": partial(fixed_size_chunks, size=512, overlap=64),
    "heading": heading_chunks,
    "heading+ctx": partial(heading_chunks, add_context=True),
    "heading-merged+ctx": partial(heading_chunks, min_tokens=150, add_context=True),
}


def chunk_corpus(docs: list[Document], strategy: str) -> list[Chunk]:
    chunker = STRATEGIES[strategy]
    return [chunk for doc in docs for chunk in chunker(doc)]
