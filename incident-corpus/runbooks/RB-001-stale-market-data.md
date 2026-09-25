---
id: RB-001
type: runbook
title: Stale market data / WebSocket feed down
services: [market-data-service, order-risk-service]
components: [exchange-websocket, redis, kafka]
alerts: [MarketDataStale, MarketDataWsDisconnected]
symptoms: [STALE_PRICE rejects, tick age rising, positions not re-marked]
severity: sev2
last_reviewed: 2026-09-25
---

# RB-001: Stale market data / WebSocket feed down

## Alert
- `MarketDataStale`: `max(md_tick_age_seconds) > 2` for 1 min.
- `MarketDataWsDisconnected`: `md_ws_connected == 0` for 30 s.

## Impact
Order-risk rejects orders with `STALE_PRICE` because Redis prices exceed the 2 s freshness limit. Positions stop re-marking, so displayed P&L freezes.

## Triage
1. Check connection state and reconnect activity:
   ```promql
   md_ws_connected
   increase(md_ws_reconnects_total[5m])
   rate(md_ticks_received_total[1m])
   ```
2. Decide which case applies:
   - **Connected but no ticks received**: silent disconnect or unsubscribed channel. The socket is open but dead.
   - **Disconnected, reconnects climbing**: exchange unreachable or rate limiting. See PM-001.
   - **Disconnected, no reconnect attempts**: reconnect loop has crashed or exited.
   - **Ticks received but not published**: go to RB-007.
3. Check logs:
   ```logql
   {service="market-data-service"} |= "websocket" | json | level=~"warn|error"
   ```
   Look for `ping timeout`, `429`, `subscription rejected`, or a traceback ending the reconnect task.
4. Check if only some symbols are stale: `md_tick_age_seconds > 2` by `symbol`. Illiquid strikes can go quiet legitimately; the alert only matters if liquid contracts (near-dated ATM) are stale.

## Mitigation
- Silent disconnect or dead reconnect loop: restart the pod.
  ```bash
  kubectl -n trading rollout restart deploy/market-data-service
  ```
- Rate limited (HTTP 429 on connect): do **not** restart repeatedly; each restart adds connection attempts. Wait for the backoff window and confirm exponential backoff with jitter is active.
- Exchange outage: switch to replay mode only in non-production test environments. In the desk, leave STALE_PRICE rejects in place; they are the correct safe behaviour.

## Verify
`md_tick_age_seconds` below 1 s for liquid symbols and `order_rejects_total{reason="STALE_PRICE"}` rate back near zero.

## Escalate
If the exchange status page reports an outage, record the window and close as external.
