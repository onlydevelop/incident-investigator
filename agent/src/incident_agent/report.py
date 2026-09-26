"""The investigation's result, as a Pydantic model.

The model is sent to the API as the response's JSON schema (`output_config.format`), so the agent's
final reply is constrained to it, and it is validated again here on the way out. The field
descriptions are part of the schema the model sees, so they carry the instructions for each field.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Evidence(BaseModel):
    finding: str = Field(description="What the telemetry or document shows, in one sentence, with the numbers and UTC times.")
    source: Literal["prometheus", "loki", "tempo", "runbook", "postmortem"] = Field(
        description="Where the finding comes from."
    )
    reference: str = Field(description=(
        "How to see it again: the PromQL, LogQL or TraceQL query, the trace ID, or the document ID "
        "and section (e.g. `RB-005 > Triage`)."
    ))


class InvestigationReport(BaseModel):
    root_cause: str = Field(description=(
        "The most likely cause, in two to four sentences: what broke, since when (UTC), and the impact. "
        "Name the alternatives you ruled out and why. If the evidence doesn't single one out, say so."
    ))
    confidence: Literal["high", "medium", "low"] = Field(description=(
        "high: the telemetry directly shows the cause. medium: the evidence fits it, but an alternative "
        "is not excluded. low: a hypothesis the telemetry barely supports, or the tools could not reach it."
    ))
    evidence: list[Evidence] = Field(
        min_length=1,
        description="The findings that support the root cause, strongest first. Telemetry, not just documents.",
    )
    next_step: str = Field(description=(
        "The single most useful thing for the on-call engineer to do now: a mitigation, or the check that "
        "would confirm the cause. Include the command or query, and the runbook it comes from, if any."
    ))

    @field_validator("root_cause", "next_step")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    def markdown(self) -> str:
        evidence = "\n".join(f"- **{e.source}**: {e.finding}  \n  `{e.reference}`" for e in self.evidence)
        return (
            f"## Root cause ({self.confidence} confidence)\n\n{self.root_cause}\n\n"
            f"## Evidence\n\n{evidence}\n\n"
            f"## Next step\n\n{self.next_step}\n"
        )
