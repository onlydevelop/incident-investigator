---
id: PM-001
type: postmortem
title: WebSocket close exits delta-ticker into CrashLoopBackOff
basis: scenario
duration_minutes: 34
severity: sev2
services: [market-data-service, order-service]
root_cause_category: retry-policy
related_runbooks: [RB-001, RB-009]
---

# PM-001: WebSocket close exits delta-ticker into CrashLoopBackOff

## Summary
Delta closed our websocket during an exchange maintenance window. `DeltaTickerClient.run()` returns when the socket closes, and `main()` then flushes the producer and exits with code 0. Kubernetes restarted the container, Delta closed the new connection within seconds, and after a few rounds the restart back-off reached 5 minutes. Ticks arrived only in short bursts between long gaps.

## Impact
- 34 minutes in which position prices updated only in bursts of a few seconds.
- Positions created in the window stayed `pending` for up to 5 minutes.
- symbols-api's `/tickers` returned 404 most of the time.

## Timeline (UTC)
- 10:02 Delta starts maintenance and closes the socket; `ws_closed` logged; the process exits.
- 10:02 Pod shows `Completed`, then restarts. `md_tick_age_seconds` series disappear, because each new process starts with no ticks recorded.
- 10:03 to 10:30 Seven restarts; each connects, subscribes, and is closed within seconds. Back-off grows to 5 min.
- 10:03 `MarketDataStale` never fires: there were no `md_tick_age_seconds` series left for it to evaluate.
- 10:18 A user notices `current_price_time` several minutes old.
- 10:21 On-call sees CrashLoopBackOff with exit code 0 and no traceback, which reads like a clean shutdown.
- 10:36 Maintenance ends; the next restart holds and ticks resume.

## Root cause
The ticker has no reconnect loop. Restarting the whole process is its only retry policy, so Kubernetes's exponential back-off (capped at 5 minutes) decided how long each gap lasted.

## What went wrong
- The staleness alert keyed on `md_tick_age_seconds` went silent: each restart starts with an empty `_latest_tick`, so the series vanish instead of growing.
- Exit code 0 hid the failure.

## Action items
- Reconnect inside the process with exponential backoff and jitter (1 s to 60 s cap). Open.
- Alert on `absent(md_ws_connected)` and on a zero tick rate, not only on tick age. Open.
- RB-001 lists "metrics absent, pod restarting" as its own case. Done.
