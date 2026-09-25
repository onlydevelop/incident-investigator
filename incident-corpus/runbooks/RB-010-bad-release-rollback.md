---
id: RB-010
type: runbook
title: "Bad release: detect and roll back"
services: [market-data-service, order-risk-service]
components: [kubernetes, config]
alerts: []
symptoms: [error or reject rate change right after deploy]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-010: Bad release: detect and roll back

## When to use
Any incident whose start time is within 30 minutes of a deploy or ConfigMap change. Roll back first; investigate after. A rollback is cheap and reversible; debugging a live bad release is not.

## Detect
1. Find recent changes:
   ```bash
   kubectl -n trading rollout history deploy/order-risk-service
   kubectl -n trading get events --sort-by=.lastTimestamp | grep -i -E "scaled|pulled|configmap"
   ```
2. Compare metrics before and after the change: reject rate by reason, p99 latency, error logs. Grafana deploy annotations mark each rollout.
3. Compare by version: every span and metric carries `service.version`. If only pods on the new version misbehave during a rolling update, the release is the cause.
   ```promql
   sum by (service_version, reason) (rate(order_rejects_total[5m]))
   ```

## Roll back
- Code release:
  ```bash
  kubectl -n trading rollout undo deploy/order-risk-service
  kubectl -n trading rollout status deploy/order-risk-service
  ```
- Config change (ConfigMaps are not versioned by rollouts): re-apply the previous version from Git, then restart pods so they reload it.
  ```bash
  git log -p -- k8s/configmaps/risk-limits.yaml
  kubectl apply -f k8s/configmaps/risk-limits.yaml && kubectl -n trading rollout restart deploy/order-risk-service
  ```

## Verify
Metrics return to pre-release baseline. Record the bad version in the incident notes and block it from redeploying.
