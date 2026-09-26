"""The investigation's result, as a Pydantic model.

The model is sent to the API as the response's JSON schema (`output_config.format`), so the agent's
final reply is constrained to it, and it is validated again here on the way out. The field
descriptions are part of the schema the model sees, so they carry the instructions for each field.
"""

from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

# The app deployments remediation may touch. The StatefulSets (postgres, kafka) and the exporters are
# deliberately not here: restarting a database is not a step to take from an automated proposal.
Deployment = Literal["delta-ticker", "symbols-api", "orders-api", "position-updater"]
DEPLOYMENTS = get_args(Deployment)
MAX_REPLICAS = 3


class Evidence(BaseModel):
    finding: str = Field(description="What the telemetry or document shows, in one sentence, with the numbers and UTC times.")
    source: Literal["prometheus", "loki", "tempo", "runbook", "postmortem"] = Field(
        description="Where the finding comes from."
    )
    reference: str = Field(description=(
        "How to see it again: the PromQL, LogQL or TraceQL query, the trace ID, or the document ID "
        "and section (e.g. `RB-005 > Triage`)."
    ))


class RemediationAction(BaseModel):
    kind: Literal["restart_deployment", "scale_deployment"] = Field(description=(
        "restart_deployment: a rolling restart, as `kubectl rollout restart` does. "
        "scale_deployment: set the replica count."
    ))
    deployment: Deployment = Field(description="The deployment in namespace incident-investigator.")
    replicas: Optional[int] = Field(description=(
        f"scale_deployment only: the new replica count, 0 to {MAX_REPLICAS}. delta-ticker runs at most 1. "
        "null for restart_deployment."
    ))
    reason: str = Field(description="Why this mitigates the root cause, and the runbook that recommends it.")

    @model_validator(mode="after")
    def check_replicas(self) -> "RemediationAction":
        if self.kind == "restart_deployment" and self.replicas is not None:
            raise ValueError("replicas must be null for restart_deployment")
        if self.kind == "scale_deployment":
            if self.replicas is None or not 0 <= self.replicas <= MAX_REPLICAS:
                raise ValueError(f"scale_deployment needs replicas between 0 and {MAX_REPLICAS}")
            # One replica, Recreate strategy: two tickers would publish every tick twice.
            if self.deployment == "delta-ticker" and self.replicas > 1:
                raise ValueError("delta-ticker must not run more than 1 replica")
        return self

    def describe(self) -> str:
        if self.kind == "restart_deployment":
            return f"restart deployment/{self.deployment}"
        return f"scale deployment/{self.deployment} to {self.replicas} replicas"


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
    remediation: list[RemediationAction] = Field(description=(
        "Changes to the cluster you propose to mitigate the incident, in the order to apply them. A human "
        "approves or rejects each one before anything runs; you never run them yourself. Propose only what "
        "a runbook recommends for a cause the telemetry confirms, and nothing if the incident is already "
        "over. Must be empty when confidence is low."
    ))

    @field_validator("root_cause", "next_step")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def no_remediation_on_a_hunch(self) -> "InvestigationReport":
        if self.confidence == "low" and self.remediation:
            raise ValueError("remediation must be empty when confidence is low")
        return self

    def markdown(self) -> str:
        evidence = "\n".join(f"- **{e.source}**: {e.finding}  \n  `{e.reference}`" for e in self.evidence)
        text = (
            f"## Root cause ({self.confidence} confidence)\n\n{self.root_cause}\n\n"
            f"## Evidence\n\n{evidence}\n\n"
            f"## Next step\n\n{self.next_step}\n"
        )
        if self.remediation:
            actions = "\n".join(f"{i}. {a.describe()}: {a.reason}" for i, a in enumerate(self.remediation, 1))
            text += f"\n## Proposed remediation\n\n{actions}\n"
        return text
