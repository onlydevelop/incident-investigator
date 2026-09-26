---
id: RB-010
type: runbook
title: "Bad release: detect and roll back"
services: [market-data-service, order-service]
components: [kubernetes, kustomize, docker-images]
alerts: []
symptoms: [error rate or tick rate change right after make deploy]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-010: Bad release: detect and roll back

## When to use
Any incident whose start time is within 30 minutes of `make -C deploy/k8s deploy` or a change to `deploy/k8s`. Roll back first; investigate after.

## Why `rollout undo` is not enough
Every app image is tagged `:local` with `imagePullPolicy: Never`, and `make deploy` rebuilds the same tag. `kubectl rollout undo` only restores the previous pod template, which names the same `:local` tag, and that tag now holds the new code. So `rollout undo` does not roll back code.

Config is different: `app-config` and `postgres-credentials` are generated with a content hash, so a config change creates a new ConfigMap name and a new ReplicaSet. `rollout undo` does revert a config-only change, until the next `kubectl apply -k` re-applies what is in Git.

## Detect
1. Find recent changes:
   ```bash
   kubectl -n incident-investigator rollout history deploy/orders-api
   kubectl -n incident-investigator get rs -o wide | grep -v " 0 "
   git log --since="2 hours ago" --stat -- market-data-service order-service deploy/k8s
   ```
2. Compare metrics before and after the rollout: orders-api 5xx rate, `md_ticks_received_total` rate, `position_updater_ticks_total` by result, error logs.
3. Split by pod, not version: spans and metrics carry no `service.version`, only `service.name`. Metrics do carry `k8s_pod_name`, whose ReplicaSet hash tells old pods from new during a rollout.
   ```promql
   sum by (k8s_pod_name) (rate(http_server_request_duration_seconds_count{job="incident-investigator/orders-api",http_response_status_code=~"5.."}[5m]))
   ```

## Roll back
- Code: check out the last good commit for the service and redeploy. `make deploy` rebuilds both images and restarts all four apps.
  ```bash
  git revert <bad-commit>        # or: git checkout <good-commit> -- order-service
  make -C deploy/k8s deploy
  ```
- Config only: `kubectl -n incident-investigator rollout undo deploy/<app>` works immediately; revert the change in Git too, or the next apply brings it back.
- Schema: `schema.sql` only runs `CREATE ... IF NOT EXISTS` and there are no migrations, so a redeploy never rolls back a column change. Revert it by hand with `psql`.

## Verify
Metrics return to the pre-release baseline. Record the bad commit in the incident notes.
