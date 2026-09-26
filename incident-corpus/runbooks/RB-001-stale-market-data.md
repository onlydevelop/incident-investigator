---
id: RB-001
type: runbook
title: Stale market data / WebSocket feed down
services: [market-data-service, order-service]
components: [delta-ticker, delta-websocket, redis, kafka]
alerts: [MarketDataStale, MarketDataTickerSilent, MarketDataTickerAbsent]
symptoms: [current_price not moving, positions stuck pending, tick age rising, /tickers returns 404]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-001: Stale market data / WebSocket feed down

## Alert
- `MarketDataStale`: `max(md_tick_age_seconds) > 30` for 2 min.
- `MarketDataTickerSilent`: `sum(rate(md_ticks_received_total[2m])) == 0` for 2 min while symbols are subscribed.
- `MarketDataTickerAbsent`: `absent(md_ws_connected)` for 2 min (the ticker isn't exporting at all).

## Impact
position-updater gets no ticks, so `current_price` freezes and new positions stay `pending`. symbols-api's `/tickers/{symbol}` returns 404 `No ticker for ... in the last 10s` once the 10 s cache TTL runs out. Nothing is rejected: orders-api never reads prices, so creating positions still works.

## Triage
1. Check connection state, tick rate and restarts:
   ```promql
   md_ws_connected
   sum(rate(md_ticks_received_total[1m]))
   max by (symbol) (md_tick_age_seconds)
   increase(kube_pod_container_status_restarts_total{container="delta-ticker"}[30m])
   ```
2. Decide which case applies:
   - **Connected but no ticks received**: a half-open socket. The client sends no pings, so a dead TCP connection is never noticed, and delta-ticker has no liveness probe to restart it. See PM-003.
   - **Metrics absent, pod restarting**: the socket closed. `DeltaTickerClient.run()` has no reconnect loop, so the process exits and Kubernetes restarts it with a growing back-off. See PM-001.
   - **Only some symbols stale**: those contracts expired or were never listed. The symbols API accepts any well-formed symbol, and an expired one simply stops ticking.
   - **`Loaded symbols from ticker:symbols: []` at startup**: Redis was unreachable when the ticker started, or the set really is empty. See PM-005 and RB-006.
3. Check logs:
   ```logql
   {service_name="delta-ticker"} | json | event=~"ws_closed|ws_error|symbols_read_failed|no_symbols|ws_message"
   ```
   A `subscriptions` message containing `"error"` means Delta rejected a symbol.
4. Check expiry: the last six digits of a symbol are its expiry date (`DDMMYY`). `C-BTC-80000-091026` stops ticking after 9 Oct 2026.

## Mitigation
- Half-open socket or exited process: restart the ticker.
  ```bash
  kubectl -n incident-investigator rollout restart deploy/delta-ticker
  ```
- CrashLoopBackOff: the back-off grows to 5 min between attempts. Deleting the pod resets it; fix the cause first or it loops again.
- Expired symbols: remove them with `DELETE /symbols/{symbol}`, but only after checking that no open or pending position uses them (PM-002).
- Never scale delta-ticker above 1 replica: every tick would be published to Kafka twice.

## Verify
`md_ws_connected` is 1, tick rate above zero, and `max(md_tick_age_seconds)` below 10 s for every subscribed symbol.

## Escalate
If Delta's status page reports an outage or maintenance, record the window and close as external.
