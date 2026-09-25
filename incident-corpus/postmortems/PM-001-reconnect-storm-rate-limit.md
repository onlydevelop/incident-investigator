---
id: PM-001
type: postmortem
title: Reconnect storm hits exchange rate limit
date: 2026-05-14
duration_minutes: 38
severity: sev2
services: [market-data-service, order-risk-service]
root_cause_category: retry-policy
related_runbooks: [RB-001, RB-009]
---

# PM-001: Reconnect storm hits exchange rate limit

## Summary
A brief network blip dropped the exchange WebSocket. The reconnect loop retried immediately with no backoff, from 2 replicas, about 20 times per second. The exchange rate-limited our IP with HTTP 429 for 30 minutes, so prices went stale and orders were rejected long after the network recovered.

## Impact
- 38 minutes of stale prices for all contracts.
- 100% of orders rejected with `STALE_PRICE` for 34 minutes.
- Position marks frozen; P&L display wrong for the whole window.

## Timeline (UTC)
- 09:12 Network blip; `md_ws_connected` drops to 0.
- 09:12 `md_ws_reconnects_total` climbs about 1,200 per minute.
- 09:13 Exchange starts returning 429 on connect.
- 09:14 `MarketDataStale` fires.
- 09:20 On-call restarts market-data pods, twice. Each restart resets the loop and adds more attempts.
- 09:41 On-call notices 429s in logs, scales market-data to 0.
- 09:48 Rate-limit window expires; one replica started; ticks resume.
- 09:50 Stale alert clears.

## Root cause
The reconnect loop had a fixed 50 ms delay and no cap. Both replicas held their own connection, doubling attempts.

## What went wrong
- Restarting pods, the usual fix for a dead socket, made this incident worse.
- The runbook did not distinguish "disconnected with retries" from "disconnected with no retries".

## Action items
- Exponential backoff with jitter (1 s to 60 s cap). Done.
- Only the leader replica holds the WebSocket; followers stand by. Done.
- RB-001 updated with the 429 case and a "do not restart" warning. Done.
- Alert on `increase(md_ws_reconnects_total[1m]) > 10`. Done.
