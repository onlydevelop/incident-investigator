import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Every corpus document starts with a YAML block between two `---` lines.
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class Document:
    doc_id: str
    # "RB-001: Stale market data / WebSocket feed down", used as the chunks' context line.
    title: str
    meta: dict
    # The markdown after the frontmatter; this is what gets chunked.
    body: str


def parse_document(text: str) -> Document:
    match = FRONTMATTER.match(text)
    if not match:
        raise ValueError("document has no YAML frontmatter")
    meta = yaml.safe_load(match.group(1))
    return Document(
        doc_id=meta["id"],
        title=f"{meta['id']}: {meta['title']}",
        meta=meta,
        body=text[match.end() :].strip() + "\n",
    )


def load_corpus(root: Path) -> list[Document]:
    """Every runbook and postmortem under `root`, sorted by path. The corpus README is not a document."""
    paths = sorted(p for p in root.rglob("*.md") if p.name != "README.md")
    return [parse_document(p.read_text()) for p in paths]
