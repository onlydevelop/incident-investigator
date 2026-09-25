---
id: PM-008
type: postmortem
title: Node clock drift after reboot rejects orders
date: 2026-09-11
duration_minutes: 33
severity: sev2
services: [order-risk-service, market-data-service]
root_cause_category: infrastructure
related_runbooks: [RB-012, RB-001, RB-004]
---

# PM-008: Node clock drift after reboot rejects orders

## Summary
A k3s worker node rebooted after a power interruption. The node has no battery-backed hardware clock, so it started with a time 4 minutes ahead. A firewall change the week before had blocked outbound NTP, so the clock never corrected. The order-risk pod on that node saw every price as 4 minutes old and rejected its orders with `STALE_PRICE`.

## Impact
- 33 minutes; about 50% of orders rejected (all orders routed to the pod on the affected node).
- Market-data on healthy nodes was fine; the feed was never actually stale.

## Timeline (UTC)
- 18:02 Node `k3s-worker-2` reboots.
- 18:04 order-risk pod rescheduled to it; `STALE_PRICE` rejects start.
- 18:06 `OrderRejectRateHigh` fires.
- 18:08 On-call follows RB-001: checks WebSocket and tick rate. Both healthy, which is confusing.
- 18:20 Rejects split by pod show only one pod rejecting.
- 18:27 `node_timex_offset_seconds` on `k3s-worker-2` is +241 s; `node_timex_sync_status` 0.
- 18:30 Node cordoned; pod moved.
- 18:35 Rejects normal. NTP rule fixed at 19:10; node uncordoned.

## Root cause
Unsynchronized node clock combined with a freshness check that compares timestamps across hosts.

## What went wrong
- RB-001 was the natural first runbook for `STALE_PRICE` but did not mention clock skew.

## Action items
- Alert on node clock offset above 0.5 s and on sync status 0. Done.
- Pods refuse readiness if local clock offset exceeds 1 s. Done.
- Created RB-012; RB-004 lists it next to RB-001 for `STALE_PRICE`. Done.
