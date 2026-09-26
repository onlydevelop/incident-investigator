---
id: RB-012
type: runbook
title: Clock skew and negative tick age
services: [market-data-service, order-service]
components: [kubernetes-node, rancher-desktop-vm, ntp]
alerts: [NodeClockUnsynced, TickAgeNegative]
symptoms: [negative tick age, every symbol looks stale with a healthy feed, positions stuck pending, stale alerts late]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-012: Clock skew and negative tick age

## How it looks
Tick age is local time minus Delta's exchange timestamp, in both `md_tick_age_seconds` (delta-ticker) and `position_updater_tick_age_seconds`. The cluster is a single k3s node inside the Rancher Desktop VM, so every pod shares that VM's clock, and it can drift after the host sleeps.
- **Clock ahead**: every tick looks old, so all symbols look stale even though the feed is healthy. Positions get their `time` from Postgres `now()`, which is later than the ticks arriving, so new positions stay pending until real time catches up (RB-008).
- **Clock behind**: tick age goes negative, so staleness checks silently pass, hiding a real feed problem. `position_updater_tick_age_seconds` clamps negatives to 0, which hides the skew as well.

## Triage
1. Is the node's clock synchronised?
   ```promql
   node_timex_offset_seconds
   node_timex_sync_status
   ```
   Offset beyond ±0.5 s or sync status 0 is a problem.
2. Look for negative ages: `min(md_tick_age_seconds) < 0`. A value like -455 on every symbol is skew, not a feed issue.
3. Compare the VM with the host:
   ```bash
   date -u; rdctl shell date -u
   ```
4. On a Linux node, check time sync directly:
   ```bash
   timedatectl status
   chronyc tracking
   ```
   A VM that was paused while the host slept resumes with its old time and stays wrong until something steps the clock.

## Mitigation
- Step the VM clock: `rdctl shell sudo chronyc makestep` if chrony runs in the VM; otherwise restart Rancher Desktop (this restarts every pod).
- Pending positions open on the first tick after the clock is correct; nothing needs replaying.
- Longer-term: compare exchange time with receive time on the same host, alert on negative ages, and stop clamping them in position-updater.

## Verify
Offset near zero, `node_timex_sync_status` 1, no negative tick ages, and pending positions opening normally.
