from typing import Optional

from incident_agent.remediation import KEY_ANNOTATION, TransientClusterError

# A patch the server applied, but whose response never reached the client.
LOST_RESPONSE = "lost response"


class FakeCluster:
    """Deployments as dicts. `failures` are raised by successive `patch` calls, in order; LOST_RESPONSE
    applies the patch and then raises a timeout, the case idempotency keys exist for."""

    def __init__(self, failures: Optional[list] = None, dry_run: bool = False):
        self.dry_run = dry_run
        self.failures = list(failures or [])
        self.deployments: dict[str, dict] = {}
        self.patches: list[tuple[str, dict]] = []
        # Rollouts started: a restart whose template annotation actually changed.
        self.rollouts: list[str] = []

    async def applied_key(self, deployment: str) -> Optional[str]:
        return self.deployments.get(deployment, {}).get("annotations", {}).get(KEY_ANNOTATION)

    async def patch(self, deployment: str, patch: dict) -> None:
        self.patches.append((deployment, patch))
        failure = self.failures.pop(0) if self.failures else None
        if isinstance(failure, Exception):
            raise failure
        self._apply(deployment, patch)
        if failure == LOST_RESPONSE:
            raise TransientClusterError("kubectl timed out after 30s")

    def _apply(self, deployment: str, patch: dict) -> None:
        current = self.deployments.setdefault(deployment, {"annotations": {}, "template": {}, "replicas": 1})
        current["annotations"].update(patch["metadata"]["annotations"])
        spec = patch["spec"]
        if "replicas" in spec:
            current["replicas"] = spec["replicas"]
        template = spec.get("template", {}).get("metadata", {}).get("annotations", {})
        if template and template != current["template"]:
            current["template"] = dict(template)
            self.rollouts.append(deployment)
