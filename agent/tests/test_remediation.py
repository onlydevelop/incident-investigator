import shutil
import subprocess
from datetime import datetime, timezone

import pytest

from incident_agent import config
from incident_agent.remediation import (
    KEY_ANNOTATION,
    RESTART_ANNOTATION,
    Backoff,
    ClusterError,
    KubectlCluster,
    Proposal,
    TransientClusterError,
    apply,
    classify,
    idempotency_key,
    patch_for,
    proposals_for,
)
from incident_agent.report import RemediationAction

from fakes import LOST_RESPONSE, FakeCluster

RESTART = RemediationAction(kind="restart_deployment", deployment="orders-api", replicas=None, reason="RB-005.")
SCALE = RemediationAction(kind="scale_deployment", deployment="position-updater", replicas=2, reason="RB-002.")
CONFLICT = TransientClusterError('Operation cannot be fulfilled on deployments.apps "orders-api": '
                                 "the object has been modified; please apply your changes to the latest version")


def proposal(action: RemediationAction = RESTART, run_id: str = "run-1") -> Proposal:
    return Proposal(key=idempotency_key(run_id, action), action=action)


class Sleeps(list):
    async def __call__(self, delay: float) -> None:
        self.append(delay)


# --- Keys and patches ---


def test_key_depends_on_the_run_and_the_action_but_not_the_wording():
    key = idempotency_key("run-1", RESTART)
    assert len(key) == 16
    assert key == idempotency_key("run-1", RESTART.model_copy(update={"reason": "Different words."}))
    assert key != idempotency_key("run-2", RESTART)
    assert key != idempotency_key("run-1", RESTART.model_copy(update={"deployment": "symbols-api"}))
    assert idempotency_key("run-1", SCALE) != idempotency_key("run-1", SCALE.model_copy(update={"replicas": 3}))


def test_a_repeated_action_is_proposed_once():
    proposals = proposals_for("run-1", [RESTART, SCALE, RESTART.model_copy(update={"reason": "again"})])
    assert [p.action.kind for p in proposals] == ["restart_deployment", "scale_deployment"]


def test_restart_patch_puts_the_key_in_the_pod_template():
    p = proposal()
    patch = patch_for(p, "dipanjan", datetime(2026, 9, 26, 2, 30, tzinfo=timezone.utc))
    assert patch == {
        "metadata": {"annotations": {KEY_ANNOTATION: p.key, "incident-agent/approved-by": "dipanjan",
                                     "incident-agent/applied-at": "2026-09-26T02:30:00Z"}},
        "spec": {"template": {"metadata": {"annotations": {RESTART_ANNOTATION: p.key}}}},
    }


def test_scale_patch_sets_the_replica_count():
    patch = patch_for(proposal(SCALE), "dipanjan", datetime.now(timezone.utc))
    assert patch["spec"] == {"replicas": 2}


# --- Applying, retrying, idempotency ---


async def test_applies_once():
    cluster = FakeCluster()
    result = await apply(cluster, proposal(), "dipanjan")
    assert (result.status, result.attempts, result.detail) == ("applied", 1, None)
    assert cluster.rollouts == ["orders-api"]


async def test_retries_a_transient_failure_with_the_same_key():
    cluster, sleeps = FakeCluster(failures=[CONFLICT, CONFLICT]), Sleeps()
    result = await apply(cluster, proposal(), "dipanjan", Backoff(initial_delay=1, factor=2), sleeps)
    assert (result.status, result.attempts) == ("applied", 3)
    assert sleeps == [1, 2]
    keys = {patch["metadata"]["annotations"][KEY_ANNOTATION] for _, patch in cluster.patches}
    assert keys == {proposal().key}
    assert cluster.rollouts == ["orders-api"]


async def test_a_patch_whose_response_was_lost_is_not_applied_twice():
    cluster = FakeCluster(failures=[LOST_RESPONSE])
    result = await apply(cluster, proposal(), "dipanjan", Backoff(initial_delay=0))
    # The second attempt finds the key already recorded, so it doesn't patch again.
    assert (result.status, result.attempts) == ("already_applied", 2)
    assert len(cluster.patches) == 1 and cluster.rollouts == ["orders-api"]


async def test_replaying_an_applied_action_is_a_no_op():
    cluster = FakeCluster()
    await apply(cluster, proposal(), "dipanjan")
    again = await apply(cluster, proposal(), "dipanjan")
    assert again.status == "already_applied" and len(cluster.patches) == 1


async def test_even_a_blind_repeat_of_the_restart_patch_starts_no_second_rollout():
    """The server-side half: re-sending the same patch leaves the pod template as it was."""
    cluster = FakeCluster()
    patch = patch_for(proposal(), "dipanjan", datetime.now(timezone.utc))
    await cluster.patch("orders-api", patch)
    await cluster.patch("orders-api", patch)
    assert cluster.rollouts == ["orders-api"]


async def test_gives_up_after_the_last_attempt():
    cluster, sleeps = FakeCluster(failures=[CONFLICT] * 4), Sleeps()
    result = await apply(cluster, proposal(), "dipanjan", Backoff(attempts=4, initial_delay=0.5), sleeps)
    assert (result.status, result.attempts) == ("failed", 4)
    assert "the object has been modified" in result.detail
    assert sleeps == [0.5, 1.0, 2.0] and cluster.rollouts == []


async def test_does_not_retry_a_permanent_failure():
    cluster = FakeCluster(failures=[ClusterError('deployments.apps "orders-api" is forbidden')])
    result = await apply(cluster, proposal(), "dipanjan", Backoff(initial_delay=0))
    assert (result.status, result.attempts) == ("failed", 1) and "forbidden" in result.detail


def test_classifies_kubectl_errors():
    assert isinstance(classify("The connection to the server 127.0.0.1:6443 was refused - did you specify "
                               "the right host or port?\nconnection refused"), TransientClusterError)
    assert isinstance(classify("Unable to connect to the server: net/http: TLS handshake timeout"),
                      TransientClusterError)
    assert isinstance(classify(CONFLICT.args[0]), TransientClusterError)
    not_found = classify('Error from server (NotFound): deployments.apps "nope" not found')
    assert type(not_found) is ClusterError
    assert str(classify("")) == "kubectl failed without a message"


# --- Against the real API server, in dry-run mode only ---


def _cluster_reachable() -> bool:
    if not shutil.which("kubectl"):
        return False
    probe = subprocess.run(["kubectl", "-n", config.NAMESPACE, "get", "deployment", "orders-api"],
                           capture_output=True, timeout=15)
    return probe.returncode == 0


@pytest.mark.k8s
async def test_dry_run_against_the_api_server_changes_nothing():
    if not _cluster_reachable():
        pytest.skip("no reachable cluster with deployment/orders-api (make k8s-start)")
    cluster = KubectlCluster(config.NAMESPACE, config.KUBE_CONTEXT, dry_run=True)
    before = await cluster.applied_key("orders-api")
    result = await apply(cluster, proposal(run_id="dry-run-test"), "pytest")
    assert (result.status, result.dry_run) == ("applied", True)
    assert await cluster.applied_key("orders-api") == before

    missing = await apply(KubectlCluster(config.NAMESPACE, config.KUBE_CONTEXT, dry_run=True),
                          proposal(RESTART.model_construct(**{**RESTART.model_dump(), "deployment": "nope"})),
                          "pytest")
    assert missing.status == "failed" and missing.attempts == 1 and "NotFound" in missing.detail
