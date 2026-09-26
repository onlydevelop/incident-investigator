---
id: PM-003
type: postmortem
title: Half-open websocket keeps the ticker connected but silent
basis: scenario
duration_minutes: 52
severity: sev2
services: [market-data-service, order-service]
root_cause_category: missing-heartbeat
related_runbooks: [RB-001, RB-011]
---

# PM-003: Half-open websocket keeps the ticker connected but silent

## Summary
The host switched networks (a VPN connected), and the ticker's TCP connection to Delta died without a close or reset reaching the pod. `run_forever()` is called without `ping_interval`, so the client never probed the socket. `md_ws_connected` stayed at 1 and no error was logged, while no ticks arrived for 52 minutes.

## Impact
- 52 minutes of frozen `current_price` on every open position.
- New positions stayed `pending` for the whole window.

## Timeline (UTC)
- 11:14 Host network changes; the existing websocket silently stops delivering.
- 11:14 `md_ticks_received_total` goes flat; `md_ws_connected` stays 1.
- 11:16 `md_tick_age_seconds` passes 30 s and keeps climbing, because the process is alive. `MarketDataStale` fires, but Alertmanager is disabled, so it only shows on Prometheus's `/alerts` page.
- 11:52 A user reports that prices haven't moved.
- 11:58 On-call follows RB-001; the socket reports connected, which at first suggests a quiet market.
- 12:02 Logs show `Socket opened` hours earlier and no `ws_closed` since.
- 12:05 `kubectl rollout restart deploy/delta-ticker`; ticks resume within 2 s.
- 12:06 Positions update.

## Root cause
No application-level heartbeat on the websocket, and no liveness probe on delta-ticker, so a half-open connection could last indefinitely.

## What went well
- `md_tick_age_seconds` kept growing because the process stayed alive; the metric did its job. The gap was notification.

## Action items
- Call `run_forever(ping_interval=20, ping_timeout=10)` so a dead socket closes, together with a reconnect loop (PM-001). Open.
- Route alerts somewhere a person sees them: enable Alertmanager. Open.
- RB-001 treats "connected but no ticks received" as its own case. Done.
