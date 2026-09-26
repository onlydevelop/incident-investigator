import pytest
from pydantic import ValidationError

from incident_agent.report import InvestigationReport

VALID = {
    "root_cause": "  delta-ticker lost Redis at 01:22 UTC and resubscribed only at 01:31.  ",
    "confidence": "medium",
    "evidence": [
        {"finding": "Two symbols_read_failed lines at 01:22.", "source": "loki",
         "reference": '{service_name="delta-ticker"} | event="symbols_read_failed"'},
        {"finding": "Startup ordering matches PM-005.", "source": "postmortem", "reference": "PM-005 > Timeline"},
    ],
    "next_step": "Check ticker:symbols with SMEMBERS (RB-006).",
}


def test_accepts_a_valid_report_and_strips_the_text_fields():
    report = InvestigationReport.model_validate(VALID)
    assert report.root_cause.startswith("delta-ticker") and report.root_cause.endswith("01:31.")
    assert [e.source for e in report.evidence] == ["loki", "postmortem"]


@pytest.mark.parametrize("change, field", [
    ({"confidence": "certain"}, "confidence"),
    ({"evidence": []}, "evidence"),
    ({"root_cause": "   "}, "root_cause"),
    ({"next_step": ""}, "next_step"),
    ({"evidence": [{"finding": "x", "source": "grafana", "reference": "y"}]}, "evidence.0.source"),
])
def test_rejects(change, field):
    with pytest.raises(ValidationError) as error:
        InvestigationReport.model_validate({**VALID, **change})
    assert field in str(error.value)


def test_markdown_rendering():
    text = InvestigationReport.model_validate(VALID).markdown()
    assert text.startswith("## Root cause (medium confidence)\n\ndelta-ticker")
    assert "- **loki**: Two symbols_read_failed lines at 01:22.  \n  `{service_name=" in text
    assert text.endswith("## Next step\n\nCheck ticker:symbols with SMEMBERS (RB-006).\n")
