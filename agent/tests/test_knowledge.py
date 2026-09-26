import json

import pytest


def tools_by_name(knowledge):
    return {tool.name: tool for tool in knowledge.tools()}


def test_search_returns_sections_with_their_document(knowledge):
    search = tools_by_name(knowledge)["search_incident_docs"]
    results = json.loads(search.invoke({"query": "sorry, too many clients already", "k": 3}))
    assert len(results) == 3
    assert {"doc_id", "title", "section", "score", "content"} <= results[0].keys()
    assert any(r["doc_id"] in ("RB-005", "PM-004") for r in results)
    assert results[0]["title"].startswith(results[0]["doc_id"] + ": ")


@pytest.mark.parametrize("doc_type, prefix", [("runbook", "RB-"), ("postmortem", "PM-")])
def test_search_filters_by_document_type(knowledge, doc_type, prefix):
    search = tools_by_name(knowledge)["search_incident_docs"]
    results = json.loads(search.invoke({"query": "consumer lag", "doc_type": doc_type, "k": 5}))
    assert results and all(r["doc_id"].startswith(prefix) for r in results)


def test_search_rejects_an_unknown_document_type(knowledge):
    search = tools_by_name(knowledge)["search_incident_docs"]
    with pytest.raises(ValueError):
        search.invoke({"query": "lag", "doc_type": "wiki"})


def test_get_document_returns_metadata_and_the_whole_body(knowledge):
    get = tools_by_name(knowledge)["get_incident_doc"]
    text = get.invoke({"doc_id": " rb-005 "})
    assert text.startswith("# RB-005: PostgreSQL connection pool exhaustion")
    assert "- alerts: ['PostgresConnectionsHigh', 'DbPoolWaiting']" in text
    assert "## Triage" in text and "## Alert" in text


def test_get_unknown_document_is_an_error_the_model_can_act_on(knowledge):
    get = tools_by_name(knowledge)["get_incident_doc"]
    text = get.invoke({"doc_id": "RB-999"})
    assert "No document 'RB-999'" in text and "RB-001" in text


def test_tool_descriptions_list_the_document_ids(knowledge):
    description = tools_by_name(knowledge)["get_incident_doc"].description
    assert "RB-001" in description and "PM-008" in description
