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
    "remediation": [],
}
RESTART = {"kind": "restart_deployment", "deployment": "delta-ticker", "replicas": None, "reason": "RB-001."}


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
    ({"confidence": "low", "remediation": [RESTART]}, "remediation must be empty when confidence is low"),
    ({"remediation": [{**RESTART, "replicas": 1}]}, "replicas must be null"),
    ({"remediation": [{**RESTART, "kind": "scale_deployment"}]}, "needs replicas between 0 and 3"),
    ({"remediation": [{**RESTART, "kind": "scale_deployment", "replicas": 4, "deployment": "orders-api"}]}, "between 0 and 3"),
    ({"remediation": [{**RESTART, "kind": "scale_deployment", "replicas": 2}]}, "delta-ticker must not run more than 1"),
    ({"remediation": [{**RESTART, "deployment": "postgres"}]}, "remediation.0.deployment"),
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


def test_markdown_lists_proposed_remediation():
    scale = {"kind": "scale_deployment", "deployment": "position-updater", "replicas": 0, "reason": "Stop it."}
    text = InvestigationReport.model_validate({**VALID, "remediation": [RESTART, scale]}).markdown()
    assert text.endswith(
        "## Proposed remediation\n\n1. restart deployment/delta-ticker: RB-001.\n"
        "2. scale deployment/position-updater to 0 replicas: Stop it.\n"
    )
