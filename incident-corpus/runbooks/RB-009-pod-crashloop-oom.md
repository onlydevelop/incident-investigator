---
id: RB-009
type: runbook
title: Pod CrashLoopBackOff / OOMKilled
services: [market-data-service, order-risk-service]
components: [kubernetes]
alerts: [PodCrashLooping, ContainerOOMKilled]
symptoms: [restarts, gaps in metrics, intermittent 503s]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-009: Pod CrashLoopBackOff / OOMKilled

## Alert
- `PodCrashLooping`: `increase(kube_pod_container_status_restarts_total{namespace="trading"}[15m]) > 3`.
- `ContainerOOMKilled`: `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1`.

## Triage
1. Why did it exit?
   ```bash
   kubectl -n trading get pods
   kubectl -n trading describe pod <pod> | grep -A5 "Last State"
   kubectl -n trading logs <pod> --previous | tail -50
   ```
   - `OOMKilled` (exit 137): memory limit hit.
   - Exit 1 with a Python traceback: startup error (bad config, missing env var, dependency unreachable).
   - Killed by liveness probe: service alive but slow to respond; see step 4.
2. For OOM, look at the memory curve before the kill:
   ```promql
   container_memory_working_set_bytes{namespace="trading",container=~"market-data-service|order-risk-service"}
   ```
   - Steady climb over hours: leak. Common Python causes are unbounded dicts (per-symbol caches, in-memory order history) and growing asyncio task sets.
   - Sudden jump: a burst (volatility spike, large replay batch).
3. Market-data restarts cause stale prices (RB-001); order-risk restarts cause 503s and a consumer rebalance (RB-002).
4. Liveness failures under load: a blocking call in the event loop stops the probe handler. Check for sync DB or HTTP calls inside async handlers.

## Mitigation
- Raise the memory limit to stop the loop, then fix the leak.
- Startup errors: roll back the release or config (RB-010).
- Make liveness probes cheap and independent of downstream dependencies.

## Verify
No restarts for 30 min and memory flat.
