---
id: RB-008
type: runbook
title: Positions stuck in pending
services: [order-service, market-data-service]
components: [position-updater, delta-ticker, symbols-api, kafka]
alerts: [PositionsPendingTooLong]
symptoms: [status stays pending, entry_price null, positions_opened_total flat]
severity: sev3
last_reviewed: 2026-09-26
---

# RB-008: Positions stuck in pending

## Alert
`PositionsPendingTooLong`: a position has been `pending` for more than 2 min. There is no metric for this yet; check with SQL:
```sql
SELECT id, symbol, side, now() - time AS waiting FROM positions WHERE status = 'pending' ORDER BY time;
```

## How a position opens
position-updater opens a pending position on the first tick for its symbol whose exchange time is at or after the position's `time`, and that carries the price it needs: the ask for a buy, the bid for a sell. A newly subscribed symbol can take up to 30 s to start ticking. A position never opens at a price older than its creation time, so waiting is safe; the entry will be the first valid tick.

## Triage
Follow the tick from Delta to Postgres and stop at the first step that fails.
1. **Is the symbol subscribed?** `curl http://market-data.localhost/symbols/<symbol>`. A 404 means the subscription was removed after the position was created (PM-002).
2. **Is the ticker receiving it?** `md_tick_age_seconds{symbol="<symbol>"}`. No series means no tick since the ticker started: the symbol is new, expired, or not listed on Delta. Check `ws_message` logs for a subscription error.
3. **Does the tick have the needed side?** `curl http://market-data.localhost/tickers/<symbol>`. `best_ask: null` keeps buys waiting; `best_bid: null` keeps sells waiting. Deep out-of-the-money options often have no bid.
4. **Is it reaching Kafka and the updater?** `md_ticks_published_total` rising (else RB-007), `position_updater_ticks_total{result="applied"}` rising (else RB-002).
5. **Is the clock right?** The position's `time` comes from Postgres `now()`. If the node clock is ahead of exchange time, no tick qualifies until real time catches up (RB-012).

## Mitigation
- Unsubscribed: `curl -X PUT http://market-data.localhost/symbols/<symbol>`; it opens on a tick within about 30 s.
- Expired or never listed: the position will never open. Delete it with `DELETE /positions/{id}`.
- One-sided book: nothing to fix; it opens when the missing side is quoted.
- Kafka, updater or clock: follow the linked runbook.

## Verify
The position shows `status: open` with `entry_price` and `entry_time` set, and `positions_opened_total` increased.
