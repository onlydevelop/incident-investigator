---
id: PM-002
type: postmortem
title: Clearing the symbol list freezes open positions
basis: scenario
duration_minutes: 190
severity: sev2
services: [market-data-service, order-service]
root_cause_category: config-change
related_runbooks: [RB-006, RB-008]
---

# PM-002: Clearing the symbol list freezes open positions

## Summary
After a demo, an operator ran `curl -X DELETE http://market-data.localhost/symbols` to stop streaming the demo's symbols, assuming open positions kept their own subscriptions. They don't: subscriptions are a separate Redis set, and the ticker unsubscribed from everything on its next refresh. Open positions kept their last `current_price`, and nothing reported an error.

## Impact
- 190 minutes of frozen prices on 6 open positions.
- Positions created during the window worked, because POST /positions re-subscribes its symbol, which made the problem look intermittent.

## Timeline (UTC)
- 14:05 `DELETE /symbols` returns 204.
- 14:05 Within 30 s the ticker logs `Symbols changed: +[] -[...]`. The removed symbols' `md_tick_age_seconds` series disappear.
- 14:06 Tick rate drops to zero and consumer lag is zero. Dashboards look quiet, not red.
- 15:40 A new position in `C-BTC-84000-091026` opens normally, re-subscribing that one symbol.
- 17:10 A user notices `current_price_time` hours old on the other positions.
- 17:14 `SMEMBERS ticker:symbols` returns one symbol; `positions` has seven distinct symbols.
- 17:15 Symbols re-subscribed from the positions table; prices update within 30 s.

## Root cause
Deleting a position doesn't unsubscribe its symbol, but unsubscribing doesn't check positions either. The symbols API has no idea which symbols positions depend on.

## What went wrong
- No signal covers "open position whose symbol isn't subscribed".
- Removing a symbol also removes its `md_tick_age_seconds` series, so the staleness alert had nothing to fire on.

## Action items
- symbols-api refuses to remove a symbol that has open or pending positions, or orders-api re-subscribes them on a schedule. Open.
- Export the age of each open position's `current_price_time` and alert above 5 minutes. Open.
- RB-006 and RB-008 compare `SMEMBERS ticker:symbols` with the symbols in `positions`. Done.
