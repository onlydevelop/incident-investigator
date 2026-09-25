---
id: RB-012
type: runbook
title: Clock skew and negative tick age
services: [market-data-service, order-risk-service]
components: [kubernetes-node, ntp]
alerts: [NodeClockSkew, TickAgeNegative]
symptoms: [STALE_PRICE rejects with fresh feed, negative tick age, trace timings inconsistent]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-012: Clock skew and negative tick age

## How it looks
Tick age is computed as local time minus the exchange timestamp. If a node's clock is wrong:
- **Clock ahead**: every tick looks old, so orders are rejected as `STALE_PRICE` even though the feed is healthy.
- **Clock behind**: tick age goes negative and staleness checks silently pass, hiding a real feed problem.

Symptoms often affect only pods on one node.

## Triage
1. Check skew per node:
   ```promql
   node_timex_offset_seconds
   node_timex_sync_status
   ```
   Offset beyond ±0.5 s or sync status 0 is a problem.
2. Check whether stale rejects correlate with a node:
   ```promql
   sum by (node) (rate(order_rejects_total{reason="STALE_PRICE"}[5m]))
   ```
   (join pod to node via `kube_pod_info`).
3. Look for `md_tick_age_seconds < 0` on any pod.
4. On the node, check time sync:
   ```bash
   timedatectl status
   chronyc tracking
   ```
   Nodes without a hardware clock start with a wrong time after reboot until NTP syncs; if NTP is blocked, they stay wrong.

## Mitigation
- Cordon the node and move pods off it: `kubectl cordon <node> && kubectl -n trading delete pod <pods on node>`.
- Fix NTP on the node (reachability, chrony config), then uncordon.
- Longer-term: compute freshness from receive time on the same host as the check, and alert on negative ages.

## Verify
Offset near zero, no negative tick ages, stale rejects back to baseline.
