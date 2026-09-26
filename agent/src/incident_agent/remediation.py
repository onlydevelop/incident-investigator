"""Applying approved remediation to the cluster, safely retried.

Every action gets an idempotency key: a hash of the run ID and the action (kind, deployment,
replicas; not the reason's wording). Applying an action is one merge patch on the deployment that
records the key, so applying it again is a no-op:

- A restart writes the key into the pod template's annotations, the way `kubectl rollout restart`
  writes a timestamp. A new key changes the template and starts a rollout; the same key again leaves
  the template unchanged, so Kubernetes starts nothing.
- A scale sets an absolute replica count, which is the same the second time.
- Both record the key and the approver in the deployment's own annotations, so a retry can tell the
  action already landed.

Transient failures (API server unreachable, timeouts, write conflicts) are retried with backoff and
the same key. Before each attempt the deployment's recorded key is checked, so an attempt whose
response was lost but whose patch was applied is reported as already applied, not applied twice.
The same holds when a run is resumed and the remediation step replays. A new run gets new keys, so
a later incident can restart the same deployment again.
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, Field, field_validator

from incident_agent.report import RemediationAction

KEY_ANNOTATION = "incident-agent/remediation-key"
APPROVER_ANNOTATION = "incident-agent/approved-by"
APPLIED_AT_ANNOTATION = "incident-agent/applied-at"
RESTART_ANNOTATION = "incident-agent/restart-key"


class Proposal(BaseModel):
    key: str
    action: RemediationAction


class ApprovalDecision(BaseModel):
    """What the human answered: who they are, and the keys of the proposals they approved."""

    approver: str
    approved: list[str] = Field(default_factory=list)

    @field_validator("approver")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approver must not be empty")
        return value.strip()


class RemediationResult(BaseModel):
    key: str
    action: RemediationAction
    status: Literal["applied", "already_applied", "rejected", "failed"]
    attempts: int = 0
    dry_run: bool = False
    detail: Optional[str] = None


def idempotency_key(run_id: str, action: RemediationAction) -> str:
    identity = {"run": run_id, "kind": action.kind, "deployment": action.deployment, "replicas": action.replicas}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]


def proposals_for(run_id: str, actions: list[RemediationAction]) -> list[Proposal]:
    """One proposal per distinct action, in order; a repeated action would get the same key anyway."""
    proposals: dict[str, Proposal] = {}
    for action in actions:
        key = idempotency_key(run_id, action)
        proposals.setdefault(key, Proposal(key=key, action=action))
    return list(proposals.values())


def patch_for(proposal: Proposal, approver: str, now: datetime) -> dict:
    action = proposal.action
    patch: dict = {"metadata": {"annotations": {
        KEY_ANNOTATION: proposal.key,
        APPROVER_ANNOTATION: approver,
        APPLIED_AT_ANNOTATION: now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }}}
    if action.kind == "restart_deployment":
        patch["spec"] = {"template": {"metadata": {"annotations": {RESTART_ANNOTATION: proposal.key}}}}
    else:
        patch["spec"] = {"replicas": action.replicas}
    return patch


class ClusterError(Exception):
    """A failure retrying won't fix: the deployment doesn't exist, access is denied, the patch is invalid."""


class TransientClusterError(ClusterError):
    """A failure worth retrying with the same key."""


class Cluster(Protocol):
    dry_run: bool

    async def applied_key(self, deployment: str) -> Optional[str]: ...
    async def patch(self, deployment: str, patch: dict) -> None: ...


@dataclass(frozen=True)
class Backoff:
    attempts: int = 4
    initial_delay: float = 1.0
    factor: float = 2.0

    def delays(self):
        delay = self.initial_delay
        for _ in range(self.attempts - 1):
            yield delay
            delay *= self.factor


DEFAULT_BACKOFF = Backoff()


async def apply(
    cluster: Cluster,
    proposal: Proposal,
    approver: str,
    backoff: Backoff = DEFAULT_BACKOFF,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> RemediationResult:
    result = RemediationResult(key=proposal.key, action=proposal.action, status="failed", dry_run=cluster.dry_run)
    delays = backoff.delays()
    while True:
        result.attempts += 1
        try:
            if await cluster.applied_key(proposal.action.deployment) == proposal.key:
                result.status = "already_applied"
                return result
            await cluster.patch(proposal.action.deployment, patch_for(proposal, approver, datetime.now(timezone.utc)))
            result.status, result.detail = "applied", None
            return result
        except TransientClusterError as e:
            result.detail = str(e)
            delay = next(delays, None)
            if delay is None:
                return result
            await sleep(delay)
        except ClusterError as e:
            result.detail = str(e)
            return result


# kubectl's messages for failures that go away by themselves.
_TRANSIENT = (
    "connection refused", "connection reset", "i/o timeout", "tls handshake timeout", "timed out",
    "unexpected eof", "the object has been modified", "serviceunavailable", "service unavailable",
    "too many requests", "etcdserver", "internal error occurred", "unable to connect to the server",
)


def classify(stderr: str) -> ClusterError:
    message = stderr.strip() or "kubectl failed without a message"
    lowered = message.lower()
    return TransientClusterError(message) if any(m in lowered for m in _TRANSIENT) else ClusterError(message)


class KubectlCluster:
    """The cluster through kubectl, in one namespace. With `dry_run`, patches are validated by the API
    server (`--dry-run=server`) but not persisted."""

    def __init__(self, namespace: str, context: Optional[str] = None, dry_run: bool = False, timeout: float = 30):
        self.namespace = namespace
        self.context = context
        self.dry_run = dry_run
        self.timeout = timeout

    async def _kubectl(self, *args: str) -> str:
        command = ["kubectl", *(["--context", self.context] if self.context else []), "-n", self.namespace, *args]
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise TransientClusterError(f"kubectl timed out after {self.timeout:g}s") from None
        if process.returncode != 0:
            raise classify(stderr.decode())
        return stdout.decode()

    async def applied_key(self, deployment: str) -> Optional[str]:
        data = json.loads(await self._kubectl("get", "deployment", deployment, "-o", "json"))
        return (data["metadata"].get("annotations") or {}).get(KEY_ANNOTATION)

    async def patch(self, deployment: str, patch: dict) -> None:
        dry_run = ["--dry-run=server"] if self.dry_run else []
        await self._kubectl("patch", "deployment", deployment, "--type=merge", "-p", json.dumps(patch), *dry_run)
