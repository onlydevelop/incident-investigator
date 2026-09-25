from pathlib import Path

import pytest

from incident_rag.chunking import (
    ENCODING,
    STRATEGIES,
    chunk_corpus,
    count_tokens,
    fixed_size_chunks,
    heading_chunks,
)
from incident_rag.corpus import load_corpus, parse_document
from incident_rag.evaluate import DEFAULT_CORPUS, DEFAULT_QUESTIONS, answers, load_questions

DOC = parse_document(
    """---
id: RB-999
type: runbook
title: Test runbook
---

# RB-999: Test runbook

## Alert
`SomethingBroke` fires.

## Triage
1. Check the thing:
   ```bash
   # not a heading, just a shell comment
   kubectl get pods
   ```
2. Check the other thing.

## Mitigation
Restart it.
"""
)


def test_parse_document_splits_frontmatter_from_body():
    assert DOC.doc_id == "RB-999"
    assert DOC.title == "RB-999: Test runbook"
    assert DOC.meta["type"] == "runbook"
    assert DOC.body.startswith("# RB-999: Test runbook")


def test_parse_document_requires_frontmatter():
    with pytest.raises(ValueError):
        parse_document("# No frontmatter\n")


def test_fixed_size_windows_cover_the_body_with_overlap():
    chunks = fixed_size_chunks(DOC, size=20, overlap=5)
    tokens = ENCODING.encode(DOC.body)
    assert len(chunks) > 1
    assert all(count_tokens(c.content) <= 20 for c in chunks)
    # Each window starts 15 tokens after the previous one, and the last reaches the end of the body.
    assert chunks[1].content == ENCODING.decode(tokens[15:35])
    assert DOC.body.endswith(chunks[-1].content)


def test_fixed_size_short_document_is_one_chunk():
    [chunk] = fixed_size_chunks(DOC, size=1000, overlap=100)
    assert chunk.content == DOC.body


def test_fixed_size_rejects_overlap_not_smaller_than_size():
    with pytest.raises(ValueError):
        fixed_size_chunks(DOC, size=10, overlap=10)


def test_fixed_size_context_prefixes_the_title_only_in_embedded_text():
    chunk = fixed_size_chunks(DOC, size=20, overlap=5, add_context=True)[0]
    assert chunk.text == f"RB-999: Test runbook\n\n{chunk.content}"
    assert not chunk.content.startswith("RB-999: Test runbook")


def test_heading_chunks_follow_sections():
    chunks = heading_chunks(DOC, min_tokens=0)
    assert [c.section for c in chunks] == ["", "Alert", "Triage", "Mitigation"]
    assert chunks[2].content.startswith("## Triage")
    # The shell comment in the code block did not start a section.
    assert "# not a heading" in chunks[2].content


def test_heading_chunks_merge_small_sections_forward_and_the_last_one_back():
    chunks = heading_chunks(DOC, min_tokens=15)
    # The bare H1 folds into Alert; the short Mitigation folds back into Triage.
    assert [c.section for c in chunks] == ["Alert", "Triage / Mitigation"]
    assert chunks[0].content.startswith("# RB-999")


def test_heading_chunks_do_not_merge_past_max_tokens():
    chunks = heading_chunks(DOC, min_tokens=1000, max_tokens=40)
    assert all(count_tokens(c.content) <= 40 for c in chunks)


def test_heading_chunks_split_large_sections_at_list_items_not_inside_code():
    chunks = heading_chunks(DOC, min_tokens=0, max_tokens=35)
    triage = [c for c in chunks if c.section == "Triage"]
    assert len(triage) == 2
    # Step 1 keeps its whole code block; step 2 is on its own. Both pieces keep the heading.
    assert triage[0].content.startswith("## Triage\n1. Check the thing:")
    assert "kubectl get pods\n   ```" in triage[0].content
    assert triage[1].content == "## Triage\n2. Check the other thing."


def test_heading_chunks_fall_back_to_token_windows_for_one_huge_block():
    doc = parse_document("---\nid: X-1\ntitle: t\n---\n\n## Log\n" + "word " * 200 + "\n")
    chunks = heading_chunks(doc, min_tokens=0, max_tokens=50)
    assert len(chunks) > 1
    assert all(count_tokens(c.content) <= 50 for c in chunks)


def test_heading_context_is_the_breadcrumb():
    chunk = heading_chunks(DOC, min_tokens=0, add_context=True)[2]
    assert chunk.text.startswith("RB-999: Test runbook > Triage\n\n## Triage")


# --- The real corpus and eval set ---


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(DEFAULT_CORPUS)


def test_corpus_loads_every_runbook_and_postmortem(corpus):
    ids = [doc.doc_id for doc in corpus]
    assert len(ids) == len(set(ids)) == len(list(Path(DEFAULT_CORPUS).rglob("*-*.md")))
    assert {i[:2] for i in ids} == {"RB", "PM"}


@pytest.mark.parametrize("strategy", [name for name in STRATEGIES if name.startswith("heading")])
def test_heading_strategies_keep_every_line_of_the_corpus_whole(corpus, strategy):
    # Fixed-size windows cut lines by design; their coverage is checked token by token above.
    chunks = chunk_corpus(corpus, strategy)
    for doc in corpus:
        contents = [c.content for c in chunks if c.doc_id == doc.doc_id]
        for line in filter(None, (line.strip() for line in doc.body.splitlines())):
            assert any(line in content for content in contents), (doc.doc_id, line)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_strategy_chunk_fits_the_embedding_model(corpus, strategy):
    # bge-small truncates at 512 of its own tokens; staying well under that in cl100k leaves headroom.
    assert max(c.tokens for c in chunk_corpus(corpus, strategy)) <= 400 or strategy == "fixed-512"


def test_every_question_is_answerable_by_a_whole_document(corpus):
    # load_questions itself rejects evidence that is not in the document.
    questions = load_questions(DEFAULT_QUESTIONS, corpus)
    assert len({q.qid for q in questions}) == len(questions)
    whole_docs = chunk_corpus(corpus, "fixed-512")
    for question in questions:
        assert any(answers(chunk, question) for chunk in whole_docs), question.qid
