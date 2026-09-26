---
id: PM-008
type: postmortem
title: VM clock behind after host sleep delays stale-feed detection
basis: scenario
duration_minutes: 54
severity: sev2
services: [market-data-service, order-service]
root_cause_category: infrastructure
related_runbooks: [RB-012, RB-001]
---

# PM-008: VM clock behind after host sleep delays stale-feed detection

## Summary
The Mac hosting Rancher Desktop slept overnight. On wake, the VM resumed with its clock 455 s behind, and nothing stepped it back. Every tick's age read about -455 s. Later that morning Delta's feed stalled, and `md_tick_age_seconds` had to climb from -455 s before it crossed the 30 s stale threshold, so the alert fired almost 8 minutes late.

## Impact
- 54 minutes with the VM clock wrong; for 18 minutes of it, stale prices on every open position.
- About 8 minutes of that went undetected because of the skew.
- `position_updater_tick_age_seconds` showed perfect freshness throughout, because it clamps negative ages to 0.

## Timeline (UTC)
- 07:55 Host wakes. `node_timex_sync_status` is 0.
- 08:02 `md_tick_age_seconds` reads about -455 s for every symbol. Nothing alerts on negative values.
- 08:31 Delta's feed stalls; the tick rate drops to zero.
- 08:39 `md_tick_age_seconds` crosses 30 s and `MarketDataStale` fires, about 8 minutes after the stall.
- 08:44 On-call compares `date -u` on the host with `rdctl shell date -u`: 7 min 35 s apart.
- 08:47 Rancher Desktop restarted, which resets the VM clock and restarts every pod, including the stalled ticker.
- 08:49 `node_timex_offset_seconds` near zero, ticks flowing, `MarketDataStale` clears.

## Root cause
The VM's clock was not disciplined after the host slept, and freshness is computed across two clocks: Delta's exchange timestamp and the VM's local time.

## What went wrong
- Negative ages looked like "very fresh" rather than "wrong".
- Clamping in position-updater hid the skew in the one metric that measures end-to-end freshness.

## Action items
- Alert on `min(md_tick_age_seconds) < -1` and on `node_timex_sync_status == 0`. Open.
- Record negative ages in position-updater instead of clamping them to 0. Open.
- RB-012 covers the "clock behind" case and the host-versus-VM check. Done.
